"""config.yaml loading. Missing file or keys fall back to prototype-equivalent defaults."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# 'default' lets browser-use pick its own model - but that needs a BROWSER_USE_API_KEY,
# so out of the box every role resolves via ANTHROPIC_API_KEY (the documented requirement).
DEFAULT_MODELS = {'planner': 'smart', 'executor': 'fast', 'reflector': 'fast', 'fallback': 'fast'}
DEFAULT_ALIASES = {'smart': 'anthropic_claude_sonnet_5', 'fast': 'anthropic_claude_haiku_4_5'}

CONFIG_TEMPLATE = """\
app:
  name: My App
  base_url: https://example.com

# Model roles. Values are aliases (defined below) or raw browser-use model names
# like anthropic_claude_haiku_4_5. 'default' lets browser-use pick its default model
# (requires BROWSER_USE_API_KEY).
models:
  planner: smart
  executor: fast
  reflector: fast
  fallback: fast

aliases:
  smart: anthropic_claude_sonnet_5
  fast: anthropic_claude_haiku_4_5

run:
  max_steps: 30
  headless: false

appmap:
  auto_reflect: true   # learn from every run (uses the 'reflector' model role)
  crawl_pages: 15      # page budget for the optional `qa crawl`

# MCP servers: extra tools for the product. Any server here can expose its tools to
# the testing agent (expose_to_executor, default true) - except jira, which defaults
# to false so bugs are only ever filed when a human runs `qa file-bug`.
# Secret env values use 'env:NAME' to read NAME from the environment/.env at launch.
# mcp:
#   jira:
#     command: npx
#     args: ['-y', 'mcp-remote', 'https://mcp.atlassian.com/v1/sse']
#   test-data:
#     command: node
#     args: ['./tools/seed-server.js']
#     env:
#       SEED_KEY: env:SEED_KEY
# jira:
#   project: PROJ   # default project key for `qa file-bug`
"""


@dataclass
class MCPServer:
	name: str
	command: str
	args: list[str] = field(default_factory=list[str])
	env: dict[str, str] = field(default_factory=dict[str, str])  # values may be 'env:NAME' indirections
	expose_to_executor: bool = True  # jira defaults to False: the testing agent must not file bugs itself
	tools: dict[str, str] = field(default_factory=dict[str, str])  # logical name -> server tool name overrides


@dataclass
class Config:
	app_name: str = 'My App'
	base_url: str = ''
	models: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_MODELS))
	aliases: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_ALIASES))
	max_steps: int = 30
	headless: bool = False
	mcp_servers: list[MCPServer] = field(default_factory=list[MCPServer])
	jira_project: str = ''
	auto_reflect: bool = True
	crawl_pages: int = 15

	def mcp_server(self, name: str) -> MCPServer | None:
		return next((s for s in self.mcp_servers if s.name == name), None)


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

	mcp_raw: dict[str, Any] = data.get('mcp') or {}
	for name, spec_any in mcp_raw.items():
		spec: dict[str, Any] = spec_any or {}
		if not spec.get('command'):
			raise ValueError(f'config.yaml: mcp.{name} needs a command')
		args_raw: list[Any] = spec.get('args') or []
		env_raw: dict[str, Any] = spec.get('env') or {}
		tools_raw: dict[str, Any] = spec.get('tools') or {}
		cfg.mcp_servers.append(
			MCPServer(
				name=str(name),
				command=str(spec['command']),
				args=[str(a) for a in args_raw],
				env={str(k): str(v) for k, v in env_raw.items()},
				expose_to_executor=bool(spec.get('expose_to_executor', name != 'jira')),
				tools={str(k): str(v) for k, v in tools_raw.items()},
			)
		)
	jira_raw: dict[str, Any] = data.get('jira') or {}
	cfg.jira_project = str(jira_raw.get('project') or '')
	appmap_raw: dict[str, Any] = data.get('appmap') or {}
	cfg.auto_reflect = bool(appmap_raw.get('auto_reflect', cfg.auto_reflect))
	cfg.crawl_pages = int(appmap_raw.get('crawl_pages', cfg.crawl_pages))
	return cfg
