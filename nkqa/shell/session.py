"""The interactive session: banner, prompt loop, slash dispatch, free-text routing."""

import contextlib
from pathlib import Path

from nkqa import config as config_mod
from nkqa import scenarios as scenarios_mod
from nkqa import workspace as workspace_mod
from nkqa.hitl import HumanInTheLoop
from nkqa.shell import render
from nkqa.shell.commands import REGISTRY, ShellContext, parse_slash
from nkqa.stop import interruptible
from nkqa.ui import TerminalChannel
from nkqa.vault import Vault

PROMPT = 'qa> '
HISTORY_FILE = '.qa_history'


def setup_readline(ctx: ShellContext) -> object | None:
	try:
		import readline
	except ImportError:  # pragma: no cover - readline ships with CPython on macOS/Linux
		return None
	with contextlib.suppress(OSError):
		readline.read_history_file(str(ctx.ws.root / HISTORY_FILE))
	readline.set_history_length(1000)
	readline.parse_and_bind('tab: complete')
	readline.set_completer_delims(' \t\n')

	def complete(text: str, state: int) -> str | None:
		options = [f'/{name}' for name in REGISTRY if f'/{name}'.startswith(text)]
		options += [s.id for s in scenarios_mod.load_all(ctx.ws.scenarios_dir) if s.id.startswith(text)]
		return options[state] if state < len(options) else None

	readline.set_completer(complete)
	return readline


def save_history(readline_mod: object | None, ws_root: Path) -> None:
	if readline_mod is None:
		return
	with contextlib.suppress(OSError, AttributeError):
		readline_mod.write_history_file(str(ws_root / HISTORY_FILE))  # type: ignore[attr-defined]


async def handle(ctx: ShellContext, line: str) -> int:
	if line.startswith('/'):
		cmd, args, error = parse_slash(line)
		if cmd is None:
			await ctx.ch.log(error)
			return 2
		return await cmd.handler(ctx, args)

	from nkqa.shell.agent import route

	return await route(ctx, line)


async def start() -> int:
	ws = workspace_mod.find()
	if ws is None:
		print('Not inside a QA workspace. Create one first:  qa init')
		return 2
	channel = TerminalChannel()
	ctx = ShellContext(
		ws=ws,
		config=config_mod.load(ws.config_file),
		# Same as the sidecar: no vault means every stored credential silently falls through
		# to "type it again", because `_from_vault` returns early when it is None.
		hitl=HumanInTheLoop(ws.permissions_file, channel, Vault(ws)),
		channel=channel,
	)
	readline_mod = setup_readline(ctx)
	print(render.banner(ws, ctx.config))

	while ctx.running:
		try:
			line = input(render.paint(PROMPT, 'bold')).strip()
		except EOFError:
			break
		except KeyboardInterrupt:
			print()
			continue
		if not line:
			continue
		try:
			# Ctrl+C during a run stops the run, not the session.
			await interruptible(handle(ctx, line), ctx.stop)
		except KeyboardInterrupt:
			print(render.paint('\n⏹  cancelled - evidence for anything that ran is saved.', 'yellow'))
		except Exception as e:  # a bad command must never kill the session
			print(render.paint(f'💥 {type(e).__name__}: {e}', 'red'))

	save_history(readline_mod, ws.root)
	print(render.paint('\nBye - your scenarios, evidence and appmap are on disk.\n', 'dim'))
	return 0
