import asyncio
from pathlib import Path
from typing import Any

import pytest

from nkqa import actions, workspace
from nkqa.config import Config
from nkqa.hitl import HumanInTheLoop
from nkqa.shell import agent, render, session
from nkqa.shell.commands import REGISTRY, ShellContext, agent_commands, parse_slash


def make_ctx(tmp_path: Path) -> ShellContext:
	ws = workspace.create(tmp_path)
	return ShellContext(ws=ws, config=Config(), hitl=HumanInTheLoop(ws.permissions_file))


def test_approve_is_never_an_agent_tool() -> None:
	names = [c.name for c in agent_commands()]
	assert 'approve' not in names  # the gate the whole product rests on
	assert REGISTRY['approve'].human_only is True
	assert {'plan', 'run', 'learn', 'revise'} <= set(names)
	assert not {'help', 'exit', 'forget'} & set(names)
	assert not any(line.startswith('- approve:') for line in agent.describe_commands().splitlines())


def test_parse_slash_positional_and_flags() -> None:
	cmd, args, err = parse_slash('/run checkout/coupon --model smart')
	assert err == '' and cmd is not None and cmd.name == 'run'
	assert args == {'id': 'checkout/coupon', 'model': 'smart'}

	cmd, args, err = parse_slash('/plan test the checkout flow --force')
	assert cmd is not None and args['ask'] == 'test the checkout flow' and args['force'] is True

	cmd, args, err = parse_slash('/revise auth/login "make step 3 stricter"')
	assert cmd is not None and args == {'id': 'auth/login', 'instruction': 'make step 3 stricter'}

	cmd, args, err = parse_slash('/file-bug demo--123 --step 2')
	assert cmd is not None and args == {'run': 'demo--123', 'step': 2}


def test_parse_slash_errors() -> None:
	assert parse_slash('/nope')[2].startswith('Unknown command')
	assert 'needs' in parse_slash('/revise')[2]  # required params missing
	assert parse_slash('/run --bogus x')[2].endswith('no option --bogus')
	assert parse_slash('/plna')[2].startswith('Unknown command')


def test_help_and_exit_and_forget(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
	ctx = make_ctx(tmp_path)
	assert asyncio.run(session.handle(ctx, '/help')) == 0
	out = capsys.readouterr().out
	assert '/plan' in out and 'human only' in out

	ctx.hitl.secrets['password'] = 'hunter2'
	ctx.hitl.session_grants.add('delete-user')
	assert asyncio.run(session.handle(ctx, '/forget')) == 0
	assert ctx.hitl.secrets == {} and ctx.hitl.session_grants == set()

	assert asyncio.run(session.handle(ctx, '/exit')) == 0
	assert ctx.running is False


def test_slash_dispatch_reaches_the_action(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
	ctx = make_ctx(tmp_path)
	assert asyncio.run(session.handle(ctx, '/scenarios')) == 0
	assert 'No scenarios yet' in capsys.readouterr().out


def test_banner_shows_workspace_state(tmp_path: Path) -> None:
	ctx = make_ctx(tmp_path)
	text = render.banner(ctx.ws, ctx.config)
	assert 'appmap:' in text and 'scenarios:' in text and 'chat=' in text


def test_start_outside_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.chdir(tmp_path)
	assert asyncio.run(session.start()) == 2


class StubLLM:
	def __init__(self, decisions: list[agent.ChatDecision]):
		self.decisions = decisions
		self.calls = 0

	async def ainvoke(self, messages: Any, output_format: Any = None) -> Any:
		decision = self.decisions[min(self.calls, len(self.decisions) - 1)]
		self.calls += 1

		class R:
			completion = decision

		return R()


def stub_chat(monkeypatch: pytest.MonkeyPatch, decisions: list[agent.ChatDecision]) -> StubLLM:
	llm = StubLLM(decisions)

	def fake(cfg: Config, role: str, override: str | None = None) -> StubLLM:
		return llm

	monkeypatch.setattr(agent, 'resolve_llm', fake)
	return llm


def test_agent_runs_a_command(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
	ctx = make_ctx(tmp_path)
	stub_chat(
		monkeypatch,
		[
			agent.ChatDecision(reply='Listing your scenarios.', command='scenarios'),
			agent.ChatDecision(reply='Nothing else needed.', command=''),
		],
	)
	assert asyncio.run(session.handle(ctx, 'what scenarios do I have?')) == 0
	out = capsys.readouterr().out
	assert 'Listing your scenarios.' in out and 'No scenarios yet' in out


def test_agent_cannot_approve(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
	ctx = make_ctx(tmp_path)
	stub_chat(monkeypatch, [agent.ChatDecision(reply='Approving it.', command='approve', args={'id': 'auth/login'})])
	assert asyncio.run(session.handle(ctx, 'approve the login scenario')) == 1
	assert '/approve auth/login' in capsys.readouterr().out  # refused, hint given


def test_agent_coerces_arg_types() -> None:
	cmd = REGISTRY['file-bug']
	assert agent.coerce(cmd, {'run': 'r--1', 'step': '2', 'bogus': 'x'}) == {'run': 'r--1', 'step': 2}
	assert agent.coerce(REGISTRY['replay'], {'all': 'true'}) == {'all': True}


def test_registry_covers_the_cli_surface() -> None:
	from nkqa.cli import build_parser

	cli_commands = set(build_parser()._subparsers._group_actions[0].choices)  # type: ignore[union-attr]
	shell_only = {'help', 'exit', 'forget'}
	cli_only = {'init', 'version', 'chat'}
	assert set(REGISTRY) - shell_only == cli_commands - cli_only


def test_auth_guards(tmp_path: Path) -> None:
	ctx = make_ctx(tmp_path)
	assert asyncio.run(actions.auth(ctx.ws, config=ctx.config)) == 2  # nothing configured

	from nkqa.config import MCPServer

	ctx.config.mcp_servers = [MCPServer(name='jira', command='npx')]
	assert asyncio.run(actions.auth(ctx.ws, 'nope', config=ctx.config)) == 2  # unknown server
