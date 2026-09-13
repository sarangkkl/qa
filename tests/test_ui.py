"""The channel contract: terminal output is unchanged, and secrets never leave hitl."""

# browser-use boundary: its tool registry is partially untyped.
# pyright: reportUnknownMemberType=false

import asyncio
import json
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, cast

import pytest
from browser_use import Tools
from conftest import FakeChannel

from nkqa.hitl import HumanInTheLoop
from nkqa.ui import RULE, Ask, Event, TerminalChannel


def call(tools: Tools[None], action_name: str, **kwargs: object) -> str:
	"""Invoke one registered HITL tool the way the agent would."""
	fn = cast(Callable[..., Coroutine[Any, Any, object]], tools.registry.registry.actions[action_name].function)
	return str(asyncio.run(fn(**kwargs)))


def test_terminal_channel_prints_event_text(capsys: pytest.CaptureFixture[str]) -> None:
	ch = TerminalChannel()
	asyncio.run(ch.emit(Event('log', 'hello')))
	asyncio.run(ch.log())
	assert capsys.readouterr().out == 'hello\n\n'


def test_terminal_ask_shows_body_then_prompt(
	monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
	monkeypatch.setattr('builtins.input', lambda _prompt='': 'y')
	ch = TerminalChannel()
	assert asyncio.run(ch.confirm('Approve? [y/N]: ', body='the scenario')) is True
	assert capsys.readouterr().out == 'the scenario\n'  # prompt itself goes to input(), not stdout


def test_terminal_interrupt_is_framed(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
	monkeypatch.setattr('builtins.input', lambda _prompt='': 'sure')
	ch = TerminalChannel()
	assert asyncio.run(ch.ask(Ask('text', 'q: ', interrupt=True))) == 'sure'
	assert capsys.readouterr().out == f'\n{RULE}\n{RULE}\n'


def test_credential_value_never_reaches_the_channel(tmp_path: Path) -> None:
	"""The security invariant, made executable: the channel carries the prompt, not the answer."""
	ch = FakeChannel(['hunter2'])
	hitl = HumanInTheLoop(tmp_path / 'perms.json', ch)
	call(hitl.build_tools(), 'ask_credential', name='Password')

	assert hitl.secrets == {'password': 'hunter2'}  # held in memory, where the Agent reads it
	assert 'hunter2' not in ch.out
	assert 'hunter2' not in json.dumps([e.data for e in ch.events])
	assert 'hunter2' not in json.dumps([a.__dict__ for a in ch.asks])
	assert not (tmp_path / 'perms.json').exists()  # nothing about a credential is persisted


def test_credential_ask_is_marked_secret_and_interrupting(tmp_path: Path) -> None:
	ch = FakeChannel(['hunter2'])
	hitl = HumanInTheLoop(tmp_path / 'perms.json', ch)
	call(hitl.build_tools(), 'ask_credential', name='password')

	ask = ch.asks[0]
	assert ask.kind == 'secret' and ask.key == 'password' and ask.interrupt is True


def test_permission_choice_paths(tmp_path: Path) -> None:
	perms = tmp_path / 'perms.json'

	granted_once = FakeChannel(['y'])
	hitl = HumanInTheLoop(perms, granted_once)
	result = call(hitl.build_tools(), 'request_permission', permission_key='del-user', description='delete')
	assert 'granted once' in result
	assert not perms.exists()  # allow-once is never persisted

	session = FakeChannel(['s'])
	hitl = HumanInTheLoop(perms, session)
	tools = hitl.build_tools()
	call(tools, 'request_permission', permission_key='del-user', description='delete')
	assert hitl.session_grants == {'del-user'} and not perms.exists()

	always = FakeChannel(['a'])
	hitl = HumanInTheLoop(perms, always)
	call(hitl.build_tools(), 'request_permission', permission_key='del-user', description='delete')
	assert json.loads(perms.read_text())['permissions'] == ['del-user']

	denied = FakeChannel(['n'])
	hitl = HumanInTheLoop(tmp_path / 'other.json', denied)
	assert 'DENIED' in call(hitl.build_tools(), 'request_permission', permission_key='wipe', description='wipe')


def test_the_gates_are_callable_without_browser_use(tmp_path: Path) -> None:
	"""`qa mcp` calls the decision methods directly; the actions above are only wrappers over them."""
	hitl = HumanInTheLoop(tmp_path / 'perms.json', FakeChannel(['n', 'hunter2']))
	denied = asyncio.run(hitl.decide_permission('Wipe-DB', 'wipe'))
	assert denied.ok is False and 'DENIED' in denied.content and 'wipe' in denied.memory

	released = asyncio.run(hitl.release_credential('Password'))
	assert released.ok is True and '<secret>password</secret>' in released.content
	assert 'hunter2' not in released.content and 'hunter2' not in released.memory
	assert asyncio.run(hitl.release_credential('password')).content.startswith('Already available')


def test_autonomy_allow_grants_without_asking_and_leaves_no_trace(tmp_path: Path) -> None:
	perms = tmp_path / 'perms.json'
	ch = FakeChannel()  # no scripted answers: asking at all would raise
	hitl = HumanInTheLoop(perms, ch)
	hitl.autonomy = 'allow'

	granted = call(hitl.build_tools(), 'request_permission', permission_key='del-user', description='delete')
	assert 'granted automatically' in granted
	assert ch.asks == []
	assert any(e.data.get('auto') == 'allow' for e in ch.events), 'an auto-grant must be visible in the log'
	# No residue anywhere: nothing persisted, nothing held for the session.
	assert not perms.exists() and hitl.session_grants == set()

	# ...so turning it back off resumes prompting for the very same key.
	hitl.autonomy = 'ask'
	hitl.channel = FakeChannel(['n'])
	assert 'DENIED' in call(hitl.build_tools(), 'request_permission', permission_key='del-user', description='delete')


def test_autonomy_refuse_beats_a_stored_always_grant(tmp_path: Path) -> None:
	""" "Refuse everything right now" has to win over a grant allowed weeks ago."""
	perms = tmp_path / 'perms.json'
	perms.write_text(json.dumps(['del-user']))
	ch = FakeChannel()
	hitl = HumanInTheLoop(perms, ch)
	hitl.autonomy = 'refuse'

	result = call(hitl.build_tools(), 'request_permission', permission_key='del-user', description='delete')
	assert 'DENIED' in result and 'not tested - permission denied' in result
	assert ch.asks == []


def test_autonomy_never_releases_a_credential(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	"""Autonomy governs `request_permission` only. Credentials keep their own human grant.

	The credential has to be genuinely *stored*, or the chain falls through to "ask the
	human" for the boring reason and the test proves nothing.
	"""
	from nkqa import workspace as workspace_mod
	from nkqa.vault import ENV_PREFIX, Vault

	ws = workspace_mod.create(tmp_path)
	ws.vault_file.write_text('credentials:\n  password:\n    description: login\n')
	monkeypatch.setenv(f'{ENV_PREFIX}PASSWORD', 'hunter2')
	vault = Vault(ws, discover=False)
	assert vault.get('password')[0] == 'hunter2', 'the value must really be stored, or this proves nothing'

	ch = FakeChannel(['n'])
	hitl = HumanInTheLoop(ws.permissions_file, ch, vault)
	hitl.autonomy = 'allow'

	result = call(hitl.build_tools(), 'ask_credential', name='password')
	assert [a.kind for a in ch.asks] == ['choice'], 'allow must not skip the credential release prompt'
	assert 'denied' in result.lower() and 'hunter2' not in result


def test_stored_grant_skips_the_prompt(tmp_path: Path) -> None:
	"""Also pins the legacy format: pre-vault workspaces stored a bare list."""
	perms = tmp_path / 'perms.json'
	perms.write_text(json.dumps(['del-user']))
	ch = FakeChannel()  # no scripted answers: asking at all would raise
	hitl = HumanInTheLoop(perms, ch)
	granted = call(hitl.build_tools(), 'request_permission', permission_key='del-user', description='delete')
	assert 'previously allowed' in granted
	assert ch.asks == []
