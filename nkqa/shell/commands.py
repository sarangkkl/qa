"""One command registry: the slash parser and the chat agent's tools both come from here.

Adding a Command makes it available in both surfaces at once. `human_only` commands
(approve) are deliberately absent from the agent's tool list - approving is a human
keystroke, and that gate is what the whole product rests on.
"""

import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from nkqa import actions
from nkqa import scenarios as scenarios_mod
from nkqa.config import Config
from nkqa.hitl import HumanInTheLoop
from nkqa.workspace import Workspace


@dataclass
class Param:
	name: str
	help: str
	type: str = 'string'  # string | integer | boolean
	flag: bool = False  # passed as --name, not positionally
	rest: bool = False  # swallows the remaining words (free text)
	required: bool = False


@dataclass
class ShellContext:
	ws: Workspace
	config: Config
	hitl: HumanInTheLoop
	last_scenarios: list[str] = field(default_factory=list[str])
	last_runs: list[str] = field(default_factory=list[str])
	running: bool = True


Handler = Callable[[ShellContext, dict[str, Any]], Awaitable[int]]


@dataclass
class Command:
	name: str
	help: str
	handler: Handler
	params: list[Param] = field(default_factory=list[Param])
	human_only: bool = False  # never exposed to the chat agent
	shell_only: bool = False  # meta commands (help/exit/forget): no agent, no CLI


def _remember_scenarios(ctx: ShellContext) -> None:
	ctx.last_scenarios = [s.id for s in scenarios_mod.load_all(ctx.ws.scenarios_dir)]


def _remember_runs(ctx: ShellContext) -> None:
	from nkqa.execution.evidence import recorded_runs

	ctx.last_runs = [d.name for d in recorded_runs(ctx.ws.runs_dir)]


async def _plan(ctx: ShellContext, a: dict[str, Any]) -> int:
	code = await actions.plan(
		ctx.ws,
		str(a.get('ask', '')),
		str(a.get('ticket', '')),
		str(a.get('area', '')),
		bool(a.get('force')),
		ctx.config,
	)
	_remember_scenarios(ctx)
	return code


async def _scenarios(ctx: ShellContext, a: dict[str, Any]) -> int:
	_remember_scenarios(ctx)
	return actions.list_scenarios(ctx.ws)


async def _approve(ctx: ShellContext, a: dict[str, Any]) -> int:
	return actions.approve(ctx.ws, str(a.get('id', '')))


async def _revise(ctx: ShellContext, a: dict[str, Any]) -> int:
	return await actions.revise(ctx.ws, str(a.get('id', '')), str(a.get('instruction', '')), ctx.config)


async def _run(ctx: ShellContext, a: dict[str, Any]) -> int:
	code = await actions.run_scenario(ctx.ws, str(a.get('id', '')), a.get('model'), ctx.config, ctx.hitl)
	_remember_runs(ctx)
	return code


async def _explore(ctx: ShellContext, a: dict[str, Any]) -> int:
	code = await actions.explore(
		ctx.ws,
		str(a.get('url', '')),
		str(a.get('focus', '')),
		str(a.get('name', '')),
		a.get('model'),
		ctx.config,
		ctx.hitl,
	)
	_remember_runs(ctx)
	return code


async def _learn(ctx: ShellContext, a: dict[str, Any]) -> int:
	return await actions.learn(ctx.ws, str(a.get('path', '')), ctx.config)


async def _reflect(ctx: ShellContext, a: dict[str, Any]) -> int:
	return await actions.reflect(ctx.ws, str(a.get('run', '')), ctx.config)


async def _crawl(ctx: ShellContext, a: dict[str, Any]) -> int:
	return await actions.crawl(ctx.ws, int(a.get('pages') or 0), a.get('model'), ctx.config, ctx.hitl)


async def _replay(ctx: ShellContext, a: dict[str, Any]) -> int:
	return await actions.replay(ctx.ws, str(a.get('run', '')), bool(a.get('all')), None, ctx.hitl)


async def _list(ctx: ShellContext, a: dict[str, Any]) -> int:
	_remember_runs(ctx)
	return actions.list_runs(ctx.ws)


async def _file_bug(ctx: ShellContext, a: dict[str, Any]) -> int:
	return await actions.file_bug(
		ctx.ws, str(a.get('run', '')), int(a.get('step') or 0), str(a.get('project', '')), ctx.config
	)


async def _auth(ctx: ShellContext, a: dict[str, Any]) -> int:
	return await actions.auth(ctx.ws, str(a.get('server', '')), bool(a.get('reset')), ctx.config)


async def _models(ctx: ShellContext, a: dict[str, Any]) -> int:
	return actions.models()


async def _forget(ctx: ShellContext, a: dict[str, Any]) -> int:
	count = len(ctx.hitl.secrets)
	ctx.hitl.secrets.clear()
	ctx.hitl.session_grants.clear()
	print(f"🧹 Cleared {count} credential(s) and this session's permission grants.")
	return 0


async def _exit(ctx: ShellContext, a: dict[str, Any]) -> int:
	ctx.running = False
	return 0


async def _help(ctx: ShellContext, a: dict[str, Any]) -> int:
	print('\nCommands (type /name, or just say what you want in plain English):\n')
	for cmd in REGISTRY.values():
		args = ' '.join(f'<{p.name}>' if p.required else f'[{p.name}]' for p in cmd.params)
		marker = '  (human only)' if cmd.human_only else ''
		print(f'  /{cmd.name:<10} {args:<28} {cmd.help}{marker}')
	print('\nCtrl+C cancels what is running · Ctrl+D or /exit leaves\n')
	return 0


COMMANDS = [
	Command(
		'plan',
		'draft scenarios from app knowledge (no browser)',
		_plan,
		[
			Param('ask', 'what to test, e.g. "the checkout flow"', rest=True),
			Param('ticket', 'Jira ticket key to plan from, e.g. PROJ-123', flag=True),
			Param('area', 'put drafts under this scenario area', flag=True),
			Param('force', 'overwrite existing scenario files', type='boolean', flag=True),
		],
	),
	Command('scenarios', 'list scenarios with status and last verdict', _scenarios),
	Command(
		'approve',
		'review a scenario and approve it for execution',
		_approve,
		[Param('id', 'scenario id', required=True)],
		human_only=True,
	),
	Command(
		'revise',
		'rewrite a scenario from an instruction (invalidates its approval)',
		_revise,
		[
			Param('id', 'scenario id', required=True),
			Param('instruction', 'how to change it, e.g. "make step 3 stricter"', rest=True, required=True),
		],
	),
	Command(
		'run',
		'execute an approved scenario in a real browser',
		_run,
		[Param('id', 'scenario id'), Param('model', 'executor model override', flag=True)],
	),
	Command(
		'explore',
		'freeform AI-driven testing, no scenario',
		_explore,
		[
			Param('url', 'URL to test'),
			Param('focus', 'what to focus on', rest=True),
			Param('name', 'name for this recording', flag=True),
			Param('model', 'executor model override', flag=True),
		],
	),
	Command(
		'learn',
		'ingest app knowledge from an annotated doc or screenshot folder',
		_learn,
		[Param('path', 'path to a markdown doc or folder', required=True)],
	),
	Command('reflect', 'update the appmap from a past run', _reflect, [Param('run', 'run dir name', required=True)]),
	Command(
		'crawl',
		'explore the live app read-only to enrich the appmap',
		_crawl,
		[
			Param('pages', 'page budget', type='integer', flag=True),
			Param('model', 'executor model override', flag=True),
		],
	),
	Command(
		'replay',
		'replay a recorded run deterministically, without the LLM',
		_replay,
		[Param('run', 'run dir name'), Param('all', 'replay every recorded run', type='boolean', flag=True)],
	),
	Command('list', 'list recorded runs', _list),
	Command(
		'file-bug',
		'file a Jira bug from a failed run (shows a preview and asks first)',
		_file_bug,
		[
			Param('run', 'run dir name', required=True),
			Param('step', 'which failed step to file', type='integer', flag=True),
			Param('project', 'Jira project key', flag=True),
		],
	),
	Command(
		'auth',
		'sign in to configured MCP servers (Jira) and verify them',
		_auth,
		[
			Param('server', 'server name from config.yaml'),
			Param('reset', 'clear cached logins first', type='boolean', flag=True),
		],
	),
	Command('models', 'show model roles, providers, and API key status', _models),
	Command('forget', 'clear credentials and permission grants held for this session', _forget, shell_only=True),
	Command('help', 'show this list', _help, shell_only=True),
	Command('exit', 'leave the session', _exit, shell_only=True),
]

REGISTRY: dict[str, Command] = {c.name: c for c in COMMANDS}


def agent_commands() -> list[Command]:
	"""Tools the chat agent may call: never human_only, never shell meta commands."""
	return [c for c in COMMANDS if not c.human_only and not c.shell_only]


def parse_slash(line: str) -> tuple[Command | None, dict[str, Any], str]:
	"""'/run checkout/coupon --model smart' -> (Command, {'id': ..., 'model': ...}, error)."""
	text = line.strip().lstrip('/')
	if not text:
		return None, {}, 'empty command'
	name, _, remainder = text.partition(' ')
	cmd = REGISTRY.get(name)
	if cmd is None:
		close = [c.name for c in COMMANDS if c.name.startswith(name[:3])]
		hint = f' Did you mean /{close[0]}?' if close else ' Type /help.'
		return None, {}, f'Unknown command "/{name}".{hint}'
	try:
		tokens = shlex.split(remainder)
	except ValueError:
		tokens = remainder.split()

	by_name = {p.name: p for p in cmd.params}
	args: dict[str, Any] = {}
	positional = [p for p in cmd.params if not p.flag]
	leftovers: list[str] = []
	i = 0
	while i < len(tokens):
		token = tokens[i]
		if token.startswith('--'):
			key = token[2:]
			param = by_name.get(key)
			if param is None:
				return None, {}, f'/{cmd.name} has no option --{key}'
			if param.type == 'boolean':
				args[key] = True
			else:
				i += 1
				if i >= len(tokens):
					return None, {}, f'--{key} needs a value'
				args[key] = int(tokens[i]) if param.type == 'integer' else tokens[i]
		else:
			leftovers.append(token)
		i += 1

	for param in positional:
		if not leftovers:
			break
		if param.rest:
			args[param.name] = ' '.join(leftovers)
			leftovers = []
		else:
			args[param.name] = leftovers.pop(0)
	if leftovers:
		args.setdefault(positional[-1].name if positional else 'ask', ' '.join(leftovers))

	missing = [p.name for p in cmd.params if p.required and not args.get(p.name)]
	if missing:
		return None, {}, f'/{cmd.name} needs: {", ".join(missing)}'
	return cmd, args, ''
