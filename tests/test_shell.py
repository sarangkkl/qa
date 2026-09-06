import asyncio
from pathlib import Path
from typing import Any, cast

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
	stub_chat(
		monkeypatch,
		[
			agent.ChatDecision(
				reply='Approving it.', command='approve', args=[agent.ChatArg(name='id', value='auth/login')]
			)
		],
	)
	assert asyncio.run(session.handle(ctx, 'approve the login scenario')) == 1
	assert '/approve auth/login' in capsys.readouterr().out  # refused, hint given


def test_agent_coerces_arg_types() -> None:
	cmd = REGISTRY['file-bug']
	assert agent.coerce(cmd, {'run': 'r--1', 'step': '2', 'bogus': 'x'}) == {'run': 'r--1', 'step': 2}
	assert agent.coerce(REGISTRY['replay'], {'all': 'true'}) == {'all': True}


def test_registry_covers_the_cli_surface() -> None:
	from nkqa.cli import build_parser

	parser = build_parser()
	cli_commands = set(parser._subparsers._group_actions[0].choices)  # type: ignore[union-attr]
	shell_only = {'help', 'exit', 'forget'}
	cli_only = {'init', 'version', 'chat'}
	# A per-session stance has no meaning in a one-shot `qa <cmd>` process, which exits before
	# it could matter. The bar for adding to this set is that high - if a command could
	# sensibly be typed at a terminal, it belongs in the CLI too.
	session_only = {'mode'}
	# the vault sub-actions are one CLI subcommand (`qa vault set`), several registry entries
	vault_subcommands = {name for name in REGISTRY if name.startswith('vault-')}
	assert set(REGISTRY) - shell_only - session_only - vault_subcommands == cli_commands - cli_only

	vault_parser = cast(Any, parser._subparsers)._group_actions[0].choices['vault']  # type: ignore[union-attr]
	actions_arg = next(a for a in cast(list[Any], vault_parser._actions) if a.dest == 'action')
	assert {f'vault-{name}' for name in actions_arg.choices if name != 'status'} <= vault_subcommands


def test_vault_commands_are_human_only() -> None:
	"""Storing, granting and revoking a credential is a human keystroke, like approving."""
	agent_names = {c.name for c in agent_commands()}
	for name in ('vault-set', 'vault-rm', 'vault-grant', 'vault-revoke'):
		assert REGISTRY[name].human_only is True
		assert name not in agent_names
	assert 'vault' in agent_names  # read-only status is fine for the agent to run


def test_auth_guards(tmp_path: Path) -> None:
	ctx = make_ctx(tmp_path)
	assert asyncio.run(actions.auth(ctx.ws, ctx.ch, config=ctx.config)) == 2  # nothing configured

	from nkqa.config import MCPServer

	ctx.config.mcp_servers = [MCPServer(name='jira', command='npx')]
	assert asyncio.run(actions.auth(ctx.ws, ctx.ch, 'nope', config=ctx.config)) == 2  # unknown server


# --- what the router knows about the app ------------------------------------


class CapturingLLM(StubLLM):
	"""Records the prompt, so a test can assert what the router was actually told."""

	def __init__(self, decisions: list[agent.ChatDecision]):
		super().__init__(decisions)
		self.prompt = ''

	async def ainvoke(self, messages: Any, output_format: Any = None) -> Any:
		self.prompt = '\n'.join(str(m.content) for m in messages)
		return await super().ainvoke(messages, output_format)


def capture(monkeypatch: pytest.MonkeyPatch, decisions: list[agent.ChatDecision]) -> CapturingLLM:
	llm = CapturingLLM(decisions)

	def fake(cfg: Config, role: str, override: str | None = None) -> CapturingLLM:
		return llm

	monkeypatch.setattr(agent, 'resolve_llm', fake)
	return llm


def test_the_router_is_told_what_the_app_map_says(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	"""It used to know which scenarios existed and nothing about the app they test.

	So the first question anyone asks - "what does the clients page do?" - had no answer in
	its context at all, and the only honest reply it could give was a guess.
	"""
	ctx = make_ctx(tmp_path)
	(ctx.ws.appmap_dir / 'pages').mkdir()
	(ctx.ws.appmap_dir / 'pages' / 'clients.md').write_text('# Clients\n\n**Route:** `/clients`\n\nA table of clients.')
	llm = capture(monkeypatch, [agent.ChatDecision(reply='It lists clients.', command='')])

	asyncio.run(session.handle(ctx, 'what does the clients page do?'))

	assert 'A table of clients.' in llm.prompt
	assert 'the app map' in llm.prompt.lower()


def test_a_question_is_answered_without_running_anything(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
	ctx = make_ctx(tmp_path)
	llm = capture(monkeypatch, [agent.ChatDecision(reply='It lists clients, with a Create Client button.', command='')])

	assert asyncio.run(session.handle(ctx, 'what does /clients do?')) == 0
	assert 'Create Client button' in capsys.readouterr().out
	assert llm.calls == 1, 'answering a question is one turn, not a command round trip'


def test_an_empty_map_is_admitted_rather_than_hidden(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	ctx = make_ctx(tmp_path)
	(ctx.ws.appmap_dir / 'overview.md').unlink()
	llm = capture(monkeypatch, [agent.ChatDecision(reply='I do not know yet.', command='')])

	asyncio.run(session.handle(ctx, 'what does this app do?'))
	assert 'app map is empty' in llm.prompt


def test_correcting_the_map_is_something_the_agent_may_do(tmp_path: Path) -> None:
	"""Not human_only: the point is to fix the map by saying so, not by typing a slash command.

	The guard is elsewhere - it is told to pass on what the human said, the diff is shown, and
	the write is a git commit that can be reverted.
	"""
	assert 'correct' in {c.name for c in agent_commands()}
	assert REGISTRY['correct'].human_only is False


def test_a_refused_command_is_named_correctly(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
	"""It used to answer every human_only refusal with "type /approve", whatever was asked."""
	ctx = make_ctx(tmp_path)
	stub_chat(
		monkeypatch,
		[
			agent.ChatDecision(
				reply='Switching to auto.', command='mode', args=[agent.ChatArg(name='value', value='allow')]
			)
		],
	)

	asyncio.run(session.handle(ctx, 'stop asking me for permission'))

	out = capsys.readouterr().out
	assert '/mode allow' in out
	assert '/approve' not in out


# --- a chat that cannot hang -------------------------------------------------


class HangingLLM:
	"""Never answers. What a reasoning model wired to the `chat` role looks like from here."""

	async def ainvoke(self, messages: Any, output_format: Any = None) -> Any:
		await asyncio.sleep(3600)


class FailingLLM:
	def __init__(self, error: Exception):
		self.error = error

	async def ainvoke(self, messages: Any, output_format: Any = None) -> Any:
		raise self.error


def use_llm(monkeypatch: pytest.MonkeyPatch, llm: object) -> None:
	def fake(cfg: Config, role: str, override: str | None = None) -> object:
		return llm

	monkeypatch.setattr(agent, 'resolve_llm', fake)


def test_a_model_that_never_answers_gives_up_and_says_which_one(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
	"""Saying "hi" and watching `working…` forever is the worst version of this.

	Both clients ship with no request timeout and retry hard (OpenAI 5, Anthropic 10), so
	nothing below this bounded it. Naming the model matters: the usual cause is that the
	`chat` role points at something far too heavy for routing a greeting.
	"""
	ctx = make_ctx(tmp_path)
	ctx.config.aliases['fast'] = 'openai:gpt-5'
	use_llm(monkeypatch, HangingLLM())
	monkeypatch.setattr(agent, 'CHAT_TIMEOUT', 0.05)

	assert asyncio.run(session.handle(ctx, 'hi')) == 1

	out = capsys.readouterr().out
	assert 'openai:gpt-5' in out, 'it must name the model that stalled'
	assert '/set-model --fast' in out, 'and say how to change it'


def test_a_provider_error_is_shown_not_swallowed(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
	ctx = make_ctx(tmp_path)
	use_llm(monkeypatch, FailingLLM(RuntimeError('model_not_found: gpt-5 does not exist')))

	assert asyncio.run(session.handle(ctx, 'hi')) == 1
	assert 'model_not_found' in capsys.readouterr().out


def test_the_timeout_does_not_fire_on_a_model_that_answers(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
	"""The guard must be about hanging, not about capping useful work."""
	ctx = make_ctx(tmp_path)
	stub_chat(monkeypatch, [agent.ChatDecision(reply='Hello.', command='')])

	assert asyncio.run(session.handle(ctx, 'hi')) == 0
	assert 'Hello.' in capsys.readouterr().out


# --- the shape sent to the model --------------------------------------------


def strict_mode_violations(schema: object, path: str = 'root') -> list[str]:
	"""Objects OpenAI's strict structured outputs will reject: no declared properties."""
	found: list[str] = []
	if isinstance(schema, dict):
		node = cast(dict[str, Any], schema)
		if node.get('type') == 'object' and 'properties' not in node:
			found.append(path)
		for key, value in node.items():
			found += strict_mode_violations(value, f'{path}.{key}')
	elif isinstance(schema, list):
		for i, value in enumerate(cast(list[Any], schema)):
			found += strict_mode_violations(value, f'{path}[{i}]')
	return found


def test_every_schema_we_send_survives_openai_strict_mode() -> None:
	"""`args: dict[str, str]` is the obvious shape for this and it 400s every chat message.

	browser-use hardcodes `strict: True` for OpenAI, and strict mode rejects an object that
	does not declare its properties - which is exactly what a free-form dict becomes. The
	failure is total (no chat works at all) and invisible until you switch provider, so it is
	worth pinning for every model we hand to a provider, not just the one that broke.
	"""
	from browser_use.llm.schema import SchemaOptimizer

	from nkqa.appmap import AppmapUpdate
	from nkqa.planner import PlanOutput
	from nkqa.revise import RevisedScenario

	for model in (agent.ChatDecision, AppmapUpdate, RevisedScenario, PlanOutput):
		schema = SchemaOptimizer.create_optimized_json_schema(model)
		assert not strict_mode_violations(schema), f'{model.__name__} would be rejected by OpenAI'
		# Strict mode also demands every property be listed as required.
		assert set(schema.get('required', [])) == set(schema.get('properties', {})), model.__name__


def test_command_arguments_survive_the_round_trip() -> None:
	decision = agent.ChatDecision(
		reply='Running it.',
		command='file-bug',
		args=[agent.ChatArg(name='run', value='demo--1'), agent.ChatArg(name='step', value='2')],
	)
	assert agent.args_of(decision) == {'run': 'demo--1', 'step': '2'}
	assert agent.coerce(REGISTRY['file-bug'], agent.args_of(decision)) == {'run': 'demo--1', 'step': 2}
