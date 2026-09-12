"""Run-dir bookkeeping: which recorded runs exist, and listing them."""

import json
from datetime import datetime
from pathlib import Path

from nkqa.ui import Channel, Event

# Two recorders: browser-use's Agent writes history.json (replayable); the MCP driver, where
# the external agent chose every action, writes steps.json (evidence only - nothing to replay).
HISTORY = 'history.json'
STEP_LOG = 'steps.json'


def recording_file(run_dir: Path) -> Path | None:
	for name in (HISTORY, STEP_LOG):
		if (run_dir / name).is_file():
			return run_dir / name
	return None


def recorded_runs(runs_dir: Path) -> list[Path]:
	"""All run dirs that contain a recording, oldest first."""
	if not runs_dir.is_dir():
		return []
	found = [(d, rec) for d in runs_dir.iterdir() if (rec := recording_file(d)) is not None]
	return [d for d, _ in sorted(found, key=lambda pair: pair[1].stat().st_mtime)]


def step_count(recording: Path) -> int | str:
	try:
		data = json.loads(recording.read_text(encoding='utf-8'))
		return len(data.get('history' if recording.name == HISTORY else 'steps', []))
	except (json.JSONDecodeError, OSError, AttributeError):
		return '?'


async def list_runs(ch: Channel, runs_dir: Path) -> int:
	runs = recorded_runs(runs_dir)
	if not runs:
		await ch.log('No recorded tests yet. Record one: qa run <url>')
		return 0
	await ch.log(f'\n{"TEST":<32} {"RECORDED":<18} STEPS')
	for d in runs:
		hist = recording_file(d) or d / HISTORY
		when = datetime.fromtimestamp(hist.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
		steps = step_count(hist)
		await ch.emit(Event('log', f'{d.name:<32} {when:<18} {steps}', {'run': d.name, 'when': when, 'steps': steps}))
	await ch.log('\nReplay one:  qa replay <name>\nReplay all:  qa replay --all')
	return 0
