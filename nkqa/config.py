"""config.yaml loading. Missing file or keys fall back to prototype-equivalent defaults."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# 'default' means: let browser-use pick its own default model (what the prototype did).
DEFAULT_MODELS = {'planner': 'smart', 'executor': 'default', 'reflector': 'fast', 'fallback': 'fast'}
DEFAULT_ALIASES = {'smart': 'anthropic_claude_sonnet_5', 'fast': 'anthropic_claude_haiku_4_5'}

CONFIG_TEMPLATE = """\
app:
  name: My App
  base_url: https://example.com

# Model roles. Values are aliases (defined below) or raw browser-use model names
# like anthropic_claude_haiku_4_5. 'default' lets browser-use pick its default model.
models:
  planner: smart
  executor: default
  reflector: fast
  fallback: fast

aliases:
  smart: anthropic_claude_sonnet_5
  fast: anthropic_claude_haiku_4_5

run:
  max_steps: 30
  headless: false
"""


@dataclass
class Config:
	app_name: str = 'My App'
	base_url: str = ''
	models: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_MODELS))
	aliases: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_ALIASES))
	max_steps: int = 30
	headless: bool = False


def load(config_file: Path | None) -> Config:
	cfg = Config()
	if config_file is None or not config_file.is_file():
		return cfg
	data: dict[str, Any] = yaml.safe_load(config_file.read_text(encoding='utf-8')) or {}
	app: dict[str, Any] = data.get('app') or {}
	run: dict[str, Any] = data.get('run') or {}
	models_raw: dict[str, Any] = data.get('models') or {}
	aliases_raw: dict[str, Any] = data.get('aliases') or {}
	cfg.app_name = str(app.get('name', cfg.app_name))
	cfg.base_url = str(app.get('base_url', cfg.base_url))
	cfg.models |= {str(k): str(v) for k, v in models_raw.items()}
	cfg.aliases |= {str(k): str(v) for k, v in aliases_raw.items()}
	cfg.max_steps = int(run.get('max_steps', cfg.max_steps))
	cfg.headless = bool(run.get('headless', cfg.headless))
	return cfg
