import asyncio
from pathlib import Path

from nkqa import scenarios, workspace
from nkqa.config import Config
from nkqa.execution import report
from nkqa.execution.report import ScenarioResult, StepVerdict
from nkqa.execution.scenario_runner import build_task, run_scenario
from nkqa.hitl import HumanInTheLoop
from nkqa.scenarios import Scenario, Step


def make_scenario(tmp_path: Path, approved: bool = False) -> Scenario:
	s = Scenario(
		id='checkout/coupon',
		path=tmp_path / 'scenarios' / 'checkout' / 'coupon.md',
		title='Coupon works',
		preconditions=['test user exists'],
		steps=[Step('Log in.', 'dashboard visible'), Step('Apply coupon.', 'total drops')],
		out_of_scope=['real payments'],
	)
	scenarios.save(s)
	if approved:
		scenarios.approve(s, 'Tester <t@example.com>')
	return s


def test_overall_matrix() -> None:
	ok = ScenarioResult(steps=[StepVerdict(step=1, verdict='pass'), StepVerdict(step=2, verdict='pass')])
	assert report.overall(ok, 2) == 'pass'
	assert report.overall(None, 2) == 'blocked'
	missing = ScenarioResult(steps=[StepVerdict(step=1, verdict='pass')])
	assert report.overall(missing, 2) == 'blocked'  # silent omission is never a pass
	failed = ScenarioResult(steps=[StepVerdict(step=1, verdict='fail'), StepVerdict(step=2, verdict='blocked')])
	assert report.overall(failed, 2) == 'fail'  # fail outranks blocked


def test_write_results_and_last_verdict(tmp_path: Path) -> None:
	s = make_scenario(tmp_path, approved=True)
	run_dir = tmp_path / 'runs' / 'checkout-coupon--20260826-1200'
	run_dir.mkdir(parents=True)
	result = ScenarioResult(
		steps=[StepVerdict(step=1, verdict='pass'), StepVerdict(step=2, verdict='fail', note='total | unchanged')],
		summary='Coupon is broken.',
	)
	verdict = report.write_results(run_dir, s, result)
	assert verdict == 'fail'
	text = (run_dir / 'results.md').read_text()
	assert text.splitlines()[0].startswith('# checkout/coupon — FAIL —')
	assert 'total \\| unchanged' in text  # pipes escaped in table cells
	assert 'Coupon is broken.' in text
	assert report.last_verdict(tmp_path / 'runs', 'checkout/coupon') == 'FAIL'
	assert report.last_verdict(tmp_path / 'runs', 'never/ran') == ''


def test_build_task_contains_everything(tmp_path: Path) -> None:
	s = make_scenario(tmp_path)
	task = build_task(s, 'https://shop.test')
	assert 'https://shop.test' in task
	assert '1. Log in. EXPECT: dashboard visible' in task
	assert 'OUT OF SCOPE' in task and 'real payments' in task
	assert 'blocked, not failed' in task
	assert 'ALREADY KNOW' not in task  # no appmap, no context block


def test_build_task_carries_app_knowledge(tmp_path: Path) -> None:
	s = make_scenario(tmp_path)
	task = build_task(s, 'https://shop.test', '--- appmap/flows/login.md ---\nsign in at /login')
	assert 'sign in at /login' in task
	assert 'reference, not steps to execute' in task


def test_run_scenario_refuses_unapproved(tmp_path: Path) -> None:
	ws = workspace.create(tmp_path)
	hitl = HumanInTheLoop(ws.permissions_file)
	draft = make_scenario(tmp_path)
	assert asyncio.run(run_scenario(ws, Config(), hitl, draft)) == 2

	approved = make_scenario(tmp_path, approved=True)
	approved.steps[0].action = 'Tampered.'
	scenarios.save(approved)
	stale = scenarios.parse(approved.path, ws.scenarios_dir)
	assert stale.runnable() == 'stale'
	assert asyncio.run(run_scenario(ws, Config(), hitl, stale)) == 2
	assert not any(ws.runs_dir.glob('checkout-coupon--*'))  # refusal leaves no run dir
