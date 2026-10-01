"""The Library: tests that passed, and whose recording replays without a model.

Nothing gets in by being written or approved. A run Claude drove has to pass, prove every
expectation with a check, and then replay once by itself - only then is its recording kept,
next to the scenario, bound to the approval it was recorded under. Edit the scenario and it
drops out until it is recorded again. Saving is a human decision; Claude has no tool for it.
"""

from pathlib import Path
from typing import Any

from nkqa import config as config_mod
from nkqa import scenarios as scenarios_mod
from nkqa.execution.driver import read_step_log
from nkqa.execution.playback import Recording, from_run, play
from nkqa.execution.report import latest_run_dir, read_results
from nkqa.hitl import HumanInTheLoop
from nkqa.scenarios import Scenario
from nkqa.ui import Channel
from nkqa.workspace import Workspace


def load(ws: Workspace, scenario_id: str) -> Recording | None:
	path = ws.recording_file(scenario_id)
	if not path.is_file():
		return None
	try:
		return Recording.model_validate_json(path.read_text(encoding='utf-8'))
	except ValueError:
		return None


def valid(s: Scenario, recording: Recording | None) -> bool:
	"""In the Library: approved, unedited since, and recorded against exactly this approval."""
	return recording is not None and s.runnable() == 'ok' and recording.approved_hash == s.approved_hash


def entries(ws: Workspace) -> list[dict[str, Any]]:
	"""The Library as the UI lists it - valid tests only, folder by folder."""
	out: list[dict[str, Any]] = []
	for s in scenarios_mod.load_all(ws.scenarios_dir):
		recording = load(ws, s.id)
		if not valid(s, recording):
			continue
		assert recording is not None
		run_dir = latest_run_dir(ws.runs_dir, s.id)
		record = read_results(run_dir) if run_dir else None
		out.append(
			{
				'id': s.id,
				'title': s.title,
				'folder': s.id.rpartition('/')[0],
				'steps': len(recording.steps),
				'saved_at': recording.saved_at,
				'recorded_from': recording.recorded_from,
				'last_run': run_dir.name if run_dir else '',
				'last_verdict': record.verdict.upper() if record else '',
			}
		)
	return out


def selected(ws: Workspace, target: str) -> list[Scenario]:
	"""A test id, a folder (everything under it), or '' for the whole Library."""
	target = target.strip().strip('/')
	return [
		s
		for s in scenarios_mod.load_all(ws.scenarios_dir)
		if valid(s, load(ws, s.id)) and (not target or s.id == target or s.id.startswith(target + '/'))
	]


async def replay(ws: Workspace, hitl: HumanInTheLoop, ch: Channel, target: str = '') -> int:
	"""Replay Library tests with no model. Exit codes as everywhere: 0 pass, 1 fail, 2 nothing to run."""
	tests = selected(ws, target)
	if not tests:
		await ch.log(f'Nothing in the Library matches "{target}".' if target else 'The Library is empty.')
		return 2
	cfg = config_mod.load(ws.config_file)
	results: dict[str, str] = {}
	for s in tests:
		recording = load(ws, s.id)
		assert recording is not None
		results[s.id] = await play(s, recording, hitl, ch, ws.scenario_run_dir(s.id), cfg.base_url, cfg.headless)
	if len(results) > 1:
		await ch.log('\n=== LIBRARY REPLAY ===')
		for sid, verdict in results.items():
			await ch.log(f'  {"✅" if verdict == "pass" else "❌"} {verdict.upper():<7} {sid}')
		passed = sum(v == 'pass' for v in results.values())
		await ch.log(f'\n{passed}/{len(results)} passed')
	return 0 if all(v == 'pass' for v in results.values()) else 1


async def save(ws: Workspace, hitl: HumanInTheLoop, ch: Channel, run: str) -> int:
	"""Keep a passing run as a Library test - only if its recording replays by itself."""
	run_dir = ws.runs_dir / run
	record = read_results(run_dir) if run and run_dir.is_dir() else None
	log = read_step_log(run_dir) if record else None
	if record is None or log is None:
		await ch.log(f'No scenario run "{run}" to save.')
		return 2
	s = scenarios_mod.find(ws.scenarios_dir, record.scenario_id)
	if s is None:
		await ch.log(f'The scenario "{record.scenario_id}" no longer exists.')
		return 2

	problem = refusal(s, record.verdict, record.approved_hash, log.replay, checked(log.steps))
	if problem:
		await ch.log(f'Not saved: {problem}')
		return 1

	cfg = config_mod.load(ws.config_file)
	recording = Recording(
		scenario_id=s.id,
		approved_hash=s.approved_hash,
		base_url=cfg.base_url,
		recorded_from=run,
		saved_at=log.finished_at or log.started_at,
		steps=from_run(log.steps),
	)
	await ch.log('Replaying it once without a model, to prove the recording works…')
	verdict = await play(s, recording, hitl, ch, ws.scenario_run_dir(s.id), cfg.base_url, cfg.headless)
	if verdict != 'pass':
		await ch.log(
			'Not saved: the recording did not replay cleanly (see the replay above). Run it again with Claude.'
		)
		return 1
	write(ws.recording_file(s.id), recording)
	await ch.log(f'📚 Saved to the Library: {s.id}')
	return 0


def checked(steps: list[Any]) -> set[int]:
	return {int(r.params.get('step') or 0) for r in steps if r.action == 'check' and not r.error}


def refusal(s: Scenario, verdict: str, approved_hash: str, replay: bool, proven: set[int]) -> str:
	"""Why a run cannot become a Library test, or '' if it can."""
	if replay:
		return 'this is a replay; save the Claude run it came from.'
	if verdict != 'pass':
		return f'the run did not pass ({verdict}). Only passing tests go in the Library.'
	if s.runnable() != 'ok' or s.approved_hash != approved_hash:
		return f'"{s.id}" was edited or re-approved since this run. Run it again first.'
	missing = [i for i, step in enumerate(s.steps, 1) if step.expect and i not in proven]
	if missing and not proven:
		return 'this run recorded no checks (it predates them, or they were skipped). Run it again and save that run.'
	if missing:
		return f'step(s) {", ".join(map(str, missing))} have no passing check, so a replay could not prove them.'
	return ''


def write(path: Path, recording: Recording) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(recording.model_dump_json(indent=1), encoding='utf-8')
