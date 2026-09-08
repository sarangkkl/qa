"""Setting up a connector from Settings.

Jira used to be a hand edit to `mcp:` in config.yaml, and the desktop's only guidance was a
dimmed line telling you to go and make it. With none configured there was no Sign in button
either - it lives inside the per-connector row - so the UI had no way forward at all, and
plan-from-ticket and file-a-bug silently vanished with it.
"""

import asyncio
from pathlib import Path

import pytest
from conftest import FakeChannel

from nkqa import actions, workspace
from nkqa import config as config_mod
from nkqa.shell.commands import REGISTRY, agent_commands
from nkqa.workspace import Workspace


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
	return workspace.create(tmp_path)


def test_connecting_jira_makes_it_real(ws: Workspace) -> None:
	ch = FakeChannel()
	assert asyncio.run(actions.connect(ws, ch, 'jira')) == 0

	spec = config_mod.load(ws.config_file).mcp_server('jira')
	assert spec is not None
	assert spec.command == 'npx'
	assert 'mcp-remote' in ' '.join(spec.args)
	assert 'qa auth jira' in ch.out, 'connecting is only half of it - say what comes next'


def test_the_agent_still_cannot_file_bugs_by_itself(ws: Workspace) -> None:
	"""The safety property, inherited from config.load's `name != 'jira'` default.

	Writing `expose_to_executor` here would turn a deliberate default into an ordinary field
	that a later edit could flip without anyone noticing.
	"""
	asyncio.run(actions.connect(ws, FakeChannel(), 'jira'))

	spec = config_mod.load(ws.config_file).mcp_server('jira')
	assert spec is not None and spec.expose_to_executor is False
	# Not written at all: the live lines we added must not mention it, so nobody can flip it
	# by editing a value that looks like an ordinary setting.
	live = [line for line in ws.config_file.read_text().splitlines() if not line.lstrip().startswith('#')]
	assert not any('expose_to_executor' in line for line in live)


def test_the_project_key_lands_where_file_bug_reads_it(ws: Workspace) -> None:
	"""`jira.project` is its own top-level key, NOT part of mcp.jira.

	Put it in the wrong place and the file still parses - it just fails later, when someone
	tries to file a bug and is told there is no project key.
	"""
	asyncio.run(actions.connect(ws, FakeChannel(), 'jira', 'sus'))

	cfg = config_mod.load(ws.config_file)
	assert cfg.jira_project == 'SUS', 'uppercased, the way Jira keys are written'
	assert cfg.mcp_server('jira') is not None


def test_connecting_twice_changes_nothing(ws: Workspace) -> None:
	"""Someone may have tuned the entry by hand; pressing Connect again is not a reason to undo it."""
	asyncio.run(actions.connect(ws, FakeChannel(), 'jira'))
	tuned = ws.config_file.read_text().replace('args: ["-y"', "args: ['--yes'")
	ws.config_file.write_text(tuned)

	ch = FakeChannel()
	assert asyncio.run(actions.connect(ws, ch, 'jira')) == 0
	assert ws.config_file.read_text() == tuned
	assert 'Already configured' in ch.out


def test_an_unknown_connector_is_usage_and_writes_nothing(ws: Workspace) -> None:
	"""This path writes a command config.yaml will later execute; it takes a fixed list only."""
	before = ws.config_file.read_text()
	ch = FakeChannel()

	assert asyncio.run(actions.connect(ws, ch, 'definitely-not-a-thing')) == 2
	assert ws.config_file.read_text() == before
	assert 'jira' in ch.out  # says what it does know


def test_naming_nothing_is_usage(ws: Workspace) -> None:
	before = ws.config_file.read_text()
	assert asyncio.run(actions.connect(ws, FakeChannel(), '')) == 2
	assert ws.config_file.read_text() == before


def test_the_agent_cannot_wire_up_its_own_connectors() -> None:
	assert REGISTRY['connect'].human_only is True
	assert 'connect' not in {c.name for c in agent_commands()}
	assert REGISTRY['connect'].instant is True  # Settings must work while a run is in flight


# --- the config writers -----------------------------------------------------


def test_the_commented_example_and_every_other_block_survive(ws: Workspace) -> None:
	before = ws.config_file.read_text()
	asyncio.run(actions.connect(ws, FakeChannel(), 'jira', 'PROJ'))
	after = ws.config_file.read_text()

	# The template's commented example is documentation; it stays.
	assert '#   jira:' in after
	assert '# MCP servers: extra tools for the product.' in after
	for line in before.splitlines():
		assert line in after.splitlines(), f'lost a line: {line!r}'
	cfg = config_mod.load(ws.config_file)
	assert cfg.max_steps == 30 and cfg.crawl_pages == 15 and cfg.app_name


def test_a_server_is_merged_into_an_existing_mcp_block() -> None:
	text = 'mcp:\n  test-data:\n    command: node\n    args: []\n\nrun:\n  max_steps: 30\n'
	after = config_mod.add_mcp_server(text, 'jira', 'npx', ['-y', 'mcp-remote'])

	assert after.count('mcp:') == 1, 'merged, not a second block'
	assert 'test-data:' in after and 'jira:' in after
	assert 'max_steps: 30' in after


def test_the_project_key_is_replaced_not_appended_twice() -> None:
	text = 'jira:\n  project: OLD\n'
	after = config_mod.set_jira_project(text, 'NEW')
	assert after.count('project:') == 1 and 'NEW' in after and 'OLD' not in after


def test_a_commented_block_is_not_mistaken_for_a_real_one(tmp_path: Path) -> None:
	"""The template ships `# mcp:` at column zero; treating it as the block would nest inside it."""
	text = '# mcp:\n#   jira:\n#     command: npx\n\nrun:\n  max_steps: 30\n'
	after = config_mod.add_mcp_server(text, 'jira', 'npx', ['-y'])

	assert '\nmcp:\n' in after, 'a real block must be created'
	assert '# mcp:' in after, 'and the comment left alone'
	written = tmp_path / 'c.yaml'
	written.write_text(after)
	assert config_mod.load(written).mcp_server('jira') is not None
