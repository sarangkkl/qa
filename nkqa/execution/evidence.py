"""Run-dir bookkeeping: which recorded runs exist, and listing them."""

import json
from datetime import datetime
from pathlib import Path

from nkqa.ui import Channel, Event


def recorded_runs(runs_dir: Path) -> list[Path]:
	"""All run dirs that contain a recording, oldest first."""
	if not runs_dir.is_dir():
		return []
	dirs = [d for d in runs_dir.iterdir() if (d / 'history.json').is_file()]
	return sorted(dirs, key=lambda d: (d / 'history.json').stat().st_mtime)


def step_count(history: Path) -> int | str:
	try:
		return len(json.loads(history.read_text(encoding='utf-8')).get('history', []))
	except (json.JSONDecodeError, OSError):
		return '?'


async def list_runs(ch: Channel, runs_dir: Path) -> int:
	runs = recorded_runs(runs_dir)
	if not runs:
		await ch.log('No recorded tests yet. Record one: qa run <url>')
		return 0
	await ch.log(f'\n{"TEST":<32} {"RECORDED":<18} STEPS')
	for d in runs:
		hist = d / 'history.json'
		when = datetime.fromtimestamp(hist.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
		steps = step_count(hist)
		await ch.emit(Event('log', f'{d.name:<32} {when:<18} {steps}', {'run': d.name, 'when': when, 'steps': steps}))
	await ch.log('\nReplay one:  qa replay <name>\nReplay all:  qa replay --all')
	return 0
