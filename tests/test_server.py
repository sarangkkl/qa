"""The sidecar contract: auth, reads, the command lifecycle, asks, cancellation."""

# starlette's TestClient boundary: it is typed against httpx2, so every response it hands
# back is Unknown here. Same treatment as the browser-use boundary elsewhere in the suite.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

import asyncio
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nkqa import chats as chats_mod
from nkqa import scenarios as scenarios_mod
from nkqa import workspace as workspace_mod
from nkqa.server import auth
from nkqa.server.app import create_app, safe_artifact
from nkqa.server.jobs import JobRunner
from nkqa.workspace import Workspace

TOKEN = 'test-token'


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
	workspace = workspace_mod.create(tmp_path)
	scenarios_mod.save(
		scenarios_mod.Scenario(
			id='auth/login',
			path=workspace.scenarios_dir / 'auth' / 'login.md',
			title='Login works',
			steps=[scenarios_mod.Step('Open the login page.', 'the form shows')],
		)
	)
	return workspace


@pytest.fixture
def client(ws: Workspace) -> Iterator[TestClient]:
	auth.set_token(TOKEN)
	with TestClient(create_app(ws)) as c:
		yield c
	auth.set_token('')


def get(client: TestClient, path: str, **kwargs: Any) -> Any:
	return client.get(path, headers={'Authorization': f'Bearer {TOKEN}'}, **kwargs)


# --- auth -------------------------------------------------------------------


def test_no_token_is_rejected(client: TestClient) -> None:
	assert client.get('/health').status_code == 401
	assert client.get('/health', headers={'Authorization': 'Bearer wrong'}).status_code == 401


def test_browser_origin_must_be_known(client: TestClient) -> None:
	headers = {'Authorization': f'Bearer {TOKEN}', 'Origin': 'https://evil.example'}
	assert client.get('/health', headers=headers).status_code == 403
	headers['Origin'] = 'tauri://localhost'
	assert client.get('/health', headers=headers).status_code == 200


def test_websocket_rejects_a_bad_token(client: TestClient) -> None:
	with pytest.raises(Exception), client.websocket_connect('/session?token=nope'):  # noqa: B017
		pass  # starlette raises on a refused upgrade


# --- reads ------------------------------------------------------------------


def test_health_and_workspace(client: TestClient, ws: Workspace) -> None:
	health = get(client, '/health').json()
	assert health['workspace'] == str(ws.root) and 'chat' in health['roles'] and health['busy'] is False

	state = get(client, '/workspace').json()
	assert state['scenarios'][0]['id'] == 'auth/login'
	assert state['scenarios'][0]['state'] == 'draft'
	assert 'overview.md' in state['appmap']
	names = {c['name'] for c in state['commands']}
	assert {'plan', 'run', 'approve'} <= names
	assert next(c for c in state['commands'] if c['name'] == 'approve')['human_only'] is True


def test_one_scenario_and_missing(client: TestClient) -> None:
	body = get(client, '/scenarios/auth/login').json()
	assert body['title'] == 'Login works' and 'Open the login page.' in body['body']
	assert get(client, '/scenarios/no/such').status_code == 404


def test_artifacts_cannot_escape_the_workspace(ws: Workspace, tmp_path: Path) -> None:
	secret = tmp_path.parent / 'outside.md'
	secret.write_text('not yours')
	with pytest.raises(Exception):  # noqa: B017 - HTTPException
		safe_artifact(ws, '../outside.md')
	with pytest.raises(Exception):  # noqa: B017
		safe_artifact(ws, str(secret))
	(ws.root / 'appmap' / 'overview.md').write_text('# hi')
	assert safe_artifact(ws, 'appmap/overview.md').name == 'overview.md'


def test_artifacts_refuse_unlisted_types(client: TestClient, ws: Workspace) -> None:
	(ws.root / 'config.yaml').write_text('app:\n  name: x\n')
	assert get(client, '/artifacts/config.yaml').status_code == 403
	assert get(client, '/artifacts/appmap/overview.md').status_code == 200


# --- the command lifecycle over the socket ----------------------------------


def run_frames(client: TestClient, outgoing: list[dict[str, Any]], stop_on: str = 'result') -> list[dict[str, Any]]:
	"""Send frames, collect until the stop frame arrives."""
	received: list[dict[str, Any]] = []
	with client.websocket_connect(f'/session?token={TOKEN}') as socket:
		for frame in outgoing:
			socket.send_json(frame)
		while True:
			frame = socket.receive_json()
			received.append(frame)
			if frame.get('type') == stop_on:
				return received


def test_command_streams_events_then_a_result(client: TestClient) -> None:
	frames = run_frames(client, [{'type': 'command', 'id': 'c1', 'name': 'scenarios'}])
	kinds = [f['type'] for f in frames]
	assert kinds[0] == 'started' and kinds[-1] == 'result'
	assert frames[-1] == {'type': 'result', 'job': 'c1', 'code': 0, 'cancelled': False}

	rows = [f for f in frames if f['type'] == 'event' and f.get('data', {}).get('scenario')]
	assert rows and rows[0]['data']['scenario'] == 'auth/login'
	assert rows[0]['data']['state'] == 'draft'  # structured, not just text for a UI to re-parse


def test_unknown_command_is_a_usage_error(client: TestClient) -> None:
	frames = run_frames(client, [{'type': 'command', 'id': 'c1', 'name': 'nope'}])
	assert frames[-1]['code'] == 2 and 'no such command' in frames[-1]['error']


def test_shell_only_commands_are_not_exposed(client: TestClient) -> None:
	frames = run_frames(client, [{'type': 'command', 'id': 'c1', 'name': 'exit'}])
	assert frames[-1]['code'] == 2


def test_ask_round_trip_approves_a_scenario(client: TestClient, ws: Workspace) -> None:
	"""approve is human_only: it is served, but it must ask before it changes anything."""
	received: list[dict[str, Any]] = []
	with client.websocket_connect(f'/session?token={TOKEN}') as socket:
		socket.send_json({'type': 'command', 'id': 'c1', 'name': 'approve', 'args': {'id': 'auth/login'}})
		while True:
			frame = socket.receive_json()
			received.append(frame)
			if frame['type'] == 'ask':
				assert frame['kind'] == 'confirm'
				assert 'Open the login page.' in frame['body']  # the human sees what they approve
				socket.send_json({'type': 'answer', 'id': frame['id'], 'value': 'y'})
			if frame['type'] == 'result':
				break

	assert received[-1]['code'] == 0
	assert scenarios_mod.parse(ws.scenarios_dir / 'auth' / 'login.md', ws.scenarios_dir).runnable() == 'ok'


def test_declining_an_ask_leaves_the_scenario_a_draft(client: TestClient, ws: Workspace) -> None:
	with client.websocket_connect(f'/session?token={TOKEN}') as socket:
		socket.send_json({'type': 'command', 'id': 'c1', 'name': 'approve', 'args': {'id': 'auth/login'}})
		while True:
			frame = socket.receive_json()
			if frame['type'] == 'ask':
				socket.send_json({'type': 'answer', 'id': frame['id'], 'value': 'n'})
			if frame['type'] == 'result':
				assert frame['code'] == 1
				break
	assert scenarios_mod.parse(ws.scenarios_dir / 'auth' / 'login.md', ws.scenarios_dir).runnable() == 'draft'


def test_a_refused_command_does_not_steal_the_running_jobs_events(client: TestClient) -> None:
	"""A job id is claimed only once the job really starts.

	`approve` parks on an ask; sending anything else while it waits is refused as busy. If
	the refused frame's id were adopted by the channel, every later event from the *live*
	job would carry an id the UI never saw a `started` for, and the run's log would go dark.
	"""
	with client.websocket_connect(f'/session?token={TOKEN}') as socket:
		socket.send_json({'type': 'command', 'id': 'c1', 'name': 'approve', 'args': {'id': 'auth/login'}})
		events: list[dict[str, Any]] = []
		while True:
			frame = socket.receive_json()
			if frame['type'] == 'ask':
				socket.send_json({'type': 'command', 'id': 'c2', 'name': 'scenarios'})  # refused: busy
				assert socket.receive_json() == {
					'type': 'result',
					'job': 'c2',
					'code': 2,
					'error': 'busy: "approve" is still running',
				}
				socket.send_json({'type': 'answer', 'id': frame['id'], 'value': 'n'})
			if frame['type'] == 'event':
				events.append(frame)
			if frame['type'] == 'result' and frame['job'] == 'c1':
				break
	assert events, 'approve should narrate at least once'
	assert {e['job'] for e in events} == {'c1'}


def test_the_socket_session_has_a_vault(ws: Workspace) -> None:
	"""Without this the credential release chain is dead: `_from_vault` returns immediately."""
	from nkqa.server.app import build_context
	from nkqa.server.channel import SocketChannel

	async def send(_: dict[str, Any]) -> None:
		return None

	assert build_context(ws, SocketChannel(send)).hitl.vault is not None


# --- slash lines typed in the chat box --------------------------------------


def test_a_command_line_uses_the_shared_parser(client: TestClient) -> None:
	"""Same grammar as the terminal, because it is literally the same parser."""
	frames = run_frames(client, [{'type': 'command', 'id': 'c1', 'line': '/scenarios'}])
	assert frames[0] == {'type': 'started', 'job': 'c1', 'name': 'scenarios'}
	assert frames[-1]['code'] == 0


def test_a_bad_command_line_reports_the_parsers_own_message(client: TestClient) -> None:
	frames = run_frames(client, [{'type': 'command', 'id': 'c1', 'line': '/nope'}])
	assert frames[-1]['code'] == 2 and 'Unknown command' in frames[-1]['error']

	frames = run_frames(client, [{'type': 'command', 'id': 'c2', 'line': '/revise'}])
	assert frames[-1]['code'] == 2 and 'needs: id, instruction' in frames[-1]['error']


def test_shell_only_as_a_line_is_still_refused(client: TestClient) -> None:
	"""Client-side /help is a convenience; the server is what enforces it."""
	frames = run_frames(client, [{'type': 'command', 'id': 'c1', 'line': '/help'}])
	assert frames[-1]['code'] == 2


def test_approve_typed_as_a_line_still_shows_the_body(client: TestClient) -> None:
	"""The gate is the ask, not the surface the command came from."""
	with client.websocket_connect(f'/session?token={TOKEN}') as socket:
		socket.send_json({'type': 'command', 'id': 'c1', 'line': '/approve auth/login'})
		while True:
			frame = socket.receive_json()
			if frame['type'] == 'ask':
				assert frame['kind'] == 'confirm' and 'Login works' in frame['body']
				socket.send_json({'type': 'answer', 'id': frame['id'], 'value': 'n'})
			if frame['type'] == 'result':
				assert frame['code'] == 1
				break


def test_a_chat_line_is_recorded_in_the_transcript(client: TestClient, ws: Workspace) -> None:
	"""Otherwise reading a conversation back has unexplained holes where commands ran."""
	frames = run_frames(client, [{'type': 'command', 'id': 'c1', 'line': '/scenarios', 'chat': ''}])
	chat_id = next(f['id'] for f in frames if f['type'] == 'chat')
	# The result races the transcript write, so wait for the turn to land.
	for _ in range(50):
		chat = chats_mod.load(ws, chat_id)
		if chat and len(chat.turns) >= 2:
			break
		time.sleep(0.02)
	chat = chats_mod.load(ws, chat_id)
	assert chat is not None
	assert [t.text for t in chat.turns if t.role == 'user'] == ['/scenarios']
	assert [(t.command, t.exit) for t in chat.turns if t.role == 'assistant'] == [('scenarios', 0)]


def test_mode_can_be_set_while_a_job_is_running(client: TestClient) -> None:
	"""The whole point of `instant`: a session control you cannot use mid-run is useless.

	`approve` parks on an ask, so the runner is busy. A normal command is refused there
	(the test above); `mode` must go through anyway, and must not disturb the live job.
	"""
	from_mode: list[dict[str, Any]] = []
	after_mode: list[dict[str, Any]] = []
	with client.websocket_connect(f'/session?token={TOKEN}') as socket:
		socket.send_json({'type': 'command', 'id': 'c1', 'name': 'approve', 'args': {'id': 'auth/login'}})
		answered = False
		while True:
			frame = socket.receive_json()
			if frame['type'] == 'ask':
				socket.send_json({'type': 'command', 'id': 'm1', 'name': 'mode', 'args': {'value': 'refuse'}})
				while True:
					reply = socket.receive_json()
					if reply['type'] == 'event':
						from_mode.append(reply)
					if reply['type'] == 'result' and reply['job'] == 'm1':
						assert reply['code'] == 0, 'mode must not be refused as busy'
						break
				socket.send_json({'type': 'answer', 'id': frame['id'], 'value': 'n'})
				answered = True
			elif frame['type'] == 'event' and answered:
				after_mode.append(frame)
			if frame['type'] == 'result' and frame['job'] == 'c1':
				break

	assert any(e.get('data', {}).get('mode') == 'refuse' for e in from_mode)
	assert {e['job'] for e in from_mode} == {'m1'}
	# The live job carries on under its own id: the instant command had its own channel and
	# never touched the running job's.
	assert after_mode and {e['job'] for e in after_mode} == {'c1'}


def test_an_unknown_mode_is_a_usage_error(client: TestClient) -> None:
	frames = run_frames(client, [{'type': 'command', 'id': 'm1', 'name': 'mode', 'args': {'value': 'yolo'}}])
	assert frames[-1]['code'] == 2


def test_mode_is_human_only_and_instant(client: TestClient) -> None:
	"""human_only keeps it out of the agent's tool list: autonomy is never self-widened."""
	from nkqa.shell.commands import agent_commands

	mode = next(c for c in get(client, '/workspace').json()['commands'] if c['name'] == 'mode')
	assert mode['human_only'] is True and mode['instant'] is True
	assert 'mode' not in {c.name for c in agent_commands()}


# --- creating a workspace from the app --------------------------------------


def test_init_creates_a_workspace_the_server_can_then_find(tmp_path: Path) -> None:
	from nkqa.server.main import create_or_fail

	root = tmp_path / 'brand-new'
	root.mkdir()
	assert create_or_fail(root, 'Sustain', 'https://dev.test') == ''
	found = workspace_mod.at(root)
	assert found is not None and found.config_file.is_file()


def test_init_refuses_home_and_the_filesystem_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	"""The two places a mis-click in a folder picker actually costs something.

	Home is faked: if the guard ever regressed, a test that passed the real one would
	scatter a workspace through the developer's home directory to prove it.
	"""
	from nkqa.server.main import create_or_fail

	fake_home = tmp_path / 'home'
	fake_home.mkdir()
	monkeypatch.setattr(Path, 'home', classmethod(lambda _cls: fake_home))  # type: ignore[misc]
	assert 'refusing' in create_or_fail(fake_home, '', '')
	assert not (fake_home / 'config.yaml').exists()
	assert 'refusing' in create_or_fail(Path(tmp_path.anchor), '', '')


def test_init_never_overwrites_an_existing_config(ws: Workspace) -> None:
	from nkqa.server.main import create_or_fail

	ws.config_file.write_text('app:\n  name: Already Here\n')
	assert create_or_fail(ws.root, 'Something Else', 'https://x.test') == ''
	assert 'Already Here' in ws.config_file.read_text()


def test_the_server_still_refuses_a_plain_folder_without_init(tmp_path: Path) -> None:
	assert workspace_mod.at(tmp_path) is None and workspace_mod.find(tmp_path) is None


# --- stopping ---------------------------------------------------------------


def test_cancel_over_the_socket_ends_the_job(client: TestClient) -> None:
	"""The whole cancel path, which nothing covered before: frame in, job actually over."""
	with client.websocket_connect(f'/session?token={TOKEN}') as socket:
		socket.send_json({'type': 'command', 'id': 'c1', 'name': 'approve', 'args': {'id': 'auth/login'}})
		acked = False
		while True:
			frame = socket.receive_json()
			if frame['type'] == 'ask':
				socket.send_json({'type': 'cancel', 'id': 'c1'})
			if frame['type'] == 'cancelled':
				assert frame == {'type': 'cancelled', 'job': 'c1', 'ok': True}
				acked = True
			if frame['type'] == 'result':
				# Trust result.cancelled, not the code - the runners swallow CancelledError
				# so partial evidence is saved, which means a cancelled job still has one.
				assert frame['cancelled'] is True
				break
	assert acked


def test_a_stale_cancel_does_not_deny_a_live_ask(client: TestClient) -> None:
	"""Abandoning resolves every pending ask with '' - a deny. A cancel for a job that is
	not running must not reach in and answer the running job's prompt."""
	with client.websocket_connect(f'/session?token={TOKEN}') as socket:
		socket.send_json({'type': 'command', 'id': 'c1', 'name': 'approve', 'args': {'id': 'auth/login'}})
		while True:
			frame = socket.receive_json()
			if frame['type'] == 'ask':
				socket.send_json({'type': 'cancel', 'id': 'ghost'})
				assert socket.receive_json() == {'type': 'cancelled', 'job': 'ghost', 'ok': False}
				# The real ask is still live and still ours to answer.
				socket.send_json({'type': 'answer', 'id': frame['id'], 'value': 'y'})
			if frame['type'] == 'result':
				assert frame['code'] == 0 and frame['cancelled'] is False
				break


def test_cancel_trips_the_stop_signal() -> None:
	"""Cancelling the task alone never reaches the agent; both halves must fire."""
	from nkqa.stop import StopSignal

	async def scenario() -> None:
		runner = JobRunner()
		signal = StopSignal()

		async def forever() -> int:
			await asyncio.sleep(30)
			return 0

		job = runner.start('j1', 'run', forever(), signal)
		await asyncio.sleep(0)
		assert runner.cancel('j1') is True
		assert signal.stopped, 'cancel must tell the agent, not just the task'
		await runner.wait(job)

	asyncio.run(scenario())


def test_starting_a_job_disarms_the_previous_stop() -> None:
	"""Or one stopped run would kill every job after it in the same session."""
	from nkqa.stop import StopSignal

	async def scenario() -> None:
		runner = JobRunner()
		signal = StopSignal()
		signal.stop()

		async def quick() -> int:
			return 0

		job = runner.start('j2', 'run', quick(), signal)
		assert not signal.stopped
		await runner.wait(job)

	asyncio.run(scenario())


def test_unknown_frame_type_is_reported(client: TestClient) -> None:
	with client.websocket_connect(f'/session?token={TOKEN}') as socket:
		socket.send_json({'type': 'wat'})
		assert socket.receive_json()['type'] == 'error'


# --- job runner -------------------------------------------------------------


def test_one_job_at_a_time() -> None:
	async def scenario() -> None:
		runner = JobRunner()

		async def slow() -> int:
			await asyncio.sleep(0.2)
			return 0

		job = runner.start('j1', 'run', slow())
		assert runner.busy
		with pytest.raises(RuntimeError, match='busy'):
			runner.start('j2', 'run', slow())
		assert await runner.wait(job) == (0, False)
		assert not runner.busy

	asyncio.run(scenario())


def test_cancel_marks_the_job_cancelled() -> None:
	async def scenario() -> None:
		runner = JobRunner()

		async def forever() -> int:
			await asyncio.sleep(30)
			return 0

		job = runner.start('j1', 'run', forever())
		await asyncio.sleep(0)
		assert runner.cancel('j1') is True
		assert runner.cancel('nope') is False
		code, cancelled = await runner.wait(job)
		assert cancelled is True and code == 1

	asyncio.run(scenario())


def test_evidence_saving_cancel_still_reports_cancelled() -> None:
	"""The runners swallow CancelledError to save evidence; the runner must not be fooled."""

	async def scenario() -> None:
		runner = JobRunner()

		async def saves_evidence() -> int:
			try:
				await asyncio.sleep(30)
			except asyncio.CancelledError:
				return 1  # what execution/runner.py does: keep the artifacts, report a code
			return 0

		job = runner.start('j1', 'run', saves_evidence())
		await asyncio.sleep(0)
		runner.cancel('j1')
		code, cancelled = await runner.wait(job)
		assert cancelled is True and code == 1

	asyncio.run(scenario())


# --- chat over the socket ----------------------------------------------------


def test_say_creates_a_chat_and_records_it(client: TestClient, ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
	"""`say` is what the desktop Chat view sends; commands alone cannot carry plain English."""
	from nkqa import chats as chats_mod
	from nkqa.shell import agent

	class StubLLM:
		async def ainvoke(self, messages: Any, output_format: Any = None) -> Any:
			class R:
				completion = agent.ChatDecision(reply='Listing your scenarios.', command='scenarios')

			return R()

	def fake_resolve(cfg: object, role: str, override: str | None = None) -> object:
		return StubLLM()

	monkeypatch.setattr(agent, 'resolve_llm', fake_resolve)

	chat_id = ''
	with client.websocket_connect(f'/session?token={TOKEN}') as socket:
		socket.send_json({'type': 'say', 'id': 's1', 'text': 'what scenarios do I have?'})
		while True:
			frame = socket.receive_json()
			if frame['type'] == 'chat':
				chat_id = frame['id']  # the server tells the client which chat it opened
			if frame['type'] == 'result':
				assert frame['job'] == 's1'
				break

	assert chat_id
	stored = chats_mod.load(ws, chat_id)
	assert stored is not None
	assert stored.turns[0].text == 'what scenarios do I have?'
	assert any(t.command == 'scenarios' for t in stored.turns)


def test_empty_say_is_a_usage_error(client: TestClient) -> None:
	frames = run_frames(client, [{'type': 'say', 'id': 's1', 'text': '   '}])
	assert frames[-1]['code'] == 2 and 'empty message' in frames[-1]['error']


def test_chat_routes(client: TestClient, ws: Workspace) -> None:
	from nkqa import chats as chats_mod
	from nkqa.chats import Turn

	chat = chats_mod.new_chat(ws)
	chat.add(Turn(role='user', text='hello'))
	chats_mod.save(ws, chat)

	assert get(client, '/chats').json()['chats'][0]['id'] == chat.id
	body = get(client, f'/chats/{chat.id}').json()
	assert body['turns'][0]['text'] == 'hello'
	assert get(client, '/chats/nope').status_code == 404


def test_frames_are_their_own_lean_message_type() -> None:
	"""At 20 fps a per-frame `text` field and event envelope actually cost something."""

	async def scenario() -> None:
		from nkqa.server.channel import SocketChannel
		from nkqa.ui import Event

		sent: list[dict[str, Any]] = []

		async def send(frame: dict[str, Any]) -> None:
			sent.append(frame)

		channel = SocketChannel(send, job_id='c1')
		await channel.emit(Event('frame', '', {'image': 'BASE64', 'format': 'jpeg', 'width': 1280, 'height': 800}))
		await channel.emit(Event('log', 'hello'))

		assert sent[0] == {
			'type': 'frame',
			'job': 'c1',
			'image': 'BASE64',
			'format': 'jpeg',
			'width': 1280,
			'height': 800,
		}
		assert sent[1]['type'] == 'event' and sent[1]['kind'] == 'log'

	asyncio.run(scenario())


def test_the_handshake_port_is_already_listening() -> None:
	"""'ready' on stdout must mean ready: a client that connects at once must not be refused.

	Binding alone does not queue connections - there was a window between printing the
	handshake and uvicorn calling listen() where connect() got ECONNREFUSED.
	"""
	import socket as socket_mod

	from nkqa.server.main import bind_port

	sock, port = bind_port()
	try:
		assert port > 0
		# Connecting is the portable proof, and the stronger one: it is the exact thing the
		# handshake promises. (SO_ACCEPTCONN cannot be read back on macOS - errno 42.)
		with socket_mod.create_connection(('127.0.0.1', port), timeout=2):
			pass  # accepted by the kernel backlog even with nothing serving yet
	finally:
		sock.close()


def test_the_server_binds_loopback_only() -> None:
	from nkqa.server.main import HOST, bind_port

	assert HOST == '127.0.0.1'  # never 0.0.0.0
	sock, _ = bind_port()
	try:
		assert sock.getsockname()[0] == '127.0.0.1'
	finally:
		sock.close()


def test_a_browser_gets_cors_headers_for_an_allowed_origin(client: TestClient) -> None:
	"""Without these the UI's fetch is blocked before the token is even considered."""
	preflight = client.options(
		'/workspace',
		headers={
			'Origin': 'tauri://localhost',
			'Access-Control-Request-Method': 'GET',
			'Access-Control-Request-Headers': 'authorization',
		},
	)
	assert preflight.status_code == 200
	assert preflight.headers['access-control-allow-origin'] == 'tauri://localhost'

	actual = client.get('/workspace', headers={'Authorization': f'Bearer {TOKEN}', 'Origin': 'tauri://localhost'})
	assert actual.status_code == 200
	assert actual.headers['access-control-allow-origin'] == 'tauri://localhost'


def test_an_unknown_origin_gets_no_cors_grant(client: TestClient) -> None:
	preflight = client.options(
		'/workspace', headers={'Origin': 'https://evil.example', 'Access-Control-Request-Method': 'GET'}
	)
	assert 'access-control-allow-origin' not in preflight.headers
	# and the request itself is refused by auth even with a valid token
	assert (
		client.get(
			'/workspace', headers={'Authorization': f'Bearer {TOKEN}', 'Origin': 'https://evil.example'}
		).status_code
		== 403
	)


def test_workspace_lists_configured_connectors(client: TestClient, ws: Workspace) -> None:
	ws.config_file.write_text(
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
	connectors = {c['name']: c for c in get(client, '/workspace').json()['connectors']}

	assert connectors['jira']['command'].startswith('npx -y mcp-remote')
	assert connectors['jira']['expose_to_executor'] is False  # the agent must not file bugs itself
	assert connectors['jira']['project'] == 'PROJ'
	assert connectors['seeder']['expose_to_executor'] is True
	assert connectors['seeder']['needs_env'] == ['KEY']


def test_connector_view_never_leaks_env_values(client: TestClient, ws: Workspace) -> None:
	"""`env:NAME` indirection exists so a resolved credential never leaves its process."""
	ws.config_file.write_text('mcp:\n  seeder:\n    command: node\n    env: {TOKEN: "env:REAL_SECRET"}\n')
	body = get(client, '/workspace').text
	assert 'REAL_SECRET' not in body  # only the key name travels
	assert '"needs_env":["TOKEN"]' in body.replace(' ', '')
