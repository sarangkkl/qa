import asyncio
from pathlib import Path

import pytest

from nkqa import planner, scenarios, workspace
from nkqa.config import Config
from nkqa.planner import DraftScenario, DraftStep


def make_draft(slug: str = 'purchase') -> DraftScenario:
	return DraftScenario(
		area='Checkout Flow!',
		slug=slug,
		title='Purchase works',
		steps=[DraftStep(action='Add item to cart.', expect='cart badge shows 1')],
	)


def test_gather_context(tmp_path: Path) -> None:
	ws = workspace.create(tmp_path)
	(ws.appmap_dir / 'pages').mkdir()
	(ws.appmap_dir / 'pages' / 'cart.md').write_text('# Cart page\nHas a coupon field.')
	written, _ = planner.write_drafts(ws, [make_draft()])
	scenarios.approve(written[0], 'T <t@e.c>')

	ctx = planner.gather_context(ws, Config(app_name='Shop', base_url='https://shop.test'))
	assert 'https://shop.test' in ctx
	assert 'Has a coupon field.' in ctx
	assert 'checkout-flow/purchase' in ctx and 'do NOT duplicate' in ctx


def test_write_drafts_stamps_ticket(tmp_path: Path) -> None:
	ws = workspace.create(tmp_path)
	written, _ = planner.write_drafts(ws, [make_draft()], ticket='PROJ-123')
	reloaded = scenarios.find(ws.scenarios_dir, written[0].id)
	assert reloaded is not None and reloaded.ticket == 'PROJ-123'


def test_write_drafts_slugifies_and_skips_existing(tmp_path: Path) -> None:
	ws = workspace.create(tmp_path)
	written, skipped = planner.write_drafts(ws, [make_draft()])
	assert [s.id for s in written] == ['checkout-flow/purchase']
	assert skipped == []
	parsed = scenarios.find(ws.scenarios_dir, 'checkout-flow/purchase')
	assert parsed is not None and parsed.status == 'draft'
	assert parsed.steps[0].expect == 'cart badge shows 1'

	# second write skips, --force overwrites
	written, skipped = planner.write_drafts(ws, [make_draft()])
	assert written == [] and skipped == ['checkout-flow/purchase']
	written, _ = planner.write_drafts(ws, [make_draft()], force=True)
	assert len(written) == 1


def test_a_pasted_url_reaches_jira_as_a_bare_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	"""fetch_issue sends this as `issueIdOrKey` and asserts the key comes back, so a URL fails."""
	from conftest import FakeChannel

	from nkqa import actions
	from nkqa import jira as jira_mod

	ws = workspace.create(tmp_path)
	asked: list[str] = []

	async def fake_read(config: object, ch: object, key: str) -> tuple[str, str]:
		asked.append(key)
		return '', 'stop here'  # the fetch is not what this test is about

	monkeypatch.setattr(jira_mod, 'read_issue', fake_read)
	asyncio.run(actions.plan(ws, FakeChannel(), '', 'https://slrconsulting.atlassian.net/browse/PNY-3689'))

	assert asked == ['PNY-3689']


def test_a_reference_with_no_key_is_refused_before_touching_jira(tmp_path: Path) -> None:
	from conftest import FakeChannel

	from nkqa import actions

	ws = workspace.create(tmp_path)
	ch = FakeChannel()
	assert asyncio.run(actions.plan(ws, ch, '', 'the login page')) == 2
	assert 'No Jira issue key' in ch.out


def test_a_jira_failure_is_an_exit_code_not_an_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	"""It used to propagate out of the handler and kill the task that reports the result."""
	from conftest import FakeChannel

	from nkqa import actions
	from nkqa import jira as jira_mod

	ws = workspace.create(tmp_path)

	async def boom(config: object, ch: object, key: str) -> tuple[str, str]:
		return '', '❌ Could not read PNY-1 (RuntimeError: nope)'

	monkeypatch.setattr(jira_mod, 'read_issue', boom)
	ch = FakeChannel()
	assert asyncio.run(actions.plan(ws, ch, '', 'PNY-1')) == 2
	assert 'Could not read PNY-1' in ch.out
