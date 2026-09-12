"""nkqa as an MCP server, driven through a real in-memory MCP client.

The browser is faked; everything else is real - workspace, scenarios, HITL, vault, evidence.
The invariants under test: nothing runs unapproved, the agent never sees a secret, the human
answers every gate through the dialog channel, and every tool documents itself.
"""

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, ClassVar

import pytest
from conftest import FakeChannel
from mcp.client.session import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import TextContent

from nkqa import mcp_server
from nkqa import scenarios as scenarios_mod
from nkqa import workspace as workspace_mod
from nkqa.config import MCPServer
from nkqa.mcp import MCPRuntime
from nkqa.mcp_server import Session, build_server
from nkqa.planner import PLANNER_SYSTEM
from nkqa.prompts import QA_RULES_MCP
from nkqa.scenarios import Scenario, Step


class FakeDriver:
	"""The Driver's surface, without a browser. Writes the same steps.json the real one does."""

	instances: ClassVar[list['FakeDriver']] = []

	def __init__(self, run_dir: Path, secrets: dict[str, Any], headless: bool, scenario_id: str = ''):
		self.run_dir = run_dir
		self.secrets = secrets
		self.scenario_id = scenario_id
		self.fresh = False
		self.closed: bool | None = None
		self.calls: list[tuple[str, dict[str, Any]]] = []
		self.page = 'https://shop.test/login'
		FakeDriver.instances.append(self)

	async def start(self) -> None:
		self.run_dir.mkdir(parents=True, exist_ok=True)
		self._save()

	def _save(self) -> None:
		steps = [
			{'n': i + 1, 'action': a, 'params': p, 'url_after': self.page, 'error': ''}
			for i, (a, p) in enumerate(self.calls)
		]
		(self.run_dir / 'steps.json').write_text(
			json.dumps({'driver': 'mcp', 'aborted': self.closed is True, 'steps': steps})
		)

	async def state(self, screenshot: bool = False) -> tuple[str, bytes | None]:
		self.fresh = True
		text = f'URL: {self.page}\n[1]<input type=password value=hunter2 />\n[2]<button>Sign in</button>'
		return text, (b'\x89PNG' if screenshot else None)

	async def act(self, action: str, params: dict[str, Any]) -> str:
		self.calls.append((action, params))
		self.fresh = False
		self._save()
		return f'{action} ok (hunter2 echoed by the page)\nNow at: {self.page}'

	async def wait(self, seconds: float) -> str:
		return f'Waited {seconds:g}s.'

	async def tabs(self) -> str:
		return '[1234] Sign in'

	async def current_url(self) -> str:
		return self.page

	async def close(self, aborted: bool = False) -> None:
		self.closed = aborted
		self._save()


Call = Callable[[str, dict[str, Any]], Awaitable[tuple[str, bool]]]


def make(tmp_path: Path, answers: list[str] | None = None) -> tuple[Session, FakeChannel]:
	ws = workspace_mod.create(tmp_path / 'ws', 'Shop', 'https://shop.test')
	scenarios_mod.save(
		Scenario(
			id='auth/login',
			path=ws.scenarios_dir / 'auth' / 'login.md',
			title='Login works',
			steps=[Step('Open the login page.', 'the form shows'), Step('Sign in as qa_user.', 'the dashboard shows')],
		)
	)
	dialogs = FakeChannel(answers)
	session = Session(dialogs=dialogs)
	session.use(ws)
	return session, dialogs


def drive(session: Session, scenario: Callable[[Call, ClientSession], Awaitable[None]]) -> None:
	"""Run `scenario` against the server over an in-memory MCP client."""

	async def go() -> None:
		async with create_connected_server_and_client_session(build_server(session)) as client:

			async def call(name: str, args: dict[str, Any]) -> tuple[str, bool]:
				result = await client.call_tool(name, args)
				text = '\n'.join(c.text for c in result.content if isinstance(c, TextContent))
				return text, bool(result.isError)

			await scenario(call, client)

	asyncio.run(go())


def approve(session: Session) -> None:
	ws = session.require_ws()
	s = scenarios_mod.find(ws.scenarios_dir, 'auth/login')
	assert s is not None
	scenarios_mod.approve(s, 'tester')


@pytest.fixture(autouse=True)
def fake_driver(monkeypatch: pytest.MonkeyPatch) -> None:
	FakeDriver.instances.clear()
	monkeypatch.setattr(mcp_server, 'Driver', FakeDriver)


def test_every_tool_documents_itself(tmp_path: Path) -> None:
	session, _ = make(tmp_path)

	async def scenario(call: Call, client: ClientSession) -> None:
		tools = (await client.list_tools()).tools
		names = {t.name for t in tools}
		for expected in (
			'workspace_status', 'list_workspaces', 'use_workspace', 'init_workspace', 'read_appmap', 'update_appmap',
			'list_scenarios', 'read_scenario', 'write_scenario', 'approve_scenario', 'start_run', 'start_explore',
			'browser_state', 'navigate', 'click', 'type_text', 'scroll', 'send_keys', 'go_back', 'list_tabs',
			'switch_tab', 'close_tab', 'wait', 'request_permission', 'ask_credential', 'finish_run',
			'finish_explore', 'abort_run', 'list_runs', 'read_run', 'read_ticket', 'file_bug',
		):  # fmt: skip
			assert expected in names, expected
		assert all(t.description and len(t.description) > 20 for t in tools), (
			'a tool without a description is invisible'
		)
		state = next(t for t in tools if t.name == 'browser_state')
		assert state.inputSchema['properties']['screenshot']['type'] == 'boolean'
		assert 'draft' in next(t.description or '' for t in tools if t.name == 'write_scenario')
		prompts = {p.name for p in (await client.list_prompts()).prompts}
		assert prompts == {'plan', 'run', 'explore'}

	drive(session, scenario)


def test_workspace_status_and_switching(tmp_path: Path) -> None:
	session, _ = make(tmp_path)
	other = workspace_mod.create(tmp_path / 'other', 'Admin', 'https://admin.test')

	async def scenario(call: Call, client: ClientSession) -> None:
		text, err = await call('workspace_status', {})
		assert not err and 'Shop' in text and 'draft: 1' in text and 'Jira: not configured' in text
		text, err = await call('use_workspace', {'path': str(tmp_path / 'nowhere')})
		assert err and 'not a QA workspace' in text
		text, err = await call('use_workspace', {'path': str(other.root)})
		assert not err and 'Admin' in text and session.ws is not None and session.ws.root == other.root
		text, err = await call('read_appmap', {})
		assert not err and 'overview.md' in text

	drive(session, scenario)


def test_no_workspace_is_a_readable_refusal(tmp_path: Path) -> None:
	session = Session(dialogs=FakeChannel())

	async def scenario(call: Call, client: ClientSession) -> None:
		text, err = await call('workspace_status', {})
		assert not err and 'No workspace is active' in text
		text, err = await call('list_scenarios', {})
		assert err and 'use_workspace' in text

	drive(session, scenario)


def test_write_then_approve_goes_through_the_human(tmp_path: Path) -> None:
	session, dialogs = make(tmp_path, ['', 'y'])  # first Cancel, then Confirm

	async def scenario(call: Call, client: ClientSession) -> None:
		draft = {
			'area': 'checkout',
			'slug': 'coupon',
			'title': 'Coupon applies',
			'steps': [
				{'action': 'Open the cart.', 'expect': 'items listed'},
				{'action': 'Apply SAVE10.', 'expect': '10% off'},
			],
		}
		text, err = await call('write_scenario', {'draft': draft})
		assert not err and 'draft checkout/coupon' in text and 'approve_scenario' in text
		ws = session.require_ws()
		assert (ws.scenarios_dir / 'checkout' / 'coupon.md').is_file()
		text, err = await call('write_scenario', {'draft': draft})
		assert not err and 'already exists' in text

		text, err = await call('approve_scenario', {'id': 'checkout/coupon'})
		assert not err and 'Not approved' in text
		ask = dialogs.asks[0]
		assert ask.kind == 'confirm' and 'Coupon applies' in ask.body and 'Apply SAVE10' in ask.body

		text, err = await call('approve_scenario', {'id': 'checkout/coupon'})
		assert not err and 'Approved' in text
		found = scenarios_mod.find(ws.scenarios_dir, 'checkout/coupon')
		assert found is not None and found.runnable() == 'ok'

	drive(session, scenario)


def test_start_run_refuses_anything_not_approved(tmp_path: Path) -> None:
	session, _ = make(tmp_path)

	async def scenario(call: Call, client: ClientSession) -> None:
		text, err = await call('start_run', {'scenario_id': 'auth/login'})
		assert err and 'not approved yet' in text and 'approve_scenario' in text
		text, err = await call('start_run', {'scenario_id': 'nope/x'})
		assert err and 'No scenario' in text
		assert FakeDriver.instances == []

	drive(session, scenario)


def test_a_run_from_start_to_finish(tmp_path: Path) -> None:
	session, dialogs = make(tmp_path, ['hunter2', 'n', 'a'])
	approve(session)

	async def scenario(call: Call, client: ClientSession) -> None:
		text, err = await call('start_run', {'scenario_id': 'auth/login'})
		assert not err and 'Open the login page.' in text and 'EXPECT: the form shows' in text and 'finish_run' in text
		assert 'https://shop.test' in text
		driver = FakeDriver.instances[-1]
		assert driver.scenario_id == 'auth/login' and driver.run_dir.name.startswith('auth-login--')

		text, err = await call('use_workspace', {'path': str(session.require_ws().root)})
		assert err and 'run is active' in text
		text, err = await call('click', {'index': 2})
		assert err and 'stale' in text and 'browser_state' in text

		text, err = await call('ask_credential', {'name': 'Password'})
		assert not err and '<secret>password</secret>' in text and 'hunter2' not in text
		assert dialogs.asks[0].kind == 'secret'
		assert session.require_hitl().secrets == {'password': 'hunter2'}

		text, err = await call('browser_state', {})
		assert not err and 'hunter2' not in text and '[secret]' in text and '[2]<button>' in text
		text, err = await call('type_text', {'index': 1, 'text': '<secret>password</secret>'})
		assert (
			not err
			and 'hunter2' not in text
			and driver.calls[-1] == ('input', {'index': 1, 'text': '<secret>password</secret>', 'clear': True})
		)
		text, err = await call('browser_state', {'screenshot': True})
		assert not err
		text, err = await call('click', {'index': 2})
		assert not err and driver.calls[-1] == ('click', {'index': 2})

		text, err = await call('request_permission', {'key': 'Delete-User', 'description': 'remove the test user'})
		assert not err and 'DENIED' in text
		text, err = await call('request_permission', {'key': 'export-csv', 'description': 'download a report'})
		assert not err and 'granted permanently' in text
		assert 'export-csv' in json.loads(session.require_ws().permissions_file.read_text())['permissions']

		steps = [
			{'step': 1, 'verdict': 'pass', 'note': ''},
			{'step': 2, 'verdict': 'fail', 'note': 'expected dashboard, got 500'},
		]
		text, err = await call('finish_run', {'steps': steps, 'summary': 'login is broken'})
		assert not err and 'FAIL' in text and 'update_appmap' in text and 'Steps executed' in text
		assert driver.closed is False and session.driver is None
		results = (driver.run_dir / 'results.md').read_text()
		assert '— FAIL —' in results and 'steps.json' in results and 'history.json' not in results
		assert json.loads((driver.run_dir / 'results.json').read_text())['verdict'] == 'fail'

		text, err = await call('list_runs', {})
		assert not err and driver.run_dir.name in text
		text, err = await call('read_run', {'name': driver.run_dir.name})
		assert not err and 'FAIL' in text and '1. input' in text and 'hunter2' not in text
		text, err = await call('list_scenarios', {})
		assert not err and 'last: FAIL' in text

	drive(session, scenario)


def test_abort_marks_the_scenario_blocked(tmp_path: Path) -> None:
	session, _ = make(tmp_path)
	approve(session)

	async def scenario(call: Call, client: ClientSession) -> None:
		await call('start_run', {'scenario_id': 'auth/login'})
		text, err = await call('abort_run', {})
		assert not err and 'BLOCKED' in text
		driver = FakeDriver.instances[-1]
		assert driver.closed is True
		assert json.loads((driver.run_dir / 'results.json').read_text())['verdict'] == 'blocked'
		text, err = await call('abort_run', {})
		assert err and 'No active run' in text

	drive(session, scenario)


def test_explore_writes_notes(tmp_path: Path) -> None:
	session, _ = make(tmp_path)

	async def scenario(call: Call, client: ClientSession) -> None:
		text, err = await call('start_explore', {'name': 'Poke Around', 'url': 'https://shop.test/'})
		assert not err and 'runs/poke-around' in text and 'navigate ok' in text
		text, err = await call('finish_run', {'steps': [], 'summary': 'x'})
		assert err and 'finish_explore' in text
		text, err = await call('finish_explore', {'summary': 'The cart page 500s.'})
		assert not err and 'notes.md' in text
		assert 'The cart page 500s.' in (session.require_ws().runs_dir / 'poke-around' / 'notes.md').read_text()

	drive(session, scenario)


def test_update_appmap_writes_through_the_sandbox(tmp_path: Path) -> None:
	session, _ = make(tmp_path)

	async def scenario(call: Call, client: ClientSession) -> None:
		files = [{'file': 'pages/login.md', 'content': '# Login\n\n**Route:** `/login`\n'}]
		text, err = await call('update_appmap', {'files': files, 'message': 'learned login'})
		assert not err and 'pages/login.md' in text
		assert (session.require_ws().appmap_dir / 'pages' / 'login.md').is_file()
		text, err = await call('update_appmap', {'files': [{'file': '../../etc/passwd', 'content': 'x'}]})
		assert err

	drive(session, scenario)


def test_prompts_carry_the_rules_and_the_context(tmp_path: Path) -> None:
	session, _ = make(tmp_path)

	async def scenario(call: Call, client: ClientSession) -> None:
		plan = await client.get_prompt('plan', {'ask': 'the checkout flow'})
		body = plan.messages[0].content
		assert isinstance(body, TextContent)
		assert PLANNER_SYSTEM.splitlines()[0] in body.text and 'Name: Shop' in body.text
		assert 'the checkout flow' in body.text and 'write_scenario' in body.text and 'auth/login' in body.text
		run = await client.get_prompt('run', {'scenario_id': 'auth/login'})
		text = run.messages[0].content
		assert isinstance(text, TextContent) and QA_RULES_MCP.strip().splitlines()[0] in text.text
		assert '"auth/login"' in text.text and 'finish_run' in text.text
		explore = await client.get_prompt('explore', {})
		assert isinstance(explore.messages[0].content, TextContent)

	drive(session, scenario)


def test_stdio_round_trip_keeps_stdout_clean(tmp_path: Path) -> None:
	"""The real transport, through the same MCP client the product uses for Jira: browser-use
	gets imported, logging is configured, and nothing but JSON-RPC may reach stdout."""
	ws = workspace_mod.create(tmp_path / 'ws', 'Shop', 'https://shop.test')
	spec = MCPServer(name='nkqa', command=sys.executable, args=['-m', 'nkqa.mcp_server', str(ws.root)], env={})

	async def scenario() -> None:
		async with MCPRuntime([spec]) as rt:
			assert 'workspace_status' in rt.tool_names('nkqa')
			assert 'App: Shop' in await rt.call('nkqa', 'workspace_status', {})

	asyncio.run(scenario())


def test_jira_tools_explain_when_jira_is_not_configured(tmp_path: Path) -> None:
	session, _ = make(tmp_path)

	async def scenario(call: Call, client: ClientSession) -> None:
		text, err = await call('read_ticket', {'key': 'PROJ-1'})
		assert err and 'jira' in text.lower()
		text, err = await call('read_ticket', {'key': 'not a key'})
		assert err and 'does not look like' in text
		text, err = await call('file_bug', {'run': 'missing'})
		assert not err and 'No run' in text

	drive(session, scenario)
