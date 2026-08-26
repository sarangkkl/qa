from pathlib import Path

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
