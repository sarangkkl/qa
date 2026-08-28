"""Terminal rendering with no dependencies: ANSI colours and the session banner."""

import os
import sys
from importlib.metadata import version as pkg_version

from nkqa import appmap
from nkqa import scenarios as scenarios_mod
from nkqa.config import Config
from nkqa.workspace import Workspace

CODES = {'dim': '2', 'bold': '1', 'red': '31', 'green': '32', 'yellow': '33', 'blue': '34', 'cyan': '36'}


def supports_colour() -> bool:
	return sys.stdout.isatty() and os.environ.get('TERM') != 'dumb' and not os.environ.get('NO_COLOR')


def paint(text: str, style: str) -> str:
	if not supports_colour() or style not in CODES:
		return text
	return f'\033[{CODES[style]}m{text}\033[0m'


def banner(ws: Workspace, config: Config) -> str:
	from nkqa.execution.evidence import recorded_runs

	scenarios = scenarios_mod.load_all(ws.scenarios_dir)
	states = [s.runnable() for s in scenarios]
	counts = {
		'approved': states.count('ok'),
		'draft': states.count('draft'),
		'STALE': states.count('stale'),
	}
	summary = ', '.join(f'{n} {label}' for label, n in counts.items() if n) or 'none yet'
	appmap_files = len(appmap.read_all(ws))
	runs = len(recorded_runs(ws.runs_dir))
	where = config.base_url or str(ws.root)
	roles = ' '.join(f'{r}={config.models.get(r, "?")}' for r in ('planner', 'executor', 'chat'))

	lines = [
		'',
		paint(f'  nkqa {pkg_version("nkqa")}', 'bold') + paint(f' · {config.app_name} ({where})', 'cyan'),
		paint(f'  appmap: {appmap_files} files · scenarios: {summary} · runs: {runs}', 'dim'),
		paint(f'  models: {roles}', 'dim'),
		'',
		paint('  Say what you want in plain English, or use /commands. /help for the list.', 'dim'),
		'',
	]
	return '\n'.join(lines)
