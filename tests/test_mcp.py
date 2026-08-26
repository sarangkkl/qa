import asyncio
import sys
from pathlib import Path

import pytest
from browser_use import Tools

from nkqa import config as config_mod
from nkqa.config import MCPServer
from nkqa.mcp import MCPRuntime, executor_servers, resolve_env

FIXTURE = str(Path(__file__).parent / 'fixtures' / 'echo_mcp_server.py')


def echo_server(name: str = 'echo', expose: bool = True, env: dict[str, str] | None = None) -> MCPServer:
	return MCPServer(name=name, command=sys.executable, args=[FIXTURE], env=env or {}, expose_to_executor=expose)


def test_resolve_env(monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setenv('MY_TOKEN', 'tok123')
	assert resolve_env({'A': 'plain', 'B': 'env:MY_TOKEN'}) == {'A': 'plain', 'B': 'tok123'}
	with pytest.raises(ValueError, match='NOT_SET_ANYWHERE'):
		resolve_env({'B': 'env:NOT_SET_ANYWHERE'})


def test_config_parses_mcp_section(tmp_path: Path) -> None:
	f = tmp_path / 'config.yaml'
	f.write_text(
		'mcp:\n'
		'  jira:\n'
		'    command: npx\n'
		"    args: ['-y', 'mcp-remote', 'https://mcp.atlassian.com/v1/sse']\n"
		'  seeder:\n'
		'    command: node\n'
		'    env: {KEY: "env:SEED_KEY"}\n'
		'jira:\n'
		'  project: PROJ\n'
	)
	cfg = config_mod.load(f)
	jira = cfg.mcp_server('jira')
	assert jira is not None and jira.expose_to_executor is False  # safety default
	seeder = cfg.mcp_server('seeder')
	assert seeder is not None and seeder.expose_to_executor is True
	assert cfg.jira_project == 'PROJ'
	assert executor_servers(cfg) == [seeder]


def test_config_without_mcp_is_unchanged(tmp_path: Path) -> None:
	cfg = config_mod.load(tmp_path / 'nope.yaml')
	assert cfg.mcp_servers == [] and cfg.jira_project == ''


def test_runtime_call_and_registration() -> None:
	async def scenario() -> None:
		async with MCPRuntime([echo_server(), echo_server('hidden', expose=False)]) as rt:
			assert await rt.call('echo', 'echo', {'text': 'hi'}) == 'echo: hi'
			assert 'echo' in rt.tool_names('echo')

			tools: Tools[None] = Tools()
			registered = await rt.register_executor_tools(tools)
			assert 'echo_echo' in registered
			assert all(not n.startswith('hidden_') for n in registered)  # expose=False stays out
			actions = tools.registry.registry.actions
			assert 'echo_echo' in actions and 'hidden_echo' not in actions

			with pytest.raises(RuntimeError, match='not connected'):
				await rt.call('nope', 'echo', {})

	asyncio.run(scenario())


def test_runtime_env_indirection(monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setenv('FIXTURE_TOKEN', 's3cret')

	async def scenario() -> None:
		async with MCPRuntime([echo_server(env={'FIXTURE_SECRET': 'env:FIXTURE_TOKEN'})]) as rt:
			assert await rt.call('echo', 'read_secret', {}) == 'secret=s3cret'

	asyncio.run(scenario())
