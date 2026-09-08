"""Suites and run-over-run comparison: what CI reads, and what the exit code means."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeChannel

from nkqa import scenarios as scenarios_mod
from nkqa import suite as suite_mod
from nkqa import workspace as workspace_mod
from nkqa.config import Config
from nkqa.execution.report import ScenarioResult, StepVerdict, write_results
from nkqa.hitl import HumanInTheLoop
from nkqa.scenarios import Scenario, Step
from nkqa.suite import Comparison, Entry, SuiteResult, compare, select
from nkqa.workspace import Workspace


def make_scenario(ws: Workspace, sid: str, tags: list[str] | None = None, approved: bool = True) -> Scenario:
	area, _, slug = sid.partition('/')
	s = Scenario(
		id=sid,
		path=ws.scenarios_dir / area / f'{slug}.md',
		title=f'{slug} works',
		tags=tags or [],
		steps=[Step('Open the page.', 'it loads')],
	)
	scenarios_mod.save(s)
	if approved:
		scenarios_mod.approve(s, 'tester')
	return scenarios_mod.parse(s.path, ws.scenarios_dir)


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
	return workspace_mod.create(tmp_path)


# --- selection --------------------------------------------------------------


def test_the_suite_is_the_approved_set(ws: Workspace) -> None:
	make_scenario(ws, 'auth/login')
	make_scenario(ws, 'checkout/coupon', approved=False)
	runnable, excluded = select(scenarios_mod.load_all(ws.scenarios_dir))
	assert [s.id for s in runnable] == ['auth/login']
	assert [s.id for s in excluded] == ['checkout/coupon']


def test_tag_filtering_happens_before_the_approval_check(ws: Workspace) -> None:
	"""A draft carrying the tag is reported as excluded, not silently invisible."""
	make_scenario(ws, 'auth/login', tags=['regression'])
	make_scenario(ws, 'checkout/coupon', tags=['regression'], approved=False)
	make_scenario(ws, 'admin/users', tags=['slow'])

	runnable, excluded = select(scenarios_mod.load_all(ws.scenarios_dir), tag='regression')
	assert [s.id for s in runnable] == ['auth/login']
	assert [s.id for s in excluded] == ['checkout/coupon']  # not hidden just because it is a draft


def test_an_edited_scenario_leaves_the_suite(ws: Workspace) -> None:
	s = make_scenario(ws, 'auth/login')
	s.steps[0].action = 'Tampered.'
	scenarios_mod.save(s)
	runnable, excluded = select(scenarios_mod.load_all(ws.scenarios_dir))
	assert runnable == []
	assert excluded[0].runnable() == 'stale'


# --- exit codes -------------------------------------------------------------


def result_with(*verdicts: str) -> SuiteResult:
	return SuiteResult(name='suite--x', entries=[Entry(scenario_id=f's{i}', verdict=v) for i, v in enumerate(verdicts)])


def test_exit_codes() -> None:
	assert result_with('pass', 'pass').exit_code() == 0
	assert result_with('pass', 'fail').exit_code() == 1
	assert result_with('pass', 'blocked').exit_code() == 1  # blocked is not a pass
	assert result_with('pass', 'excluded').exit_code() == 0  # a draft is not a failure
	assert result_with('pass', 'excluded').exit_code(strict=True) == 1  # unless you say so
	assert result_with('excluded').exit_code() == 2  # nothing ran: a usage problem


# --- comparison -------------------------------------------------------------


def test_compare_against_nothing_reports_everything_as_new() -> None:
	current = SuiteResult(entries=[Entry('auth/login', verdict='pass'), Entry('checkout/coupon', verdict='fail')])
	delta = compare(None, current)
	assert delta.added == ['auth/login', 'checkout/coupon']
	assert delta.newly_failing == []  # nothing to regress from


def test_compare_finds_the_line_that_matters_in_ci() -> None:
	previous = SuiteResult(
		entries=[
			Entry('auth/login', verdict='pass'),
			Entry('checkout/coupon', verdict='fail'),
			Entry('admin/users', verdict='fail'),
			Entry('old/thing', verdict='pass'),
		]
	)
	current = SuiteResult(
		entries=[
			Entry('auth/login', verdict='fail'),  # regressed
			Entry('checkout/coupon', verdict='pass'),  # fixed
			Entry('admin/users', verdict='blocked'),  # still bad
			Entry('brand/new', verdict='pass'),
		]
	)
	delta = compare(previous, current)
	assert delta.newly_failing == ['auth/login']
	assert delta.newly_passing == ['checkout/coupon']
	assert delta.still_failing == ['admin/users']
	assert delta.added == ['brand/new']
	assert delta.removed == ['old/thing']


def test_blocked_counts_as_bad_in_both_directions() -> None:
	delta = compare(
		SuiteResult(entries=[Entry('a', verdict='blocked')]), SuiteResult(entries=[Entry('a', verdict='pass')])
	)
	assert delta.newly_passing == ['a']
	delta = compare(
		SuiteResult(entries=[Entry('a', verdict='pass')]), SuiteResult(entries=[Entry('a', verdict='blocked')])
	)
	assert delta.newly_failing == ['a']


def test_an_excluded_scenario_is_not_a_regression() -> None:
	"""Un-approving something is a hole, not a failure - --strict is how you catch it."""
	delta = compare(
		SuiteResult(entries=[Entry('a', verdict='pass')]), SuiteResult(entries=[Entry('a', verdict='excluded')])
	)
	assert delta.newly_failing == [] and delta.removed == []


# --- persistence ------------------------------------------------------------


def test_report_round_trips(ws: Workspace) -> None:
	result = SuiteResult(
		name='suite--20260904-120000',
		started='2026-09-04T12:00:00',
		tag='regression',
		entries=[Entry('auth/login', title='Login works', verdict='pass', run='auth-login--1', seconds=12.4)],
	)
	run_dir = ws.runs_dir / result.name
	suite_mod.write_report(run_dir, result, Comparison(added=['auth/login']))

	again = suite_mod.load_suite(run_dir)
	assert again is not None
	assert again.tag == 'regression' and again.entries[0].seconds == 12.4
	markdown = (run_dir / 'suite.md').read_text()
	assert 'PASS' in markdown and 'auth/login' in markdown and 'regression' in markdown


def test_previous_suite_picks_the_one_before(ws: Workspace) -> None:
	for name in ('suite--20260101-000000', 'suite--20260202-000000', 'suite--20260303-000000'):
		suite_mod.write_report(ws.runs_dir / name, SuiteResult(name=name), Comparison())

	assert suite_mod.previous_suite(ws.runs_dir) is not None
	newest = suite_mod.previous_suite(ws.runs_dir)
	assert newest is not None and newest.name == 'suite--20260303-000000'
	earlier = suite_mod.previous_suite(ws.runs_dir, before='suite--20260303-000000')
	assert earlier is not None and earlier.name == 'suite--20260202-000000'


def test_a_corrupt_suite_file_is_survivable(ws: Workspace) -> None:
	bad = ws.runs_dir / 'suite--broken'
	bad.mkdir(parents=True)
	(bad / 'suite.json').write_text('{not json')
	assert suite_mod.load_suite(bad) is None
	assert suite_mod.previous_suite(ws.runs_dir) is None


# --- running the suite ------------------------------------------------------


def fake_run(verdicts: dict[str, str]) -> Any:
	"""Stands in for scenario_runner: writes the results.json a real run would."""

	async def run_scenario(
		ws: Workspace,
		config: Config,
		hitl: HumanInTheLoop,
		ch: Any,
		scenario: Scenario,
		model: str | None = None,
		stop: Any = None,
	) -> int:
		verdict = verdicts.get(scenario.id, 'pass')
		run_dir = ws.runs_dir / f'{scenario.id.replace("/", "-")}--20260904-120000'
		run_dir.mkdir(parents=True, exist_ok=True)
		steps = [StepVerdict(step=1, verdict='pass' if verdict == 'pass' else 'fail')]
		write_results(run_dir, scenario, ScenarioResult(steps=steps) if verdict != 'blocked' else None)
		return 0 if verdict == 'pass' else 1

	return run_scenario


def run_suite(
	ws: Workspace, monkeypatch: pytest.MonkeyPatch, verdicts: dict[str, str], **kw: Any
) -> tuple[int, FakeChannel]:
	import nkqa.execution.scenario_runner as runner_mod

	monkeypatch.setattr(runner_mod, 'run_scenario', fake_run(verdicts))
	ch = FakeChannel()
	code = asyncio.run(suite_mod.run_suite(ws, Config(), HumanInTheLoop(ws.permissions_file, ch), ch, **kw))
	return code, ch


def test_a_green_suite_exits_zero_and_writes_a_report(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
	make_scenario(ws, 'auth/login')
	make_scenario(ws, 'checkout/coupon')
	code, ch = run_suite(ws, monkeypatch, {})

	assert code == 0
	dirs = suite_mod.suite_dirs(ws.runs_dir)
	assert len(dirs) == 1
	result = suite_mod.load_suite(dirs[0])
	assert result is not None and result.counts == {'pass': 2, 'fail': 0, 'blocked': 0, 'excluded': 0}
	assert all(e.run for e in result.entries)  # every entry links to its evidence
	assert 'passed' in ch.out


def test_a_failing_scenario_fails_the_suite(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
	make_scenario(ws, 'auth/login')
	make_scenario(ws, 'checkout/coupon')
	code, _ = run_suite(ws, monkeypatch, {'checkout/coupon': 'fail'})
	assert code == 1


def test_drafts_are_reported_not_failed(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
	make_scenario(ws, 'auth/login')
	make_scenario(ws, 'checkout/coupon', approved=False)

	code, ch = run_suite(ws, monkeypatch, {})
	assert code == 0
	assert 'never approved' in ch.out  # said out loud, so a half-empty suite is not silent

	strict, _ = run_suite(ws, monkeypatch, {}, strict=True)
	assert strict == 1


def test_tag_narrows_what_runs(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
	make_scenario(ws, 'auth/login', tags=['regression'])
	make_scenario(ws, 'admin/slow-thing', tags=['nightly'])
	code, _ = run_suite(ws, monkeypatch, {}, tag='regression')

	assert code == 0
	result = suite_mod.load_suite(suite_mod.suite_dirs(ws.runs_dir)[0])
	assert result is not None
	assert [e.scenario_id for e in result.entries] == ['auth/login']
	assert result.tag == 'regression'


def test_an_empty_workspace_is_a_usage_error(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
	code, ch = run_suite(ws, monkeypatch, {})
	assert code == 2 and 'No scenarios' in ch.out


def test_the_second_suite_reports_what_regressed(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
	make_scenario(ws, 'auth/login')
	run_suite(ws, monkeypatch, {})
	code, ch = run_suite(ws, monkeypatch, {'auth/login': 'fail'})

	assert code == 1
	assert 'Newly failing' in ch.out and 'auth/login' in ch.out
	newest = suite_mod.suite_dirs(ws.runs_dir)[-1]
	assert 'newly failing' in (newest / 'suite.md').read_text()


def test_compare_command_defaults_to_the_two_newest(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
	make_scenario(ws, 'auth/login')
	run_suite(ws, monkeypatch, {})
	run_suite(ws, monkeypatch, {'auth/login': 'fail'})

	ch = FakeChannel()
	code = asyncio.run(suite_mod.compare_suites(ws, ch))
	assert code == 1 and 'newly failing' in ch.out and 'auth/login' in ch.out


def test_compare_needs_two_suites(ws: Workspace) -> None:
	ch = FakeChannel()
	assert asyncio.run(suite_mod.compare_suites(ws, ch)) == 2
	assert 'Need two recorded suites' in ch.out


def test_compare_reports_no_change(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
	make_scenario(ws, 'auth/login')
	run_suite(ws, monkeypatch, {})
	run_suite(ws, monkeypatch, {})

	ch = FakeChannel()
	assert asyncio.run(suite_mod.compare_suites(ws, ch)) == 0
	assert 'No change.' in ch.out


def test_suite_json_is_machine_readable(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
	"""CI reads this file, so its shape is a contract."""
	make_scenario(ws, 'auth/login')
	run_suite(ws, monkeypatch, {'auth/login': 'fail'})

	raw = json.loads((suite_mod.suite_dirs(ws.runs_dir)[0] / 'suite.json').read_text())
	assert set(raw) == {'name', 'started', 'tag', 'entries'}
	assert set(raw['entries'][0]) == {'scenario_id', 'title', 'state', 'verdict', 'run', 'seconds', 'reason'}


def test_two_suites_in_the_same_second_do_not_collide(ws: Workspace) -> None:
	"""A fast suite run twice would otherwise overwrite its own history."""
	from datetime import datetime

	at = datetime(2026, 9, 4, 12, 0, 0)
	first = suite_mod.suite_name(ws.runs_dir, at)
	suite_mod.write_report(ws.runs_dir / first, SuiteResult(name=first), Comparison())
	second = suite_mod.suite_name(ws.runs_dir, at)
	suite_mod.write_report(ws.runs_dir / second, SuiteResult(name=second), Comparison())

	assert first != second
	assert len(suite_mod.suite_dirs(ws.runs_dir)) == 2


def test_a_stopped_suite_does_not_run_the_rest_and_fails(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
	"""Stopping one scenario used to just move on to the next - the opposite of Stop.

	The exit code matters as much as the skipping: leftovers are recorded as `excluded`,
	and passes-plus-excluded would otherwise add up to a green suite.
	"""
	import nkqa.execution.scenario_runner as runner_mod
	from nkqa.stop import StopSignal

	make_scenario(ws, 'auth/login')
	make_scenario(ws, 'checkout/coupon')

	signal = StopSignal()
	inner = fake_run({})
	ran: list[str] = []

	async def run_and_stop(*args: Any, **kwargs: Any) -> int:
		scenario = args[4]
		ran.append(scenario.id)
		signal.stop()  # as if the human hit Stop during the first scenario
		return await inner(*args, **kwargs)

	monkeypatch.setattr(runner_mod, 'run_scenario', run_and_stop)
	ch = FakeChannel()
	code = asyncio.run(suite_mod.run_suite(ws, Config(), HumanInTheLoop(ws.permissions_file, ch), ch, stop=signal))

	assert len(ran) == 1, 'the suite must not start another scenario after a stop'
	assert code == 1, 'a stopped suite is not a passing suite'
	assert 'Suite stopped' in ch.out
