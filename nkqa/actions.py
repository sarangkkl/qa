"""The verbs both front-ends call: the argparse CLI and the interactive shell.

Everything here returns a process-style exit code (0 ok, 1 failure, 2 usage/not-found)
so the CLI can exit with it and the shell can report it.
"""

import asyncio
from collections.abc import Coroutine
from datetime import datetime
from pathlib import Path
from typing import Any

from nkqa import config as config_mod
from nkqa import scenarios as scenarios_mod
from nkqa import workspace as workspace_mod
from nkqa.config import Config
from nkqa.hitl import HumanInTheLoop
from nkqa.workspace import Workspace

STATUS_LABELS = {'ok': 'approved', 'draft': 'draft', 'stale': 'STALE', 'deprecated': 'deprecated'}


def _cfg(ws: Workspace, config: Config | None) -> Config:
	return config or config_mod.load(ws.config_file)


def _hitl(ws: Workspace, hitl: HumanInTheLoop | None) -> HumanInTheLoop:
	return hitl or HumanInTheLoop(ws.permissions_file)


def init() -> int:
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


def list_scenarios(ws: Workspace) -> int:
	from nkqa.execution.report import last_verdict

	all_scenarios = scenarios_mod.load_all(ws.scenarios_dir)
	if not all_scenarios:
		print('No scenarios yet. Draft some:  qa plan "<what to test>"')
		return 0
	print(f'\n{"STATUS":<12} {"LAST RUN":<9} {"ID":<36} TITLE')
	for s in all_scenarios:
		print(f'{STATUS_LABELS[s.runnable()]:<12} {last_verdict(ws.runs_dir, s.id) or "-":<9} {s.id:<36} {s.title}')
	print('\nApprove:  qa approve <id>    Run:  qa run <id>')
	return 0


def approve(ws: Workspace, scenario_id: str) -> int:
	"""Human-only: never exposed to the chat agent."""
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
	if input(f'Approve "{s.id}" for execution? [y/N]: ').strip().lower() != 'y':
		print('Not approved.')
		return 1
	scenarios_mod.approve(s, scenarios_mod.git_identity(ws.root))
	print(f'✅ Approved (hash {s.approved_hash[:12]}). Run it:  qa run {s.id}')
	return 0


def models() -> int:
	from nkqa.models import ROLES, describe_role

	ws = workspace_mod.find()
	cfg = config_mod.load(ws.config_file if ws else None)
	source = str(ws.config_file) if ws else 'built-in defaults (no workspace found)'
	print(f'\nModel setup from: {source}\n')
	print(f'{"ROLE":<11} {"MODEL":<36} {"PROVIDER":<20} KEYS')
	ok = True
	for role in ROLES:
		name, provider, keys, missing = describe_role(cfg, role)
		if missing:
			ok = False
			status = ' '.join(f'❌ {k}' for k in missing)
		else:
			status = '✅ ' + ', '.join(keys) if keys else '⚠️ unknown provider'
		print(f'{role:<11} {name:<36} {provider:<20} {status}')
	print('\nChange models in config.yaml (models/aliases); put keys in .env.')
	return 0 if ok else 1


def list_runs(ws: Workspace) -> int:
	from nkqa.execution.evidence import list_runs as _list

	return _list(ws.runs_dir)


async def plan(
	ws: Workspace, ask: str = '', ticket: str = '', area: str = '', force: bool = False, config: Config | None = None
) -> int:
	from nkqa.planner import plan as _plan

	if not ask and not ticket:
		print('Tell me what to plan:  qa plan "<ask>"  and/or  qa plan --ticket PROJ-123')
		return 2
	return await _plan(ws, _cfg(ws, config), ask, area, force, ticket)


async def revise(ws: Workspace, scenario_id: str, instruction: str, config: Config | None = None) -> int:
	from nkqa.revise import revise as _revise

	return await _revise(ws, _cfg(ws, config), scenario_id, instruction)


async def run_scenario(
	ws: Workspace,
	scenario_id: str = '',
	model: str | None = None,
	config: Config | None = None,
	hitl: HumanInTheLoop | None = None,
) -> int:
	from nkqa.execution.scenario_runner import run_scenario as _run

	if scenario_id.startswith(('http://', 'https://')) or '.' in scenario_id.split('/')[0]:
		print(f'"{scenario_id}" looks like a URL. Freeform testing:  qa explore {scenario_id}')
		return 2
	if not scenario_id:
		return list_scenarios(ws)
	s = scenarios_mod.find(ws.scenarios_dir, scenario_id)
	if s is None:
		print(f'No scenario "{scenario_id}". See:  qa scenarios')
		return 2
	return await _run(ws, _cfg(ws, config), _hitl(ws, hitl), s, model)


async def explore(
	ws: Workspace,
	url: str = '',
	focus: str = '',
	name: str = '',
	model: str | None = None,
	config: Config | None = None,
	hitl: HumanInTheLoop | None = None,
) -> int:
	from nkqa.execution.runner import run_freeform

	return await run_freeform(ws, _cfg(ws, config), _hitl(ws, hitl), url, focus, name, model)


async def learn(ws: Workspace, path: str, config: Config | None = None) -> int:
	from nkqa.ingest import learn as _learn

	return await _learn(ws, _cfg(ws, config), path)


async def reflect(ws: Workspace, run: str, config: Config | None = None) -> int:
	from nkqa.reflector import reflect as _reflect

	run_dir = ws.runs_dir / run
	if not run_dir.is_dir():
		print(f'No run "{run}" under {ws.runs_dir}/. See:  qa list')
		return 2
	written = await _reflect(ws, _cfg(ws, config), run_dir)
	print(f'🧠 appmap updated: {", ".join(written)}' if written else 'Nothing new learned from this run.')
	return 0


async def crawl(
	ws: Workspace,
	pages: int = 0,
	model: str | None = None,
	config: Config | None = None,
	hitl: HumanInTheLoop | None = None,
) -> int:
	from nkqa.crawler import crawl as _crawl

	cfg = _cfg(ws, config)
	return await _crawl(ws, cfg, _hitl(ws, hitl), pages or cfg.crawl_pages, model)


async def replay(
	ws: Workspace,
	run: str = '',
	all_runs: bool = False,
	var: list[str] | None = None,
	hitl: HumanInTheLoop | None = None,
) -> int:
	from nkqa.execution.replay import replay as _replay
	from nkqa.execution.replay import replay_all, resolve_history_file

	session_hitl = _hitl(ws, hitl)
	if all_runs:
		return await replay_all(ws, session_hitl, var or [])
	history_file = resolve_history_file(ws, run)
	if history_file is None:
		return 2
	return await _replay(ws, session_hitl, history_file, var or [])


async def file_bug(ws: Workspace, run: str, step: int = 0, project: str = '', config: Config | None = None) -> int:
	from nkqa.execution.report import read_results
	from nkqa.jira import compose_bug, create_bug, failed_steps, jira_server
	from nkqa.mcp import MCPRuntime

	run_dir = ws.runs_dir / run
	if not run_dir.is_dir():
		print(f'No run "{run}" under {ws.runs_dir}/. See:  qa list')
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

	cfg = _cfg(ws, config)
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

	async with MCPRuntime([spec]) as rt:
		key = await create_bug(rt, spec, project, summary, description)
	with (run_dir / 'results.md').open('a', encoding='utf-8') as fh:
		fh.write(f'\nFiled: {key} ({datetime.now():%Y-%m-%d %H:%M})\n')
	print(f'🐞 Filed {key}. Recorded in {run_dir / "results.md"}.')
	return 0


def run_sync(coro: Coroutine[Any, Any, int]) -> int:
	"""Run an async action from the synchronous CLI."""
	return asyncio.run(coro)
