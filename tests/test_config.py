from pathlib import Path

from nkqa import config, models


def test_defaults_when_missing(tmp_path: Path) -> None:
	cfg = config.load(tmp_path / 'nope.yaml')
	assert cfg.max_steps == 30
	assert cfg.headless is False
	assert cfg.models['executor'] == 'default'
	assert cfg.aliases['fast'] == 'anthropic_claude_haiku_4_5'


def test_load_merges_partial_file(tmp_path: Path) -> None:
	f = tmp_path / 'config.yaml'
	f.write_text('app:\n  name: Shop\nmodels:\n  executor: fast\nrun:\n  max_steps: 5\n')
	cfg = config.load(f)
	assert cfg.app_name == 'Shop'
	assert cfg.models['executor'] == 'fast'
	assert cfg.models['fallback'] == 'fast'  # untouched default survives
	assert cfg.max_steps == 5


def test_template_parses_to_defaults(tmp_path: Path) -> None:
	f = tmp_path / 'config.yaml'
	f.write_text(config.CONFIG_TEMPLATE)
	cfg = config.load(f)
	assert cfg.models == config.DEFAULT_MODELS
	assert cfg.aliases == config.DEFAULT_ALIASES


def test_model_name_resolution() -> None:
	cfg = config.Config()
	assert models.model_name(cfg, 'executor') is None  # 'default' -> browser-use decides
	assert models.model_name(cfg, 'fallback') == 'anthropic_claude_haiku_4_5'
	assert models.model_name(cfg, 'executor', override='smart') == 'anthropic_claude_sonnet_5'
	assert models.model_name(cfg, 'executor', override='openai_gpt_4_1') == 'openai_gpt_4_1'  # raw name passes through
	assert models.model_name(cfg, 'executor', override='default') is None
