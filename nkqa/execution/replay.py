"""Deterministic replay of recorded runs - no LLM decisions, free and repeatable."""

# browser-use boundary: its internals are partially untyped, so the Unknown family is off here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

import re
from pathlib import Path

from browser_use import Agent
from browser_use.browser import BrowserProfile
from pydantic import BaseModel

from nkqa.execution.evidence import list_runs, recorded_runs
from nkqa.hitl import HumanInTheLoop
from nkqa.workspace import Workspace, slugify


async def _collect_replay_secrets(hitl: HumanInTheLoop, history_file: Path) -> None:
	"""If the recording used credentials, ask the human for them again (values are never stored)."""
	needed = set(re.findall(r'<secret>(.*?)</secret>', history_file.read_text(encoding='utf-8')))
	for key in sorted(needed - set(hitl.secrets)):
		await hitl.collect_secret(key, f'🔑 The recording uses "{key}". Enter it for this replay: ')


def resolve_history_file(ws: Workspace, name_or_path: str) -> Path | None:
	"""Accept a run name, a run dir, or a direct path to a history.json."""
	if not name_or_path:
		runs = recorded_runs(ws.runs_dir)
		if len(runs) == 1:
			return runs[0] / 'history.json'
		list_runs(ws.runs_dir)
		return None
	candidates = [
		ws.runs_dir / slugify(name_or_path) / 'history.json',
		Path(name_or_path) / 'history.json',
		Path(name_or_path),
	]
	for c in candidates:
		if c.is_file():
			return c
	print(f'No recording found for "{name_or_path}".')
	list_runs(ws.runs_dir)
	return None


def parse_vars(var_pairs: list[str]) -> dict[str, str]:
	variables: dict[str, str] = {}
	for pair in var_pairs:
		key, _, value = pair.partition('=')
		variables[key.strip()] = value
	return variables


async def replay(ws: Workspace, hitl: HumanInTheLoop, history_file: Path, var_pairs: list[str]) -> int:
	variables = parse_vars(var_pairs)
	run_dir = history_file.parent
	await _collect_replay_secrets(hitl, history_file)
	print(f'\n▶️  Replaying {run_dir.name}' + (f' with overrides {list(variables)}' if variables else ''))

	agent: Agent[None, BaseModel] = Agent(
		task=f'Replay of recorded QA test "{run_dir.name}"',
		tools=hitl.build_tools(),
		sensitive_data=hitl.secrets,
		browser_profile=BrowserProfile(headless=False, record_video_dir=run_dir / 'videos'),
		file_system_path=str(run_dir),
	)
	results = await agent.load_and_rerun(history_file, variables=variables or None, skip_failures=True)

	failed = 0
	print(f'\n=== REPLAY REPORT: {run_dir.name} ===')
	for i, r in enumerate(results, start=1):
		if r.error:
			failed += 1
			print(f'step {i:>2}: ❌ {r.error.splitlines()[0][:120]}')
		else:
			summary = (r.extracted_content or 'ok').splitlines()[0][:120]
			print(f'step {i:>2}: ✅ {summary}')
	print(f'\nVerdict: {"PASS" if failed == 0 else f"FAIL ({failed} step(s) failed)"}')
	return 0 if failed == 0 else 1


async def replay_all(ws: Workspace, hitl: HumanInTheLoop, var_pairs: list[str]) -> int:
	runs = recorded_runs(ws.runs_dir)
	if not runs:
		print('No recorded tests yet. Record one: qa run <url>')
		return 2
	verdicts: dict[str, int] = {}
	for d in runs:
		verdicts[d.name] = await replay(ws, hitl, d / 'history.json', var_pairs)
	print('\n=== SUITE SUMMARY ===')
	for name, code in verdicts.items():
		print(f'  {"✅ PASS" if code == 0 else "❌ FAIL"}  {name}')
	failed = sum(1 for c in verdicts.values() if c != 0)
	print(f'\n{len(verdicts) - failed}/{len(verdicts)} tests passed')
	return 0 if failed == 0 else 1
