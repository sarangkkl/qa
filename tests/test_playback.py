"""Library recordings: finding elements again, mapping steps to verdicts, and who gets in."""

from pathlib import Path
from typing import Any

from nkqa import library
from nkqa.execution import checks
from nkqa.execution.driver import StepRecord, with_unique
from nkqa.execution.playback import from_run, owners, rebase, verdicts
from nkqa.scenarios import Scenario, Step


class Node:
	"""An element as the selector map holds it, with just what the matcher reads."""

	def __init__(self, h: int, stable: int = 0, xpath: str = '', ax: str = '', name: str = 'button', **attrs: str):
		self.h, self.stable, self.xpath, self.node_name, self.attributes = h, stable, xpath, name, attrs
		self.ax_node = type('Ax', (), {'name': ax})() if ax else None
		self.frame_id = None

	def __hash__(self) -> int:
		return self.h

	def compute_stable_hash(self) -> int:
		return self.stable


def fp(**kw: Any) -> dict[str, Any]:
	base: dict[str, Any] = {
		'node_name': 'BUTTON',
		'element_hash': -1,
		'stable_hash': None,
		'x_path': '',
		'ax_name': None,
	}
	return {**base, **kw}


def test_the_matcher_prefers_the_strictest_level_that_matches() -> None:
	page = {1: Node(10, 20, '/a'), 2: Node(11, 21, '/b', ax='Save'), 3: Node(12, 22, '/c', id='go')}
	assert checks.locate(fp(element_hash=11), page) == (2, 'exact')
	assert checks.locate(fp(stable_hash=22), page) == (3, 'stable')  # a class changed, the rest did not
	assert checks.locate(fp(x_path='/a'), page) == (1, 'xpath')
	assert checks.locate(fp(ax_name='Save'), page) == (2, 'name')
	assert checks.locate(fp(attributes={'id': 'go'}), page) == (3, 'attribute id')
	assert checks.locate(fp(ax_name='Delete'), page) == (None, '')


def test_an_ambiguous_attribute_is_not_a_match() -> None:
	page = {1: Node(1, name='input', name_attr='x'), 2: Node(2, name='input')}
	page[1].attributes = {'name': 'q'}
	page[2].attributes = {'name': 'q'}
	assert checks.locate(fp(attributes={'name': 'q'}), page) == (None, '')


def test_text_is_compared_the_way_a_person_reads_it() -> None:
	assert checks.norm('  SAVE\n  changes ') == checks.norm('Save changes')


def rec(action: str, **params: Any) -> StepRecord:
	return StepRecord(n=0, action=action, params=params)


def test_each_recorded_step_belongs_to_the_check_after_it() -> None:
	steps = [rec('navigate'), rec('check', step=1), rec('input'), rec('click'), rec('check', step=2), rec('scroll')]
	assert owners(steps, 3) == [1, 1, 2, 2, 2, 3]


def test_a_failure_fails_its_step_and_leaves_the_rest_not_run() -> None:
	s = Scenario(id='a/b', path=Path('x.md'), title='t', steps=[Step('one'), Step('two'), Step('three')])
	got = verdicts(s, 2, 'could not find <button> "Save"', [1, 1, 2, 2, 3])
	assert [(v.step, v.verdict) for v in got] == [(1, 'pass'), (2, 'fail'), (3, 'blocked')]
	assert 'Save' in got[1].note and 'stopped at step 2' in got[2].note
	assert all(v.verdict == 'pass' for v in verdicts(s, None, '', [1, 2, 3]))


def test_only_what_worked_is_replayed() -> None:
	steps = [rec('state'), rec('click'), StepRecord(n=3, action='click', error='stale'), rec('check', step=1)]
	assert [s.action for s in from_run(steps)] == ['click', 'check']


def test_navigation_moves_with_the_base_url() -> None:
	assert rebase('https://dev.app/login?x=1', 'https://dev.app/', 'https://uat.app') == 'https://uat.app/login?x=1'
	assert rebase('https://elsewhere.com/', 'https://dev.app', 'https://uat.app') == 'https://elsewhere.com/'


def test_unique_is_stamped_only_where_it_is_asked_for() -> None:
	assert with_unique({'text': 'QA {{unique}}', 'index': 3}, '0930-1200') == {'text': 'QA 0930-1200', 'index': 3}


def test_only_a_proven_passing_claude_run_can_join_the_library(tmp_path: Path) -> None:
	from nkqa import scenarios as scenarios_mod

	s = Scenario(id='a/b', path=tmp_path / 'b.md', title='t', steps=[Step('open', 'page shows'), Step('click')])
	scenarios_mod.approve(s, 'qa')
	h = s.approved_hash
	assert library.refusal(s, 'pass', h, False, {1}) == ''
	assert 'did not pass' in library.refusal(s, 'fail', h, False, {1})
	assert 'replay' in library.refusal(s, 'pass', h, True, {1})
	assert 'edited' in library.refusal(s, 'pass', 'other', False, {1})
	assert 'no checks' in library.refusal(s, 'pass', h, False, set())  # a run from before checks existed
	s2 = Scenario(id='a/c', path=tmp_path / 'c.md', title='t', steps=[Step('open', 'shows'), Step('go', 'lands')])
	scenarios_mod.approve(s2, 'qa')
	assert 'step(s) 2' in library.refusal(s2, 'pass', s2.approved_hash, False, {1})
