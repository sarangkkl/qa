"""Deterministic replay of recorded runs - no LLM decisions, free and repeatable."""

# browser-use boundary: its internals are partially untyped, so the Unknown family is off here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

import re
from pathlib import Path

from browser_use import Agent
from browser_use.browser import BrowserProfile
from pydantic import BaseModel

from nkqa import config as config_mod
from nkqa.execution.evidence import list_runs, recorded_runs
from nkqa.hitl import HumanInTheLoop
from nkqa.models import resolve_llm
from nkqa.ui import Channel
from nkqa.workspace import Workspace, slugify


async def _collect_replay_secrets(hitl: HumanInTheLoop, history_file: Path) -> None:
	"""If the recording used credentials, ask the human for them again (values are never stored)."""
	needed = set(re.findall(r'<secret>(.*?)</secret>', history_file.read_text(encoding='utf-8')))
	for key in sorted(needed - set(hitl.secrets)):
		await hitl.collect_secret(key, f'🔑 The recording uses "{key}". Enter it for this replay: ')


async def resolve_history_file(ws: Workspace, ch: Channel, name_or_path: str) -> Path | None:
	"""Accept a run name, a run dir, or a direct path to a history.json."""
	if not name_or_path:
		runs = recorded_runs(ws.runs_dir)
		if len(runs) == 1:
			return runs[0] / 'history.json'
		await list_runs(ch, ws.runs_dir)
		return None
	candidates = [
		ws.runs_dir / name_or_path / 'history.json',  # exact run-dir name (timestamped scenario runs)
		ws.runs_dir / slugify(name_or_path) / 'history.json',
		Path(name_or_path) / 'history.json',
		Path(name_or_path),
	]
	for c in candidates:
		if c.is_file():
			return c
	await ch.log(f'No recording found for "{name_or_path}".')
	await list_runs(ch, ws.runs_dir)
	return None


def parse_vars(var_pairs: list[str]) -> dict[str, str]:
	variables: dict[str, str] = {}
	for pair in var_pairs:
		key, _, value = pair.partition('=')
		variables[key.strip()] = value
	return variables


async def replay(ws: Workspace, hitl: HumanInTheLoop, ch: Channel, history_file: Path, var_pairs: list[str]) -> int:
	variables = parse_vars(var_pairs)
	run_dir = history_file.parent
	await _collect_replay_secrets(hitl, history_file)
	await ch.log(f'\n▶️  Replaying {run_dir.name}' + (f' with overrides {list(variables)}' if variables else ''))

	from nkqa.execution.report import ScenarioResult

	config = config_mod.load(ws.config_file)
	# scenario runs (marked by results.md) recorded a structured done action -
	# the replay agent needs the same schema to parse the recording back
	schema = ScenarioResult if (run_dir / 'results.md').is_file() else None
	agent: Agent[None, BaseModel] = Agent(
		task=f'Replay of recorded QA test "{run_dir.name}"',
		# replay never calls the LLM, but Agent() demands one at construction
		llm=resolve_llm(config, 'fallback') or resolve_llm(config, 'executor'),
		output_model_schema=schema,
		tools=hitl.build_tools(),
		sensitive_data=hitl.secrets,
		browser_profile=BrowserProfile(headless=False, record_video_dir=run_dir / 'videos'),
		file_system_path=str(run_dir),
	)
	from nkqa.execution import stream

	async with stream.forward(ch):
		results = await agent.load_and_rerun(history_file, variables=variables or None, skip_failures=True)

	failed = 0
	await ch.log(f'\n=== REPLAY REPORT: {run_dir.name} ===')
	for i, r in enumerate(results, start=1):
		if r.error:
			failed += 1
			await ch.step(f'step {i:>2}: ❌ {r.error.splitlines()[0][:120]}', n=i, ok=False)
		else:
			summary = (r.extracted_content or 'ok').splitlines()[0][:120]
			await ch.step(f'step {i:>2}: ✅ {summary}', n=i, ok=True)
	await ch.verdict(
		f'\nVerdict: {"PASS" if failed == 0 else f"FAIL ({failed} step(s) failed)"}',
		run=run_dir.name,
		verdict='pass' if failed == 0 else 'fail',
	)
	return 0 if failed == 0 else 1


async def replay_all(ws: Workspace, hitl: HumanInTheLoop, ch: Channel, var_pairs: list[str]) -> int:
	runs = recorded_runs(ws.runs_dir)
	if not runs:
		await ch.log('No recorded tests yet. Record one: qa run <url>')
		return 2
	verdicts: dict[str, int] = {}
	for d in runs:
		verdicts[d.name] = await replay(ws, hitl, ch, d / 'history.json', var_pairs)
	await ch.log('\n=== SUITE SUMMARY ===')
	for name, code in verdicts.items():
		await ch.log(f'  {"✅ PASS" if code == 0 else "❌ FAIL"}  {name}')
	failed = sum(1 for c in verdicts.values() if c != 0)
	await ch.log(f'\n{len(verdicts) - failed}/{len(verdicts)} tests passed')
	return 0 if failed == 0 else 1
