from pathlib import Path

import pytest

from nkqa import scenarios
from nkqa.scenarios import Scenario, Step

SAMPLE = """\
---
title: Purchase with a coupon
status: draft
ticket: PROJ-123
tags: [checkout, regression]
preconditions:
  - a test user exists
---

## Steps
1. Log in as the test user.
2. Apply coupon `SAVE10`.
   - **Expect:** total drops by 10%.

## Out of scope
- Real payment capture.
"""


def write_sample(tmp_path: Path) -> Path:
	path = tmp_path / 'scenarios' / 'checkout' / 'coupon.md'
	path.parent.mkdir(parents=True)
	path.write_text(SAMPLE)
	return path


def test_parse(tmp_path: Path) -> None:
	s = scenarios.parse(write_sample(tmp_path), tmp_path / 'scenarios')
	assert s.id == 'checkout/coupon'
	assert s.title == 'Purchase with a coupon'
	assert s.status == 'draft'
	assert s.tags == ['checkout', 'regression']
	assert s.preconditions == ['a test user exists']
	assert [st.action for st in s.steps] == ['Log in as the test user.', 'Apply coupon `SAVE10`.']
	assert s.steps[1].expect == 'total drops by 10%.'
	assert s.out_of_scope == ['Real payment capture.']


def test_roundtrip_preserves_hash(tmp_path: Path) -> None:
	sdir = tmp_path / 'scenarios'
	s = scenarios.parse(write_sample(tmp_path), sdir)
	original = s.content_hash()
	scenarios.save(s)
	assert scenarios.parse(s.path, sdir).content_hash() == original


def test_hash_ignores_volatile_keys(tmp_path: Path) -> None:
	sdir = tmp_path / 'scenarios'
	s = scenarios.parse(write_sample(tmp_path), sdir)
	before = s.content_hash()
	s.status = 'approved'
	s.approved_by = 'someone'
	s.approved_at = '2026-01-01T00:00:00'
	assert s.content_hash() == before
	s.steps[0].action = 'Something else.'
	assert s.content_hash() != before


def test_runnable_lifecycle(tmp_path: Path) -> None:
	sdir = tmp_path / 'scenarios'
	s = scenarios.parse(write_sample(tmp_path), sdir)
	assert s.runnable() == 'draft'

	scenarios.approve(s, 'Tester <t@example.com>')
	reloaded = scenarios.parse(s.path, sdir)
	assert reloaded.runnable() == 'ok'
	assert reloaded.approved_by == 'Tester <t@example.com>'

	# edit after approval -> stale
	reloaded.steps[0].action = 'Tampered.'
	scenarios.save(reloaded)
	assert scenarios.parse(s.path, sdir).runnable() == 'stale'

	# re-approve clears staleness
	scenarios.approve(scenarios.parse(s.path, sdir), 'Tester <t@example.com>')
	assert scenarios.parse(s.path, sdir).runnable() == 'ok'

	stale = scenarios.parse(s.path, sdir)
	stale.status = 'deprecated'
	scenarios.save(stale)
	assert scenarios.parse(s.path, sdir).runnable() == 'deprecated'


def test_find_and_load_all(tmp_path: Path) -> None:
	sdir = tmp_path / 'scenarios'
	write_sample(tmp_path)
	assert scenarios.find(sdir, 'checkout/coupon') is not None
	assert scenarios.find(sdir, 'nope') is None
	assert [s.id for s in scenarios.load_all(sdir)] == ['checkout/coupon']
	assert scenarios.load_all(tmp_path / 'missing') == []


def test_parse_rejects_missing_frontmatter(tmp_path: Path) -> None:
	path = tmp_path / 'scenarios' / 'bad.md'
	path.parent.mkdir()
	path.write_text('just text')
	with pytest.raises(ValueError):
		scenarios.parse(path, tmp_path / 'scenarios')


def test_serialize_new_scenario(tmp_path: Path) -> None:
	s = Scenario(
		id='auth/login',
		path=tmp_path / 'scenarios' / 'auth' / 'login.md',
		title='Login works',
		steps=[Step('Open the login page.', 'form is visible'), Step('Submit valid credentials.')],
	)
	scenarios.save(s)
	parsed = scenarios.parse(s.path, tmp_path / 'scenarios')
	assert parsed.title == 'Login works'
	assert parsed.steps[0].expect == 'form is visible'
	assert parsed.content_hash() == s.content_hash()
