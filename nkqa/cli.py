"""qa - AI QA teammate. Dispatch only; logic lives in the other modules."""

import argparse
import asyncio
import sys
from importlib.metadata import version as pkg_version
from pathlib import Path

from dotenv import load_dotenv

from nkqa import config as config_mod
from nkqa import workspace as workspace_mod
from nkqa.hitl import HumanInTheLoop
from nkqa.workspace import Workspace


def build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(prog='qa', description=__doc__)
	sub = parser.add_subparsers(dest='command')

	sub.add_parser('init', help='create a QA workspace in the current directory')

	run = sub.add_parser('run', help='record a new AI-driven test')
	run.add_argument('url', nargs='?', default='', help='URL to test')
	run.add_argument('focus', nargs='?', default='', help='what to focus on')
	run.add_argument('--name', default='', metavar='TEST_NAME', help='meaningful name for this recording')
	run.add_argument('--model', default=None, metavar='MODEL', help='executor model (alias or browser-use name)')

	replay = sub.add_parser('replay', help='replay a recorded test deterministically, without the LLM')
	replay.add_argument('name', nargs='?', default='', help='test name, dir, or history.json path')
	replay.add_argument('--all', action='store_true', help='replay every recorded test (CI mode)')
	replay.add_argument('--var', action='append', default=[], metavar='KEY=VALUE', help='override a recorded value')

	sub.add_parser('list', help='list all recorded tests')
	sub.add_parser('version', help='show nkqa and browser-use versions')
	return parser


def require_workspace() -> Workspace:
	ws = workspace_mod.find()
	if ws is None:
		print('Not inside a QA workspace. Create one first:  qa init')
		sys.exit(2)
	return ws


def cmd_init() -> int:
	ws = workspace_mod.create(Path.cwd())
	print(f'✅ QA workspace ready at {ws.root}')
	print(f'   Edit {ws.config_file.name} (app URL, models), then:  qa run')

	old_output = ws.root / 'qa_output'
	recordings = workspace_mod.prototype_recordings(old_output)
	if recordings and not any(d.is_dir() for d in ws.runs_dir.iterdir()):
		names = ', '.join(d.name for d in recordings)
		answer = (
			input(f'   Found old recordings in qa_output/ ({names}). Move them into runs/? [y/N]: ').strip().lower()
		)
		if answer == 'y':
			moved = workspace_mod.migrate_prototype(ws, old_output)
			print(f'   Moved {len(moved)} recording(s) into runs/.')
	return 0


def main() -> None:
	load_dotenv()
	args = build_parser().parse_args()

	if args.command == 'version':
		print(f'nkqa {pkg_version("nkqa")} (browser-use {pkg_version("browser-use")})')
		sys.exit(0)
	if args.command == 'init':
		sys.exit(cmd_init())
	if args.command == 'list':
		from nkqa.execution.evidence import list_runs

		sys.exit(list_runs(require_workspace().runs_dir))
	if args.command == 'replay':
		from nkqa.execution.replay import replay, replay_all, resolve_history_file

		ws = require_workspace()
		hitl = HumanInTheLoop(ws.permissions_file)
		if args.all:
			sys.exit(asyncio.run(replay_all(ws, hitl, args.var)))
		history_file = resolve_history_file(ws, args.name)
		if history_file is None:
			sys.exit(2)
		sys.exit(asyncio.run(replay(ws, hitl, history_file, args.var)))
	if args.command == 'run':
		from nkqa.execution.runner import run_freeform

		ws = require_workspace()
		cfg = config_mod.load(ws.config_file)
		hitl = HumanInTheLoop(ws.permissions_file)
		sys.exit(asyncio.run(run_freeform(ws, cfg, hitl, args.url, args.focus, args.name, args.model)))

	build_parser().print_help()
	sys.exit(2)


if __name__ == '__main__':
	main()
