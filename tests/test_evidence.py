"""Two recorders, one run list: the Agent's history.json and the MCP driver's steps.json."""

import asyncio
import json
from pathlib import Path

from conftest import FakeChannel

from nkqa.execution import evidence

STEP: dict[str, str] = {}


def _run(runs: Path, name: str, file: str, payload: object) -> Path:
	d = runs / name
	d.mkdir(parents=True)
	(d / file).write_text(json.dumps(payload))
	return d


def test_both_recorders_are_listed_and_counted(tmp_path: Path) -> None:
	runs = tmp_path / 'runs'
	agent = _run(runs, 'agent-run', 'history.json', {'history': [STEP, STEP, STEP]})
	driven = _run(runs, 'mcp-run', 'steps.json', {'steps': [STEP, STEP]})
	(runs / 'empty').mkdir()

	listed = evidence.recorded_runs(runs)
	assert {d.name for d in listed} == {'agent-run', 'mcp-run'}
	assert evidence.recording_file(agent) == agent / 'history.json'
	assert evidence.recording_file(driven) == driven / 'steps.json'
	assert evidence.recording_file(runs / 'empty') is None
	assert evidence.step_count(agent / 'history.json') == 3
	assert evidence.step_count(driven / 'steps.json') == 2


def test_history_wins_when_both_exist(tmp_path: Path) -> None:
	d = _run(tmp_path / 'runs', 'x', 'history.json', {'history': [STEP]})
	(d / 'steps.json').write_text(json.dumps({'steps': [STEP, STEP, STEP]}))
	assert evidence.recording_file(d) == d / 'history.json'


def test_list_runs_shows_driver_runs(tmp_path: Path) -> None:
	runs = tmp_path / 'runs'
	_run(runs, 'mcp-run', 'steps.json', {'steps': [STEP]})
	ch = FakeChannel()
	assert asyncio.run(evidence.list_runs(ch, runs)) == 0
	assert 'mcp-run' in ch.out
	assert any(e.data.get('steps') == 1 for e in ch.events)


def test_garbage_recording_counts_as_unknown(tmp_path: Path) -> None:
	d = _run(tmp_path / 'runs', 'x', 'steps.json', [])  # a list, not the expected object
	assert evidence.step_count(d / 'steps.json') == '?'
