"""qa - AI QA teammate. Dispatch only; logic lives in the other modules."""

import argparse
import asyncio
import sys
from datetime import datetime
from importlib.metadata import version as pkg_version
from pathlib import Path

from dotenv import load_dotenv

from nkqa import config as config_mod
from nkqa import scenarios as scenarios_mod
from nkqa import workspace as workspace_mod
from nkqa.hitl import HumanInTheLoop
from nkqa.workspace import Workspace


def build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(prog='qa', description=__doc__)
	sub = parser.add_subparsers(dest='command')

	sub.add_parser('init', help='create a QA workspace in the current directory')

	plan = sub.add_parser('plan', help='draft test scenarios from app knowledge (no browser)')
	plan.add_argument('ask', nargs='?', default='', help='what to test, e.g. "the checkout flow"')
	plan.add_argument('--ticket', default='', metavar='KEY', help='plan from a Jira ticket, e.g. PROJ-123')
	plan.add_argument('--area', default='', help='force all drafts under this scenario area')
	plan.add_argument('--force', action='store_true', help='overwrite existing scenario files')

	bug = sub.add_parser('file-bug', help='file a Jira bug from a failed run (human-instructed only)')
	bug.add_argument('run', help='run dir name under runs/, e.g. checkout-coupon--20260826-2238')
	bug.add_argument('--step', type=int, default=0, help='which failed step to file (when several failed)')
	bug.add_argument('--project', default='', metavar='KEY', help='Jira project key (default: jira.project in config)')

	run = sub.add_parser('run', help='execute an approved scenario')
	run.add_argument('id', nargs='?', default='', help='scenario id, e.g. checkout/coupon')
	run.add_argument('--model', default=None, metavar='MODEL', help='executor model (alias or browser-use name)')

	explore = sub.add_parser('explore', help='record a freeform AI-driven test (no scenario)')
	explore.add_argument('url', nargs='?', default='', help='URL to test')
	explore.add_argument('focus', nargs='?', default='', help='what to focus on')
	explore.add_argument('--name', default='', metavar='TEST_NAME', help='meaningful name for this recording')
	explore.add_argument('--model', default=None, metavar='MODEL', help='executor model (alias or browser-use name)')

	sub.add_parser('scenarios', help='list scenarios with status and last verdict')

	approve = sub.add_parser('approve', help='review a scenario and approve it (hash-bound)')
	approve.add_argument('id', help='scenario id')

	replay = sub.add_parser('replay', help='replay a recorded run deterministically, without the LLM')
	replay.add_argument('name', nargs='?', default='', help='run name, dir, or history.json path')
	replay.add_argument('--all', action='store_true', help='replay every recorded run (CI mode)')
	replay.add_argument('--var', action='append', default=[], metavar='KEY=VALUE', help='override a recorded value')

	sub.add_parser('list', help='list all recorded runs')
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
	print(f'   Edit {ws.config_file.name} (app URL, models), then:  qa plan "<what to test>"')

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


def cmd_scenarios(ws: Workspace) -> int:
	from nkqa.execution.report import last_verdict

	all_scenarios = scenarios_mod.load_all(ws.scenarios_dir)
	if not all_scenarios:
		print('No scenarios yet. Draft some:  qa plan "<what to test>"')
		return 0
	print(f'\n{"STATUS":<12} {"LAST RUN":<9} {"ID":<36} TITLE')
	for s in all_scenarios:
		state = s.runnable()
		label = {'ok': 'approved', 'draft': 'draft', 'stale': 'STALE', 'deprecated': 'deprecated'}[state]
		print(f'{label:<12} {last_verdict(ws.runs_dir, s.id) or "-":<9} {s.id:<36} {s.title}')
	print('\nApprove:  qa approve <id>    Run:  qa run <id>')
	return 0


def cmd_approve(ws: Workspace, scenario_id: str) -> int:
	s = scenarios_mod.find(ws.scenarios_dir, scenario_id)
	if s is None:
		print(f'No scenario "{scenario_id}". See:  qa scenarios')
		return 2
	state = s.runnable()
	if state == 'ok':
		print(f'"{s.id}" is already approved and unchanged (by {s.approved_by} at {s.approved_at}).')
		return 0
	print(f'\n──── {s.path} ────\n')
	print(s.path.read_text(encoding='utf-8'))
	print('────')
	if state == 'stale':
		print(f'⚠️  Edited since last approval (by {s.approved_by} at {s.approved_at}) - re-approval needed.')
	answer = input(f'Approve "{s.id}" for execution? [y/N]: ').strip().lower()
	if answer != 'y':
		print('Not approved.')
		return 1
	scenarios_mod.approve(s, scenarios_mod.git_identity(ws.root))
	print(f'✅ Approved (hash {s.approved_hash[:12]}). Run it:  qa run {s.id}')
	return 0


def cmd_file_bug(ws: Workspace, run_name: str, step: int, project: str) -> int:
	from nkqa.execution.report import read_results
	from nkqa.jira import compose_bug, create_bug, failed_steps, jira_server
	from nkqa.mcp import MCPRuntime

	run_dir = ws.runs_dir / run_name
	if not run_dir.is_dir():
		print(f'No run "{run_name}" under {ws.runs_dir}/. See:  qa list')
		return 2
	record = read_results(run_dir)
	if record is None:
		print('This run has no results.json - only scenario runs can file bugs.')
		return 2
	scenario = scenarios_mod.find(ws.scenarios_dir, record.scenario_id)
	if scenario is None:
		print(f'Scenario "{record.scenario_id}" no longer exists; cannot compose the bug.')
		return 2
	failures = failed_steps(record)
	if not failures:
		print('Every step passed in this run - nothing to file. 🎉')
		return 0
	if step:
		failures = [f for f in failures if f.step == step]
		if not failures:
			print(f'Step {step} did not fail in this run.')
			return 2
	if len(failures) > 1:
		print('Several steps failed - pick one with --step:')
		for f in failures:
			print(f'  --step {f.step}  [{f.verdict}] {f.note[:100]}')
		return 2
	failure = failures[0]

	cfg = config_mod.load(ws.config_file)
	project = project or cfg.jira_project
	if not project:
		print('No Jira project key: pass --project KEY or set jira.project in config.yaml.')
		return 2
	spec = jira_server(cfg)

	summary, description = compose_bug(scenario, record, failure, run_dir, cfg.base_url)
	print(f'\n──── bug preview ({project}) ────\n\n{summary}\n\n{description}\n\n────')
	if input('File this bug? [y/N]: ').strip().lower() != 'y':
		print('Not filed.')
		return 1

	async def _file() -> str:
		async with MCPRuntime([spec]) as rt:
			return await create_bug(rt, spec, project, summary, description)

	key = asyncio.run(_file())
	with (run_dir / 'results.md').open('a', encoding='utf-8') as fh:
		fh.write(f'\nFiled: {key} ({datetime.now():%Y-%m-%d %H:%M})\n')
	print(f'🐞 Filed {key}. Recorded in {run_dir / "results.md"}.')
	return 0


def cmd_run(ws: Workspace, scenario_id: str, model: str | None) -> int:
	from nkqa.execution.scenario_runner import run_scenario

	if scenario_id.startswith(('http://', 'https://')) or '.' in scenario_id.split('/')[0]:
		print(f'"{scenario_id}" looks like a URL. Freeform testing moved:  qa explore {scenario_id}')
		return 2
	if not scenario_id:
		return cmd_scenarios(ws)
	s = scenarios_mod.find(ws.scenarios_dir, scenario_id)
	if s is None:
		print(f'No scenario "{scenario_id}". See:  qa scenarios')
		return 2
	cfg = config_mod.load(ws.config_file)
	hitl = HumanInTheLoop(ws.permissions_file)
	return asyncio.run(run_scenario(ws, cfg, hitl, s, model))


def main() -> None:
	load_dotenv()
	args = build_parser().parse_args()

	if args.command == 'version':
		print(f'nkqa {pkg_version("nkqa")} (browser-use {pkg_version("browser-use")})')
		sys.exit(0)
	if args.command == 'init':
		sys.exit(cmd_init())
	if args.command == 'plan':
		from nkqa.planner import plan as plan_cmd

		if not args.ask and not args.ticket:
			print('Tell me what to plan:  qa plan "<ask>"  and/or  qa plan --ticket PROJ-123')
			sys.exit(2)
		ws = require_workspace()
		cfg = config_mod.load(ws.config_file)
		sys.exit(asyncio.run(plan_cmd(ws, cfg, args.ask, args.area, args.force, args.ticket)))
	if args.command == 'file-bug':
		sys.exit(cmd_file_bug(require_workspace(), args.run, args.step, args.project))
	if args.command == 'scenarios':
		sys.exit(cmd_scenarios(require_workspace()))
	if args.command == 'approve':
		sys.exit(cmd_approve(require_workspace(), args.id))
	if args.command == 'run':
		sys.exit(cmd_run(require_workspace(), args.id, args.model))
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
	if args.command == 'explore':
		from nkqa.execution.runner import run_freeform

		ws = require_workspace()
		cfg = config_mod.load(ws.config_file)
		hitl = HumanInTheLoop(ws.permissions_file)
		sys.exit(asyncio.run(run_freeform(ws, cfg, hitl, args.url, args.focus, args.name, args.model)))

	build_parser().print_help()
	sys.exit(2)
