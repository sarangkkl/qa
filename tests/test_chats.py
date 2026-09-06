"""Chat persistence, and the router recording what it did as well as what it said."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeChannel

from nkqa import chats as chats_mod
from nkqa import workspace as workspace_mod
from nkqa.chats import Turn
from nkqa.config import Config
from nkqa.hitl import HumanInTheLoop
from nkqa.shell import agent, session
from nkqa.shell.commands import ShellContext
from nkqa.workspace import Workspace


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
	return workspace_mod.create(tmp_path)


def test_new_chat_is_saved_and_listed(ws: Workspace) -> None:
	chat = chats_mod.new_chat(ws)
	assert chats_mod.load(ws, chat.id) is not None
	assert [c['id'] for c in chats_mod.list_chats(ws)] == [chat.id]


def test_title_comes_from_the_first_thing_the_human_said(ws: Workspace) -> None:
	chat = chats_mod.new_chat(ws)
	chat.add(Turn(role='user', text='test the coupon flow'))
	chat.add(Turn(role='user', text='and the refund one'))
	assert chat.title == 'test the coupon flow'


def test_turns_round_trip_including_the_command_that_ran(ws: Workspace) -> None:
	chat = chats_mod.new_chat(ws)
	chat.add(Turn(role='user', text='what scenarios do I have?'))
	chat.add(Turn(role='assistant', text='Listing them.', command='scenarios', args={'a': 'b'}, exit=0))
	chats_mod.save(ws, chat)

	again = chats_mod.load(ws, chat.id)
	assert again is not None
	assert [t.role for t in again.turns] == ['user', 'assistant']
	assert again.turns[1].command == 'scenarios' and again.turns[1].exit == 0
	assert again.turns[1].args == {'a': 'b'}


def test_context_is_capped(ws: Workspace) -> None:
	chat = chats_mod.new_chat(ws)
	for i in range(60):
		chat.add(Turn(role='user', text=f'message {i}'))
	assert len(chat.recent()) == chats_mod.CONTEXT_TURNS
	assert chat.recent()[-1].text == 'message 59'


def test_corrupt_and_missing_chats_are_survivable(ws: Workspace) -> None:
	assert chats_mod.load(ws, 'nope') is None
	(ws.chats_dir / 'broken.json').write_text('{not json')
	assert chats_mod.load(ws, 'broken') is None
	assert chats_mod.list_chats(ws) == []  # a broken file is skipped, not fatal


def test_delete(ws: Workspace) -> None:
	chat = chats_mod.new_chat(ws)
	assert chats_mod.delete(ws, chat.id) is True
	assert chats_mod.delete(ws, chat.id) is False


# --- the router writing into a chat -----------------------------------------


class StubLLM:
	def __init__(self, decisions: list[agent.ChatDecision]):
		self.decisions = decisions
		self.calls = 0
		self.seen: list[Any] = []

	async def ainvoke(self, messages: Any, output_format: Any = None) -> Any:
		self.seen.append(messages)
		decision = self.decisions[min(self.calls, len(self.decisions) - 1)]
		self.calls += 1

		class R:
			completion = decision

		return R()


def _stub_resolve(llm: object) -> object:
	def fake(cfg: object, role: str, override: str | None = None) -> object:
		return llm

	return fake


def ctx_with_chat(
	ws: Workspace, monkeypatch: pytest.MonkeyPatch, decisions: list[agent.ChatDecision]
) -> tuple[ShellContext, StubLLM]:
	llm = StubLLM(decisions)
	monkeypatch.setattr(agent, 'resolve_llm', _stub_resolve(llm))
	ctx = ShellContext(
		ws=ws,
		config=Config(),
		hitl=HumanInTheLoop(ws.permissions_file, FakeChannel()),
		channel=FakeChannel(),
		chat=chats_mod.new_chat(ws),
	)
	return ctx, llm


def test_router_records_the_command_it_ran(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
	ctx, _ = ctx_with_chat(
		ws,
		monkeypatch,
		[
			agent.ChatDecision(reply='Listing your scenarios.', command='scenarios'),
			agent.ChatDecision(reply='Nothing else needed.', command=''),
		],
	)
	assert asyncio.run(session.handle(ctx, 'what scenarios do I have?')) == 0

	assert ctx.chat is not None
	stored = chats_mod.load(ws, ctx.chat.id)
	assert stored is not None
	assert stored.turns[0].role == 'user' and stored.turns[0].text == 'what scenarios do I have?'
	ran = next(t for t in stored.turns if t.command)
	assert ran.command == 'scenarios' and ran.exit == 0
	assert stored.title == 'what scenarios do I have?'


def test_a_refused_approval_is_recorded_too(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
	ctx, _ = ctx_with_chat(
		ws, monkeypatch, [agent.ChatDecision(reply='Approving it.', command='approve', args={'id': 'auth/login'})]
	)
	assert asyncio.run(session.handle(ctx, 'approve the login scenario')) == 1

	assert ctx.chat is not None
	stored = chats_mod.load(ws, ctx.chat.id)
	assert stored is not None
	assert '/approve auth/login' in stored.turns[-1].text  # the refusal is in the transcript
	assert not any(t.command == 'approve' for t in stored.turns)  # and it never ran


def test_earlier_turns_are_replayed_into_the_next_prompt(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
	ctx, llm = ctx_with_chat(ws, monkeypatch, [agent.ChatDecision(reply='ok', command='')])
	asyncio.run(session.handle(ctx, 'first message'))
	asyncio.run(session.handle(ctx, 'second message'))

	replayed = [str(getattr(m, 'content', '')) for m in llm.seen[-1]]
	assert any('first message' in c for c in replayed)  # the chat has memory across turns


def test_without_a_chat_the_router_still_works(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
	"""The terminal shell may run chatless; recording must be optional, never required."""
	ctx, _ = ctx_with_chat(ws, monkeypatch, [agent.ChatDecision(reply='ok', command='')])
	ctx.chat = None
	assert asyncio.run(session.handle(ctx, 'hello')) == 0
	assert chats_mod.list_chats(ws) == [] or all(c['turns'] == 0 for c in chats_mod.list_chats(ws))


def test_no_credential_value_can_reach_a_transcript(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
	"""Belt and braces: the router only ever records its own reply and the command name."""
	ctx, _ = ctx_with_chat(ws, monkeypatch, [agent.ChatDecision(reply='Running it.', command='scenarios')])
	ctx.hitl.store_secret('password', 'hunter2')
	asyncio.run(session.handle(ctx, 'run the login scenario'))

	assert ctx.chat is not None
	assert 'hunter2' not in json.dumps(json.loads((ws.chats_dir / f'{ctx.chat.id}.json').read_text()))
