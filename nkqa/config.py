"""config.yaml loading. Missing file or keys fall back to prototype-equivalent defaults."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# 'default' lets browser-use pick its own model - but that needs a BROWSER_USE_API_KEY,
# so out of the box every role resolves via ANTHROPIC_API_KEY (the documented requirement).
DEFAULT_MODELS = {'planner': 'smart', 'executor': 'fast', 'reflector': 'fast', 'chat': 'fast', 'fallback': 'fast'}
DEFAULT_ALIASES = {'smart': 'anthropic_claude_sonnet_5', 'fast': 'anthropic_claude_haiku_4_5'}

CONFIG_TEMPLATE = """\
app:
  name: My App
  base_url: https://example.com

# Model roles. Values are aliases (defined below) or model names in either form:
#   anthropic_claude_haiku_4_5   browser-use naming (underscores become dashes)
#   openai:gpt-5.1-mini          provider:exact-id, passed through verbatim
# 'default' lets browser-use pick its own model (requires BROWSER_USE_API_KEY).
models:
  planner: smart      # drafting scenarios, revising, ingesting docs
  executor: fast      # driving the browser through approved steps
  reflector: fast     # learning from runs into the appmap
  chat: fast          # routing/narration in the interactive shell
  fallback: fast      # cross-provider failover mid-run

aliases:
  smart: anthropic_claude_sonnet_5
  fast: anthropic_claude_haiku_4_5
  # mixing providers is fine, e.g.:
  # smart: openai:gpt-5.1

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


def render_template(app_name: str = '', base_url: str = '') -> str:
	"""CONFIG_TEMPLATE with the two app placeholders filled in. No arguments = unchanged.

	The value is quoted with json.dumps because YAML 1.2 is a JSON superset: an app name
	like `Acme: The Shop` or `#1 Store` would otherwise break the document, and the bare
	words `yes`/`no`/`on` would parse as booleans. Anchored on the whole `key: value` pair
	so it cannot match a comment further down the file.
	"""
	text = CONFIG_TEMPLATE
	if app_name:
		text = text.replace('name: My App', f'name: {json.dumps(app_name)}', 1)
	if base_url:
		text = text.replace('base_url: https://example.com', f'base_url: {json.dumps(base_url)}', 1)
	return text


def _block_span(lines: list[str], name: str) -> tuple[int, int]:
	"""Where a top-level `name:` block's body starts and ends, or (-1, -1) if there is none.

	A line at column zero ends the block, and that includes a comment - which is what keeps the
	commented-out `# mcp:` example in the template from being mistaken for the real thing.
	"""
	start = -1
	for i, line in enumerate(lines):
		stripped = line.strip()
		top_level = line[:1] not in (' ', '\t') and stripped != ''
		if start >= 0 and top_level:
			return start + 1, i
		if top_level and stripped.split('#', 1)[0].rstrip() == f'{name}:':
			start = i
	return (start + 1, len(lines)) if start >= 0 else (-1, -1)


def set_aliases(text: str, updates: dict[str, str]) -> str:
	"""Rewrite values in config.yaml's top-level `aliases:` block, leaving the rest byte-identical.

	A round-trip through yaml.safe_dump would be five lines instead of thirty, and would throw
	away every comment in the file - including the ones telling you what each role is for. The
	settings page edits two values; it has no business reformatting a file the user also edits
	by hand.

	Values are quoted with json.dumps for the same reason render_template does it: YAML 1.2 is
	a JSON superset, so this is always valid and never depends on the model id being free of
	characters YAML treats as structure.
	"""
	lines = text.splitlines()
	body, end = _block_span(lines, 'aliases')
	pending = dict(updates)
	trailing = '\n' if text.endswith('\n') else ''

	if body < 0:  # no aliases block at all - add one
		return '\n'.join([*lines, '', 'aliases:', *(f'  {k}: {json.dumps(v)}' for k, v in pending.items())]) + '\n'

	out = list(lines)
	for i in range(body, end):
		stripped = out[i].strip()
		if not stripped or stripped.startswith('#'):
			continue
		key = stripped.split(':', 1)[0].strip()
		if key in pending:
			indent = out[i][: len(out[i]) - len(out[i].lstrip())]
			out[i] = f'{indent}{key}: {json.dumps(pending.pop(key))}'
	if pending:
		out[body:body] = [f'  {k}: {json.dumps(v)}' for k, v in pending.items()]
	return '\n'.join(out) + trailing


# The official Atlassian remote MCP, bridged to stdio. Same spec as docs/ARCHITECTURE.md.
JIRA_MCP_COMMAND = 'npx'
JIRA_MCP_ARGS = ['-y', 'mcp-remote', 'https://mcp.atlassian.com/v1/sse']


def add_mcp_server(text: str, name: str, command: str, args: list[str]) -> str:
	"""Add a server under the top-level `mcp:` block, creating the block if there is not one.

	Idempotent by design: a server already listed is left exactly as it is. Someone may have
	tuned its args or set `expose_to_executor` by hand, and pressing Connect again is not a
	reason to undo that.
	"""
	lines = text.splitlines()
	body, end = _block_span(lines, 'mcp')
	# expose_to_executor is deliberately not written: config.load defaults it to False for
	# jira, which is what stops the testing agent filing bugs on its own. Spelling it out here
	# would make a safety default look like an ordinary setting.
	entry = [f'  {name}:', f'    command: {json.dumps(command)}', f'    args: {json.dumps(args)}']
	trailing = '\n' if text.endswith('\n') else ''

	if body < 0:
		return '\n'.join([*lines, '', 'mcp:', *entry]) + '\n'
	for i in range(body, end):
		stripped = lines[i].strip()
		if not stripped.startswith('#') and stripped.split(':', 1)[0].strip() == name:
			return text
	out = list(lines)
	out[body:body] = entry
	return '\n'.join(out) + trailing


def set_jira_project(text: str, key: str) -> str:
	"""Set `jira.project`, the default project key for `qa file-bug`.

	Its own top-level block, NOT a field of `mcp.jira` - that is where `load()` reads it from
	and where `file_bug` looks. Putting it in the wrong place parses fine and then fails only
	when someone tries to file a bug.
	"""
	lines = text.splitlines()
	body, end = _block_span(lines, 'jira')
	trailing = '\n' if text.endswith('\n') else ''

	if body < 0:
		return '\n'.join([*lines, '', 'jira:', f'  project: {json.dumps(key)}']) + '\n'
	out = list(lines)
	for i in range(body, end):
		stripped = out[i].strip()
		if not stripped.startswith('#') and stripped.split(':', 1)[0].strip() == 'project':
			indent = out[i][: len(out[i]) - len(out[i].lstrip())]
			out[i] = f'{indent}project: {json.dumps(key)}'
			return '\n'.join(out) + trailing
	out[body:body] = [f'  project: {json.dumps(key)}']
	return '\n'.join(out) + trailing


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
