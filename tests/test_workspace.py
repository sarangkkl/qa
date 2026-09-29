import json
from pathlib import Path

from nkqa import workspace


def test_slugify() -> None:
	assert workspace.slugify('Login flow!') == 'login-flow'
	assert workspace.slugify('  ') == 'unnamed-test'
	assert len(workspace.slugify('x' * 100)) <= 40


def test_create_and_find(tmp_path: Path) -> None:
	ws = workspace.create(tmp_path)
	assert ws.config_file.is_file()
	assert (ws.appmap_dir / 'overview.md').is_file()
	assert ws.scenarios_dir.is_dir() and ws.runs_dir.is_dir()

	nested = tmp_path / 'a' / 'b'
	nested.mkdir(parents=True)
	found = workspace.find(nested)
	assert found is not None and found.root == ws.root
	assert workspace.find(Path('/')) is None


def test_at_does_not_walk_up(tmp_path: Path) -> None:
	"""`find` adopts a parent workspace; `at` answers "is *this* folder one?" - which is what
	`--init` must ask, or initialising a subfolder would silently create nothing."""
	workspace.create(tmp_path)
	nested = tmp_path / 'sub'
	nested.mkdir()
	assert workspace.find(nested) is not None
	assert workspace.at(nested) is None


def test_create_writes_the_app_details(tmp_path: Path) -> None:
	from nkqa import config as config_mod

	ws = workspace.create(tmp_path, 'Sustain', 'https://dev.sustain.test')
	cfg = config_mod.load(ws.config_file)
	assert cfg.app_name == 'Sustain' and cfg.base_url == 'https://dev.sustain.test'


def test_create_survives_an_awkward_app_name(tmp_path: Path) -> None:
	"""A bare `name: Acme: The Shop` is not valid YAML, and `name: no` parses as False."""
	from nkqa import config as config_mod

	for name in ('Acme: The Shop', '#1 Store', 'no', 'a "quoted" thing'):
		root = tmp_path / workspace.slugify(name)
		ws = workspace.create(root, name, 'https://x.test')
		assert config_mod.load(ws.config_file).app_name == name


def test_create_without_details_is_the_untouched_template(tmp_path: Path) -> None:
	from nkqa import config as config_mod

	ws = workspace.create(tmp_path)
	assert ws.config_file.read_text() == config_mod.CONFIG_TEMPLATE


def test_create_is_idempotent(tmp_path: Path) -> None:
	ws = workspace.create(tmp_path)
	ws.config_file.write_text('app:\n  name: Edited\n')
	(tmp_path / 'AGENTS.md').write_text('my own notes\n')
	workspace.create(tmp_path)
	assert 'Edited' in ws.config_file.read_text()
	assert (tmp_path / 'AGENTS.md').read_text() == 'my own notes\n'


AGENT_FILES = ('AGENTS.md', 'CLAUDE.md', '.mcp.json', '.cursor/mcp.json')


def test_create_writes_the_agent_files(tmp_path: Path) -> None:
	"""Any agent opening the folder should learn the job without being told."""
	from nkqa.prompts import INSTRUCTIONS, QA_RULES_MCP

	workspace.create(tmp_path, 'Shop', 'https://dev.shop.test')
	for name in AGENT_FILES:
		assert (tmp_path / name).is_file(), name

	agents = (tmp_path / 'AGENTS.md').read_text()
	assert 'Shop' in agents and 'https://dev.shop.test' in agents
	# Interpolated, not retyped: if either block is ever pasted in by hand, this fails.
	assert INSTRUCTIONS.strip() in agents
	assert QA_RULES_MCP.strip() in agents
	# An import, not a mention: Claude Code skips its native AGENTS.md read whenever a CLAUDE.md
	# exists, so a pointer that only *asks* would leave the brief unread.
	assert (tmp_path / 'CLAUDE.md').read_text().startswith('@AGENTS.md\n')

	for name in ('.mcp.json', '.cursor/mcp.json'):
		server = json.loads((tmp_path / name).read_text())['mcpServers']['nkqa']
		assert server['command'] == 'uvx' and 'qa' in server['args'] and 'mcp' in server['args']


def test_create_never_overwrites_the_agent_files(tmp_path: Path) -> None:
	"""The folder is usually the app's own repo - it may already have a CLAUDE.md or an .mcp.json."""
	for name in AGENT_FILES:
		path = tmp_path / name
		path.parent.mkdir(parents=True, exist_ok=True)
		path.write_text('do not touch\n')

	workspace.create(tmp_path, 'Shop', 'https://dev.shop.test')

	for name in AGENT_FILES:
		assert (tmp_path / name).read_text() == 'do not touch\n', name


def test_agent_files_survive_a_workspace_with_no_app_details(tmp_path: Path) -> None:
	workspace.create(tmp_path)
	agents = (tmp_path / 'AGENTS.md').read_text()
	assert 'None' not in agents
	assert 'read_appmap' in agents and 'request_permission' in agents and 'approve_scenario' in agents


def test_scenario_run_dir_is_the_shape_latest_run_dir_globs(tmp_path: Path) -> None:
	from datetime import datetime

	from nkqa.execution.report import latest_run_dir

	ws = workspace.create(tmp_path)
	run_dir = ws.scenario_run_dir('auth/login', datetime(2026, 9, 10, 12, 0, 5))
	assert run_dir == ws.runs_dir / 'auth-login--20260910-120005'
	run_dir.mkdir()
	(run_dir / 'results.md').write_text('# auth/login — PASS — now\n')
	assert latest_run_dir(ws.runs_dir, 'auth/login') == run_dir


def test_recent_workspaces_reads_the_desktop_list_and_drops_the_dead(tmp_path: Path) -> None:
	"""The desktop app writes a JSON list of paths; folders that stopped being workspaces
	(deleted, or never were) must not come back from here."""
	alive = workspace.create(tmp_path / 'alive')
	recents = tmp_path / 'workspaces.json'
	recents.write_text(json.dumps([str(alive.root), str(tmp_path / 'gone'), 42]))
	assert [ws.root for ws in workspace.recent_workspaces(recents)] == [alive.root]

	recents.write_text('not json')
	assert workspace.recent_workspaces(recents) == []
	assert workspace.recent_workspaces(tmp_path / 'missing.json') == []
	assert workspace.recents_file().name == 'workspaces.json'


def test_migrate_prototype(tmp_path: Path) -> None:
	ws = workspace.create(tmp_path)
	old = tmp_path / 'qa_output'
	(old / 'tests' / 'checkout').mkdir(parents=True)
	(old / 'tests' / 'checkout' / 'history.json').write_text('{}')
	(old / 'qa_permissions.json').write_text('["delete-test-user"]')

	moved = workspace.migrate_prototype(ws, old)
	assert moved == ['checkout']
	assert (ws.runs_dir / 'checkout' / 'history.json').is_file()
	assert ws.permissions_file.is_file()
	# second call is a no-op
	assert workspace.migrate_prototype(ws, old) == []
