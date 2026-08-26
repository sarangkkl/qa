import json
from pathlib import Path

import pytest

from nkqa import config as config_mod
from nkqa import jira, scenarios, workspace
from nkqa.config import Config, MCPServer
from nkqa.execution.report import RunRecord, ScenarioResult, StepVerdict, read_results, write_results
from nkqa.scenarios import Scenario, Step


def test_jira_server_requires_config() -> None:
	with pytest.raises(RuntimeError, match='mcp-remote'):
		jira.jira_server(Config())
	cfg = Config(mcp_servers=[MCPServer(name='jira', command='npx')])
	assert jira.jira_server(cfg).name == 'jira'


def test_tool_name_overrides() -> None:
	spec = MCPServer(name='jira', command='npx', tools={'get_issue': 'jira_get'})
	assert jira._tool(spec, 'get_issue') == 'jira_get'  # pyright: ignore[reportPrivateUsage]
	assert jira._tool(spec, 'create_issue') == 'createJiraIssue'  # pyright: ignore[reportPrivateUsage]


def make_scenario(tmp_path: Path) -> Scenario:
	s = Scenario(
		id='checkout/coupon',
		path=tmp_path / 'scenarios' / 'checkout' / 'coupon.md',
		title='Coupon works',
		ticket='PROJ-9',
		steps=[Step('Log in.', 'dashboard visible'), Step('Apply coupon.', 'total drops 10%')],
	)
	scenarios.save(s)
	scenarios.approve(s, 'T <t@e.c>')
	return scenarios.parse(s.path, tmp_path / 'scenarios')


def failed_record(scenario: Scenario, tmp_path: Path) -> tuple[RunRecord, Path]:
	run_dir = tmp_path / 'runs' / 'checkout-coupon--20260826-1200'
	run_dir.mkdir(parents=True)
	result = ScenarioResult(
		steps=[StepVerdict(step=1, verdict='pass'), StepVerdict(step=2, verdict='fail', note='total unchanged at $50')]
	)
	write_results(run_dir, scenario, result)
	record = read_results(run_dir)
	assert record is not None
	return record, run_dir


def test_results_json_roundtrip(tmp_path: Path) -> None:
	s = make_scenario(tmp_path)
	record, run_dir = failed_record(s, tmp_path)
	assert record.scenario_id == 'checkout/coupon'
	assert record.verdict == 'fail'
	assert json.loads((run_dir / 'results.json').read_text())['verdict'] == 'fail'


def test_compose_bug(tmp_path: Path) -> None:
	s = make_scenario(tmp_path)
	record, run_dir = failed_record(s, tmp_path)
	failures = jira.failed_steps(record)
	assert [f.step for f in failures] == [2]
	summary, description = jira.compose_bug(s, record, failures[0], run_dir, 'https://shop.test')
	assert summary.startswith('[checkout/coupon] step 2 fails: Apply coupon.')
	assert 'Steps to reproduce:' in description
	assert '1. Log in.' in description and '2. Apply coupon.' in description
	assert 'Expected: total drops 10%' in description
	assert 'Actual: total unchanged at $50' in description
	assert 'Related ticket: PROJ-9' in description
	assert str(run_dir) in description


def test_cli_file_bug_guards(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
	from tests.test_cli import run_cli

	monkeypatch.chdir(tmp_path)
	ws = workspace.create(tmp_path)
	assert run_cli(monkeypatch, ['file-bug', 'nope--123']) == 2  # unknown run

	s = make_scenario(tmp_path)
	_, run_dir = failed_record(s, tmp_path)
	ws.config_file.write_text(ws.config_file.read_text() + '\nmcp:\n  jira:\n    command: npx\n')
	monkeypatch.setattr('builtins.input', lambda _prompt='': 'n')
	assert run_cli(monkeypatch, ['file-bug', run_dir.name, '--project', 'PROJ']) == 1  # declined before any MCP

	# all-pass run -> nothing to file
	ok_dir = tmp_path / 'runs' / 'checkout-coupon--20260826-1300'
	ok_dir.mkdir()
	write_results(
		ok_dir, s, ScenarioResult(steps=[StepVerdict(step=1, verdict='pass'), StepVerdict(step=2, verdict='pass')])
	)
	assert run_cli(monkeypatch, ['file-bug', ok_dir.name]) == 0

	# missing project key
	assert run_cli(monkeypatch, ['file-bug', run_dir.name]) == 2
	assert config_mod.load(ws.config_file).jira_project == ''
