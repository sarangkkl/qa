"""Verdict models, results.md rendering, and results.json persistence for scenario runs."""

import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from nkqa.scenarios import Scenario

Verdict = Literal['pass', 'fail', 'blocked']


class StepVerdict(BaseModel):
	step: int
	verdict: Verdict
	note: str = ''


class ScenarioResult(BaseModel):
	steps: list[StepVerdict]
	summary: str = ''


def overall(result: ScenarioResult | None, expected_steps: int) -> Verdict:
	"""Missing or partial verdicts are never a silent pass."""
	if result is None:
		return 'blocked'
	if any(v.verdict == 'fail' for v in result.steps):
		return 'fail'
	covered = {v.step for v in result.steps}
	if any(v.verdict == 'blocked' for v in result.steps) or covered < set(range(1, expected_steps + 1)):
		return 'blocked'
	return 'pass'


def _cell(text: str) -> str:
	return text.replace('|', '\\|').replace('\n', ' ')


def write_results(run_dir: Path, scenario: Scenario, result: ScenarioResult | None) -> Verdict:
	verdict = overall(result, len(scenario.steps))
	when = datetime.now().strftime('%Y-%m-%d %H:%M')
	by_index = {v.step: v for v in (result.steps if result else [])}
	lines = [
		f'# {scenario.id} — {verdict.upper()} — {when}',
		'',
		f'**{scenario.title}** · approved hash at run time: `{scenario.approved_hash[:12]}`',
		'',
		'| # | Step | Verdict | Note |',
		'|---|------|---------|------|',
	]
	for i, step in enumerate(scenario.steps, 1):
		v = by_index.get(i)
		lines.append(
			f'| {i} | {_cell(step.action)} | {(v.verdict if v else "blocked").upper()} '
			f'| {_cell(v.note) if v else "no verdict returned"} |'
		)
	if result and result.summary:
		lines += ['', '## Summary', '', result.summary]
	lines += [
		'',
		'## Evidence',
		'',
		'- [Recording (history.json)](history.json)',
		'- [Step-by-step gif](last_run.gif)',
		'- [Videos](videos/)',
		'- [LLM transcript](conversation/)',
	]
	(run_dir / 'results.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
	(run_dir / 'results.json').write_text(
		json.dumps(
			{
				'scenario_id': scenario.id,
				'verdict': verdict,
				'approved_hash': scenario.approved_hash,
				'result': result.model_dump() if result else None,
			},
			indent=1,
		),
		encoding='utf-8',
	)
	return verdict


class RunRecord(BaseModel):
	scenario_id: str
	verdict: Verdict
	approved_hash: str = ''
	result: ScenarioResult | None = None


def read_results(run_dir: Path) -> RunRecord | None:
	f = run_dir / 'results.json'
	if not f.is_file():
		return None
	return RunRecord.model_validate_json(f.read_text(encoding='utf-8'))


def latest_run_dir(runs_dir: Path, scenario_id: str) -> Path | None:
	"""Newest run dir for a scenario, by the results.md it wrote."""
	slug = scenario_id.replace('/', '-')
	candidates = sorted(runs_dir.glob(f'{slug}--*/results.md'), key=lambda p: p.stat().st_mtime)
	return candidates[-1].parent if candidates else None


def last_verdict(runs_dir: Path, scenario_id: str) -> str:
	"""Latest verdict for a scenario ('PASS'/'FAIL'/'BLOCKED'), '' if never run."""
	run_dir = latest_run_dir(runs_dir, scenario_id)
	if run_dir is None:
		return ''
	first_line = (run_dir / 'results.md').read_text(encoding='utf-8').splitlines()[0]
	for v in ('PASS', 'FAIL', 'BLOCKED'):
		if f'— {v} —' in first_line:
			return v
	return ''
