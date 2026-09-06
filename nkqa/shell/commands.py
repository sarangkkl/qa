"""One command registry: the slash parser and the chat agent's tools both come from here.

Adding a Command makes it available in both surfaces at once. `human_only` commands
(approve) are deliberately absent from the agent's tool list - approving is a human
keystroke, and that gate is what the whole product rests on.
"""

import shlex
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from nkqa import actions
from nkqa import config as config_mod
from nkqa import scenarios as scenarios_mod
from nkqa.chats import Chat
from nkqa.config import Config
from nkqa.hitl import AUTONOMY, HumanInTheLoop
from nkqa.stop import StopSignal
from nkqa.ui import Channel, Event, TerminalChannel
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
	channel: Channel = field(default_factory=TerminalChannel)
	chat: 'Chat | None' = None  # when set, the router records what it said and did
	stop: StopSignal = field(default_factory=StopSignal)  # how a run gets interrupted
	last_scenarios: list[str] = field(default_factory=list[str])
	last_runs: list[str] = field(default_factory=list[str])
	running: bool = True

	@property
	def ch(self) -> Channel:
		return self.channel


Handler = Callable[[ShellContext, dict[str, Any]], Coroutine[Any, Any, int]]


@dataclass
class Command:
	name: str
	help: str
	handler: Handler
	params: list[Param] = field(default_factory=list[Param])
	human_only: bool = False  # never exposed to the chat agent
	shell_only: bool = False  # meta commands (help/exit/forget): no agent, no CLI
	# Runs outside the one-job-at-a-time runner, so it still works while a run is in flight.
	# Only safe for a command that touches no browser, writes nothing a running job is also
	# writing, and never asks: an instant command's channel is not in the pending-ask map,
	# so a prompt from one could never be answered.
	instant: bool = False


def _remember_scenarios(ctx: ShellContext) -> None:
	ctx.last_scenarios = [s.id for s in scenarios_mod.load_all(ctx.ws.scenarios_dir)]


def _remember_runs(ctx: ShellContext) -> None:
	from nkqa.execution.evidence import recorded_runs

	ctx.last_runs = [d.name for d in recorded_runs(ctx.ws.runs_dir)]


async def _plan(ctx: ShellContext, a: dict[str, Any]) -> int:
	code = await actions.plan(
		ctx.ws,
		ctx.ch,
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
	return await actions.list_scenarios(ctx.ws, ctx.ch)


async def _approve(ctx: ShellContext, a: dict[str, Any]) -> int:
	return await actions.approve(ctx.ws, ctx.ch, str(a.get('id', '')))


async def _revise(ctx: ShellContext, a: dict[str, Any]) -> int:
	return await actions.revise(ctx.ws, ctx.ch, str(a.get('id', '')), str(a.get('instruction', '')), ctx.config)


async def _run(ctx: ShellContext, a: dict[str, Any]) -> int:
	code = await actions.run_scenario(
		ctx.ws, ctx.ch, str(a.get('id', '')), a.get('model'), ctx.config, ctx.hitl, ctx.stop
	)
	_remember_runs(ctx)
	return code


async def _explore(ctx: ShellContext, a: dict[str, Any]) -> int:
	code = await actions.explore(
		ctx.ws,
		ctx.ch,
		str(a.get('url', '')),
		str(a.get('focus', '')),
		str(a.get('name', '')),
		a.get('model'),
		ctx.config,
		ctx.hitl,
		ctx.stop,
	)
	_remember_runs(ctx)
	return code


async def _learn(ctx: ShellContext, a: dict[str, Any]) -> int:
	return await actions.learn(ctx.ws, ctx.ch, str(a.get('path', '')), ctx.config)


async def _correct(ctx: ShellContext, a: dict[str, Any]) -> int:
	return await actions.correct(ctx.ws, ctx.ch, str(a.get('instruction', '')), ctx.config)


async def _reflect(ctx: ShellContext, a: dict[str, Any]) -> int:
	return await actions.reflect(ctx.ws, ctx.ch, str(a.get('run', '')), ctx.config)


async def _crawl(ctx: ShellContext, a: dict[str, Any]) -> int:
	return await actions.crawl(
		ctx.ws, ctx.ch, int(a.get('pages') or 0), a.get('model'), ctx.config, ctx.hitl, ctx.stop, bool(a.get('refresh'))
	)


async def _suite(ctx: ShellContext, a: dict[str, Any]) -> int:
	code = await actions.suite(
		ctx.ws, ctx.ch, str(a.get('tag', '')), bool(a.get('strict')), a.get('model'), ctx.config, ctx.hitl, ctx.stop
	)
	_remember_runs(ctx)
	return code


async def _compare(ctx: ShellContext, a: dict[str, Any]) -> int:
	return await actions.compare(ctx.ws, ctx.ch, str(a.get('first', '')), str(a.get('second', '')))


async def _replay(ctx: ShellContext, a: dict[str, Any]) -> int:
	return await actions.replay(ctx.ws, ctx.ch, str(a.get('run', '')), bool(a.get('all')), None, ctx.hitl)


async def _list(ctx: ShellContext, a: dict[str, Any]) -> int:
	_remember_runs(ctx)
	return await actions.list_runs(ctx.ws, ctx.ch)


async def _file_bug(ctx: ShellContext, a: dict[str, Any]) -> int:
	return await actions.file_bug(
		ctx.ws, ctx.ch, str(a.get('run', '')), int(a.get('step') or 0), str(a.get('project', '')), ctx.config
	)


async def _auth(ctx: ShellContext, a: dict[str, Any]) -> int:
	return await actions.auth(ctx.ws, ctx.ch, str(a.get('server', '')), bool(a.get('reset')), ctx.config)


async def _models(ctx: ShellContext, a: dict[str, Any]) -> int:
	return await actions.models(ctx.ch, ctx.ws)


async def _set_model(ctx: ShellContext, a: dict[str, Any]) -> int:
	code = await actions.set_model(
		ctx.ws, ctx.ch, str(a.get('provider', '')), str(a.get('smart', '')), str(a.get('fast', ''))
	)
	# The session holds one Config, read when the socket opened. Without this the file says
	# one thing and the next run uses another - the setting would look applied and do nothing.
	ctx.config = config_mod.load(ctx.ws.config_file)
	return code


async def _forget(ctx: ShellContext, a: dict[str, Any]) -> int:
	count = ctx.hitl.forget()
	await ctx.ch.log(f"🧹 Cleared {count} credential(s) and this session's permission grants.")
	return 0


async def _exit(ctx: ShellContext, a: dict[str, Any]) -> int:
	ctx.running = False
	return 0


MODE_HELP = {
	'ask': 'stop and ask every time (the default)',
	'allow': 'grant risky actions automatically, for this session',
	'refuse': 'refuse risky actions automatically, for this session',
}


async def _mode(ctx: ShellContext, a: dict[str, Any]) -> int:
	"""human_only, so the agent can never widen its own autonomy - the `approve` mechanism."""
	value = str(a.get('value', '')).strip().lower()
	if not value:
		await ctx.ch.log(f'Autonomy: {ctx.hitl.autonomy}')
		for name, help_text in MODE_HELP.items():
			await ctx.ch.log(f'  /mode {name:<7} {help_text}')
		return 0
	if value not in AUTONOMY:
		await ctx.ch.log(f'No autonomy mode "{value}". Pick one of: {", ".join(AUTONOMY)}')
		return 2
	ctx.hitl.autonomy = value  # narrowed to Autonomy by the membership check above
	await ctx.ch.emit(Event('log', f'⚙️  Autonomy: {value} — {MODE_HELP[value]}.', {'mode': value}))
	if value != 'ask':
		await ctx.ch.log('   It governs actions the agent declares risky. It is not a sandbox.')
	return 0


async def _help(ctx: ShellContext, a: dict[str, Any]) -> int:
	await ctx.ch.log('\nCommands (type /name, or just say what you want in plain English):\n')
	for cmd in REGISTRY.values():
		args = ' '.join(f'<{p.name}>' if p.required else f'[{p.name}]' for p in cmd.params)
		marker = '  (human only)' if cmd.human_only else ''
		await ctx.ch.log(f'  /{cmd.name:<10} {args:<28} {cmd.help}{marker}')
	await ctx.ch.log('\nCtrl+C cancels what is running · Ctrl+D or /exit leaves\n')
	return 0


ACTION_COMMANDS = [
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
		'correct',
		'fix what the app map gets wrong, in your own words',
		_correct,
		# rest=True so it reads as a sentence: /correct client rows open /clients/<id>
		[
			Param(
				'instruction', 'what is actually true, e.g. "client rows open /clients/<id>"', rest=True, required=True
			)
		],
	),
	Command(
		'crawl',
		'explore the live app read-only to enrich the appmap',
		_crawl,
		[
			Param('pages', 'page budget', type='integer', flag=True),
			Param('model', 'executor model override', flag=True),
			Param('refresh', 're-map pages already documented', type='boolean', flag=True),
		],
	),
	Command(
		'replay',
		'replay a recorded run deterministically, without the LLM',
		_replay,
		[Param('run', 'run dir name'), Param('all', 'replay every recorded run', type='boolean', flag=True)],
	),
	Command(
		'suite',
		'run every approved scenario (optionally by tag) and report like CI',
		_suite,
		[
			Param('tag', 'only scenarios carrying this tag'),
			Param('strict', 'also fail when a scenario is excluded (draft or STALE)', type='boolean', flag=True),
			Param('model', 'executor model override', flag=True),
		],
	),
	Command(
		'compare',
		'what changed between two suite runs (default: the two newest)',
		_compare,
		[Param('first', 'older suite run name'), Param('second', 'newer suite run name')],
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
	Command(
		'set-model',
		'choose the provider and models config.yaml uses (anthropic | openai)',
		_set_model,
		[
			Param('provider', 'anthropic | openai', flag=True),
			Param('smart', 'model id for the smart tier (planning, revising)', flag=True),
			Param('fast', 'model id for the fast tier (executing, reflecting, chat)', flag=True),
		],
		# The agent must never choose the model it is about to be judged with, or pick the
		# cheapest one to get through a task. Same reasoning as approve and the vault writes.
		human_only=True,
		# Settings has to work while something is running - that is exactly when you notice
		# the model is wrong. It writes one config file, touches no browser and never asks.
		instant=True,
	),
]

# Session controls: real commands over the socket and in the shell, but deliberately not
# `qa` subcommands - a per-session stance has no meaning in a one-shot `qa <cmd>` process.
SESSION_COMMANDS = [
	Command(
		'mode',
		'how much the agent may do without asking (ask | allow | refuse)',
		_mode,
		[Param('value', 'ask | allow | refuse')],
		human_only=True,  # the agent must never be able to widen its own autonomy
		instant=True,  # usable mid-run, which is the only time it matters
	),
]

SHELL_COMMANDS = [
	Command('forget', 'clear credentials and permission grants held for this session', _forget, shell_only=True),
	Command('help', 'show this list', _help, shell_only=True),
	Command('exit', 'leave the session', _exit, shell_only=True),
]


# One list per contributing module, concatenated here: adding a command touches only its
# own module, so the surfaces that generate themselves from this stay merge-friendly.
def _vault_commands() -> list[Command]:
	from nkqa.vault_commands import VAULT_COMMANDS  # imported here: vault_commands imports this module

	return VAULT_COMMANDS


COMMANDS = [*ACTION_COMMANDS, *_vault_commands(), *SESSION_COMMANDS, *SHELL_COMMANDS]

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
