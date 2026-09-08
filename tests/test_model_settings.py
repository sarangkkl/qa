"""Choosing the provider and models: the config rewrite, the action, and its guards.

The rewrite is the part worth pinning. config.yaml is a file humans also edit, and the whole
reason this does a surgical line edit instead of a yaml round-trip is that a round-trip would
silently delete every comment in it - including the ones explaining what each role is for.
"""

import asyncio
from pathlib import Path

import pytest
from conftest import FakeChannel

from nkqa import actions
from nkqa import config as config_mod
from nkqa import workspace as workspace_mod
from nkqa.models import CATALOGUE, DEFAULTS, TIERS, qualify, split_model
from nkqa.shell.commands import REGISTRY, agent_commands
from nkqa.workspace import Workspace


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
	return workspace_mod.create(tmp_path)


def test_the_rewrite_keeps_every_comment_and_every_other_block(ws: Workspace) -> None:
	before = ws.config_file.read_text()
	after = config_mod.set_aliases(before, {'smart': 'openai:gpt-5.1', 'fast': 'openai:gpt-5.1-mini'})

	assert '# mixing providers is fine, e.g.:' in after
	assert '# learn from every run' in after
	# Every line that is not one of the two being set survives byte for byte.
	changed = [b for b, a in zip(before.splitlines(), after.splitlines(), strict=True) if a != b]
	assert len(changed) == 2

	cfg = config_mod.load(_write(ws, after))
	assert cfg.aliases == {'smart': 'openai:gpt-5.1', 'fast': 'openai:gpt-5.1-mini'}
	assert cfg.max_steps == 30 and cfg.crawl_pages == 15  # the other blocks still parse


def test_only_the_aliases_block_is_touched() -> None:
	"""`models:` has a `planner: smart` line; a blind search-and-replace would eat keys there."""
	text = 'models:\n  smart: not-a-tier\n  planner: smart\n\naliases:\n  smart: anthropic_claude_sonnet_5\n'
	after = config_mod.set_aliases(text, {'smart': 'openai:gpt-5'})

	assert 'models:\n  smart: not-a-tier\n  planner: smart' in after
	assert after.count('openai:gpt-5') == 1


def test_a_key_that_is_not_there_yet_is_added(ws: Workspace) -> None:
	text = 'aliases:\n  smart: anthropic_claude_sonnet_5\n'
	assert config_mod.load(_write(ws, config_mod.set_aliases(text, {'fast': 'openai:gpt-5-mini'}))).aliases == {
		'smart': 'anthropic_claude_sonnet_5',
		'fast': 'openai:gpt-5-mini',
	}


def test_a_config_with_no_aliases_block_at_all_grows_one(ws: Workspace) -> None:
	text = 'app:\n  name: Thing\n'
	after = config_mod.set_aliases(text, {'smart': 'openai:gpt-5.1'})
	cfg = config_mod.load(_write(ws, after))
	assert cfg.aliases['smart'] == 'openai:gpt-5.1'
	assert cfg.app_name == 'Thing'


@pytest.mark.parametrize('bad', ['claude\nheadless: true', 'gpt 5', '', '../../etc/passwd', '"quoted"'])
def test_a_model_id_that_could_corrupt_the_file_is_refused(bad: str) -> None:
	"""This string is written into config.yaml, so a newline in it is a rewritten workspace."""
	with pytest.raises(ValueError):
		qualify('openai', bad)


def test_an_unknown_provider_is_refused_by_name() -> None:
	with pytest.raises(ValueError, match='anthropic, openai'):
		qualify('deepmind', 'some-model')


def test_an_already_qualified_id_carries_its_own_provider() -> None:
	assert qualify('anthropic', 'openai:gpt-5.1') == 'openai:gpt-5.1'


def test_both_name_forms_read_back_to_the_same_pair() -> None:
	"""The settings page has to show a config written by hand in browser-use naming."""
	assert split_model('anthropic_claude_sonnet_5') == ('anthropic', 'claude-sonnet-5')
	assert split_model('openai:gpt-5.1-mini') == ('openai', 'gpt-5.1-mini')


def test_choosing_a_provider_alone_moves_both_tiers(ws: Workspace) -> None:
	ch = FakeChannel()
	assert asyncio.run(actions.set_model(ws, ch, 'openai')) == 0

	aliases = config_mod.load(ws.config_file).aliases
	assert split_model(aliases['smart'])[0] == 'openai'
	assert split_model(aliases['fast'])[0] == 'openai'
	assert aliases['smart'] != aliases['fast'], 'the smart/fast split is the point of the tiers'


def test_one_tier_can_be_narrowed_without_naming_the_provider(ws: Workspace) -> None:
	asyncio.run(actions.set_model(ws, FakeChannel(), 'anthropic'))
	assert asyncio.run(actions.set_model(ws, FakeChannel(), '', 'claude-opus-5')) == 0

	aliases = config_mod.load(ws.config_file).aliases
	assert aliases['smart'] == 'anthropic:claude-opus-5'
	assert aliases['fast'] == 'anthropic:claude-haiku-4-5', 'the untouched tier must not move'


def test_a_model_id_that_is_not_in_the_catalogue_still_works(ws: Workspace) -> None:
	"""The catalogue is what settings offers, not what it accepts - it will go stale."""
	assert asyncio.run(actions.set_model(ws, FakeChannel(), 'anthropic', 'claude-not-released-yet')) == 0
	assert config_mod.load(ws.config_file).aliases['smart'] == 'anthropic:claude-not-released-yet'


def test_a_bad_choice_is_usage_and_changes_nothing(ws: Workspace) -> None:
	before = ws.config_file.read_text()
	ch = FakeChannel()
	assert asyncio.run(actions.set_model(ws, ch, 'deepmind')) == 2
	assert ws.config_file.read_text() == before


def test_saying_nothing_lists_the_choices_instead_of_writing(ws: Workspace) -> None:
	before = ws.config_file.read_text()
	ch = FakeChannel()
	assert asyncio.run(actions.set_model(ws, ch)) == 2
	assert 'anthropic' in ch.out and 'openai' in ch.out
	assert ws.config_file.read_text() == before


def test_a_missing_key_is_reported_but_is_not_a_failed_save(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
	"""Otherwise switching provider before pasting the key renders as "the change failed"."""
	monkeypatch.delenv('OPENAI_API_KEY', raising=False)
	ch = FakeChannel()
	assert asyncio.run(actions.set_model(ws, ch, 'openai')) == 0
	assert 'OPENAI_API_KEY' in ch.out
	assert config_mod.load(ws.config_file).aliases['smart'].startswith('openai:')


def test_a_role_pointed_straight_at_a_model_is_named_not_silently_left_behind(ws: Workspace) -> None:
	ws.config_file.write_text(ws.config_file.read_text().replace('planner: smart', 'planner: openai:gpt-5'))
	ch = FakeChannel()
	asyncio.run(actions.set_model(ws, ch, 'anthropic'))

	assert 'planner' in ch.out and 'did not move' in ch.out
	assert config_mod.load(ws.config_file).models['planner'] == 'openai:gpt-5'


def test_the_report_reads_the_workspace_it_was_given(ws: Workspace, tmp_path: Path) -> None:
	"""The sidecar's cwd is wherever it was launched, so find() was reporting the wrong config."""
	asyncio.run(actions.set_model(ws, FakeChannel(), 'openai'))
	elsewhere = tmp_path / 'elsewhere'
	elsewhere.mkdir()
	import os

	cwd = Path.cwd()
	try:
		os.chdir(elsewhere)
		ch = FakeChannel()
		asyncio.run(actions.models(ch, ws))
	finally:
		os.chdir(cwd)
	assert f'openai:{DEFAULTS["openai"]["smart"]}' in ch.out


def test_the_agent_cannot_choose_its_own_model() -> None:
	"""Same gate as approve and the vault writes: it must not pick what it is judged with."""
	assert REGISTRY['set-model'].human_only is True
	assert 'set-model' not in {c.name for c in agent_commands()}
	assert 'models' in {c.name for c in agent_commands()}  # reading the setup is fine


def test_settings_works_during_a_run() -> None:
	"""Noticing the model is wrong is something that happens while it is running."""
	assert REGISTRY['set-model'].instant is True


def test_every_offered_model_names_a_tier() -> None:
	"""The provider dropdown picks both tiers from this; a missing one is a broken switch."""
	for provider, entries in CATALOGUE.items():
		tiers = {e['tier'] for e in entries}
		assert {'smart', 'fast'} <= tiers, f'{provider} cannot fill both tiers'


def test_every_default_is_a_model_the_picker_actually_offers() -> None:
	"""The catalogue shipped `gpt-5.1-mini` as openai's default fast model. The API 404s on it.

	A default is the one entry nobody chooses deliberately, so a wrong one is invisible until
	every executor and chat call fails. Being "presentation, not validation" excuses a model
	missing from the list; it does not excuse pointing the default at one that is not in it.
	"""
	assert set(DEFAULTS) == set(CATALOGUE), 'every provider needs defaults, and vice versa'
	for provider, picks in DEFAULTS.items():
		by_id = {e['id']: e for e in CATALOGUE[provider]}
		assert set(picks) == set(TIERS), f'{provider} must default both tiers'
		for tier, model_id in picks.items():
			assert model_id in by_id, f'{provider} defaults {tier} to "{model_id}", which it does not offer'
			assert by_id[model_id]['tier'] == tier, (
				f'{provider}:{model_id} is offered as a {by_id[model_id]["tier"]} model'
			)


def test_the_catalogue_is_well_formed() -> None:
	for provider, entries in CATALOGUE.items():
		ids = [e['id'] for e in entries]
		assert len(ids) == len(set(ids)), f'{provider} lists a model twice'
		for entry in entries:
			assert entry['id'] and entry['label'], f'{provider} has an entry missing an id or label'
			assert entry['tier'] in TIERS, f'{provider}:{entry["id"]} has tier "{entry["tier"]}"'
			qualify(provider, entry['id'])  # every offered id must survive being written to config.yaml


def _write(ws: Workspace, text: str) -> Path:
	ws.config_file.write_text(text)
	return ws.config_file
