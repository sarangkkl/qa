"""Run-dir bookkeeping: which recorded runs exist, and listing them."""

import json
from datetime import datetime
from pathlib import Path


def recorded_runs(runs_dir: Path) -> list[Path]:
	"""All run dirs that contain a recording, oldest first."""
	if not runs_dir.is_dir():
		return []
	dirs = [d for d in runs_dir.iterdir() if (d / 'history.json').is_file()]
	return sorted(dirs, key=lambda d: (d / 'history.json').stat().st_mtime)


def list_runs(runs_dir: Path) -> int:
	runs = recorded_runs(runs_dir)
	if not runs:
		print('No recorded tests yet. Record one: qa run <url>')
		return 0
	print(f'\n{"TEST":<32} {"RECORDED":<18} STEPS')
	for d in runs:
		hist = d / 'history.json'
		when = datetime.fromtimestamp(hist.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
		steps: int | str
		try:
			steps = len(json.loads(hist.read_text(encoding='utf-8')).get('history', []))
		except (json.JSONDecodeError, OSError):
			steps = '?'
		print(f'{d.name:<32} {when:<18} {steps}')
	print('\nReplay one:  qa replay <name>\nReplay all:  qa replay --all')
	return 0
