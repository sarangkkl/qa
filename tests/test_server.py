"""The sidecar contract: auth, reads, the command lifecycle, asks, cancellation."""

# starlette's TestClient boundary: it is typed against httpx2, so every response it hands
# back is Unknown here. Same treatment as the browser-use boundary elsewhere in the suite.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

import asyncio
import os
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
from nkqa.server.claude import find_claude as unpatched_find_claude  # the autouse fixture stubs the module's
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


@pytest.fixture(autouse=True)
def no_real_claude(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
	"""Never spawn the machine's real `claude`, never read the real ~/.claude."""
	from nkqa.server import claude

	async def fake_status(recheck: bool = False) -> dict[str, Any]:
		return {
			'path': '/x/claude',
			'version': '9.9.9',
			'logged_in': True,
			'auth_method': 'claude.ai',
			'plan': 'max',
			'ready': True,
		}

	monkeypatch.setattr(claude, 'status', fake_status)
	monkeypatch.setattr(claude, 'find_claude', lambda: '')
	monkeypatch.setenv('HOME', str(tmp_path / 'home'))


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
	assert health['workspace'] == str(ws.root) and health['claude']['ready'] is True and health['busy'] is False

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


def write_run(ws: Workspace, name: str, verdict: str, body: str = '') -> Path:
	"""A run directory as far as the report layer is concerned: a results.md with the header."""
	run_dir = ws.runs_dir / name
	run_dir.mkdir(parents=True)
	(run_dir / 'results.md').write_text(f'# auth/login — {verdict} — 2026-09-06 18:58\n{body}')
	return run_dir


def test_a_scenario_names_the_run_behind_its_verdict(client: TestClient, ws: Workspace) -> None:
	"""Without last_run the UI knows a scenario failed but has no way to show why."""
	assert get(client, '/workspace').json()['scenarios'][0]['last_run'] == ''
	assert get(client, '/scenarios/auth/login').json()['last_run'] == ''

	write_run(ws, 'auth-login--20260906-100000', 'FAIL')
	for body in (get(client, '/workspace').json()['scenarios'][0], get(client, '/scenarios/auth/login').json()):
		assert body['last_run'] == 'auth-login--20260906-100000'
		assert body['last_verdict'] == 'FAIL'


def test_last_run_and_last_verdict_agree_on_the_newest_run(client: TestClient, ws: Workspace) -> None:
	"""They resolve the run separately, so they can disagree - and then the UI shows the wrong report."""
	old = write_run(ws, 'auth-login--20260906-100000', 'FAIL')
	new = write_run(ws, 'auth-login--20260906-110000', 'PASS')
	os.utime(old / 'results.md', (1_700_000_000, 1_700_000_000))
	os.utime(new / 'results.md', (1_800_000_000, 1_800_000_000))

	body = get(client, '/scenarios/auth/login').json()
	assert body['last_run'] == 'auth-login--20260906-110000'
	assert body['last_verdict'] == 'PASS'


def test_the_report_named_by_last_run_is_servable(client: TestClient, ws: Workspace) -> None:
	"""The contract the Result tab rests on: last_run -> an artifact path that returns the report."""
	write_run(ws, 'auth-login--20260906-100000', 'FAIL', '| # | Step | Verdict |\n')
	name = get(client, '/scenarios/auth/login').json()['last_run']
	response = get(client, f'/artifacts/runs/{name}/results.md')
	assert response.status_code == 200 and '| # | Step | Verdict |' in response.text


def test_a_run_lists_its_evidence_as_files_that_can_be_opened(client: TestClient, ws: Workspace) -> None:
	"""The report links `videos/` and `conversation/`. Those are directories, and the artifact
	route serves files only - so the report's own evidence links can never resolve. Listing the
	files is what turns "evidence exists" into "evidence you can open"."""
	run_dir = write_run(ws, 'auth-login--20260906-100000', 'FAIL')
	(run_dir / 'steps').mkdir()
	(run_dir / 'conversation').mkdir()
	for n in (1, 2, 10):
		(run_dir / 'steps' / f'step-{n:03d}.png').write_bytes(b'\x89PNG')
		(run_dir / 'conversation' / f'conversation_abc_{n}.txt').write_text(f'step {n}')
	(run_dir / 'history.json').write_text('{"history": []}')

	body = get(client, '/runs/auth-login--20260906-100000').json()
	assert [p.rsplit('_', 1)[-1] for p in body['conversation']] == ['1.txt', '2.txt', '10.txt'], (
		'plain sorting puts _10 before _2, which reads as a shuffled transcript'
	)
	assert len(body['shots']) == 3

	for path in [*body['shots'], *body['conversation'], body['artifacts']['history']]:
		assert get(client, path).status_code == 200, f'{path} is listed but cannot be fetched'


def test_a_directory_is_not_servable_which_is_why_the_files_are_listed(client: TestClient, ws: Workspace) -> None:
	run_dir = write_run(ws, 'auth-login--20260906-100000', 'FAIL')
	(run_dir / 'conversation').mkdir()
	(run_dir / 'conversation' / 'conversation_abc_1.txt').write_text('hi')
	assert get(client, '/artifacts/runs/auth-login--20260906-100000/conversation/').status_code == 404


def test_a_run_with_no_evidence_reports_empty_lists_not_an_error(client: TestClient, ws: Workspace) -> None:
	"""Every existing run predates video recording; the UI has to say so rather than break."""
	write_run(ws, 'auth-login--20260906-100000', 'PASS')
	body = get(client, '/runs/auth-login--20260906-100000').json()
	assert body['videos'] == [] and body['shots'] == [] and body['conversation'] == []


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


def test_approving_several_scenarios_is_one_decision(client: TestClient, ws: Workspace) -> None:
	"""The chat's "Approve all": every body in one prompt, one yes approves each."""
	scenarios_mod.save(
		scenarios_mod.Scenario(
			id='auth/logout',
			path=ws.scenarios_dir / 'auth' / 'logout.md',
			title='Logout works',
			steps=[scenarios_mod.Step('Click Log out.', 'the login page shows')],
		)
	)
	asks: list[dict[str, Any]] = []
	with client.websocket_connect(f'/session?token={TOKEN}') as socket:
		socket.send_json({'type': 'command', 'id': 'c1', 'name': 'approve', 'args': {'id': 'auth/login auth/logout'}})
		while True:
			frame = socket.receive_json()
			if frame['type'] == 'ask':
				asks.append(frame)
				socket.send_json({'type': 'answer', 'id': frame['id'], 'value': 'y'})
			if frame['type'] == 'result':
				assert frame['code'] == 0
				break
	assert len(asks) == 1 and 'Login works' in asks[0]['body'] and 'Logout works' in asks[0]['body']
	states = {s.id: s.runnable() for s in scenarios_mod.load_all(ws.scenarios_dir)}
	assert states == {'auth/login': 'ok', 'auth/logout': 'ok'}


def test_a_scenario_comes_with_its_steps_parsed(client: TestClient) -> None:
	body = get(client, '/scenarios/auth/login').json()
	assert body['steps'] == [{'action': 'Open the login page.', 'expect': 'the form shows'}]


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


def test_workspace_carries_what_the_settings_page_needs(client: TestClient) -> None:
	"""The picker renders from this alone - no model names are hard-coded in TypeScript."""
	state = get(client, '/workspace').json()
	providers = {p['name']: p for p in state['providers']}
	assert {'anthropic', 'openai'} <= set(providers)
	assert providers['anthropic']['label'] == 'Anthropic'
	assert all(m['id'] and m['label'] and m['tier'] for m in providers['openai']['models'])
	# The current selection, so the form can open showing the truth rather than a guess.
	assert set(state['aliases']) == {'smart', 'fast'}


def test_choosing_a_model_over_the_socket_lands_in_the_config(client: TestClient, ws: Workspace) -> None:
	frames = run_frames(client, [{'type': 'command', 'id': 'm1', 'name': 'set-model', 'args': {'provider': 'openai'}}])
	assert frames[-1]['code'] == 0

	from nkqa import config as config_mod

	assert config_mod.load(ws.config_file).aliases['smart'].startswith('openai:')
	# And the endpoint reports it, which is what makes the form show the saved value.
	assert get(client, '/workspace').json()['aliases']['smart'].startswith('openai:')


def test_the_model_can_be_changed_while_a_job_is_parked(client: TestClient, ws: Workspace) -> None:
	"""Noticing the model is wrong happens *during* a run, which is when the runner is busy.

	Without `instant` the runner refuses this with `busy`, and the settings page would be dead
	exactly when you want it. `approve` parks on an ask, so the job is genuinely in flight.
	"""
	with client.websocket_connect(f'/session?token={TOKEN}') as socket:
		socket.send_json({'type': 'command', 'id': 'j1', 'name': 'approve', 'args': {'id': 'auth/login'}})
		while (parked := socket.receive_json())['type'] != 'ask':
			pass  # waiting for a human, so the runner is busy

		socket.send_json({'type': 'command', 'id': 'm1', 'name': 'set-model', 'args': {'provider': 'openai'}})
		while (frame := socket.receive_json())['type'] != 'result' or frame['job'] != 'm1':
			pass
		assert frame['code'] == 0, 'the settings page must work during a run'

		# Let the parked job finish rather than leaving it for socket teardown: the ask it is
		# sitting on is still live, and answering it is what the human would have done.
		socket.send_json({'type': 'answer', 'id': parked['id'], 'value': 'n'})
		while (frame := socket.receive_json())['type'] != 'result' or frame['job'] != 'j1':
			pass

	from nkqa import config as config_mod

	assert config_mod.load(ws.config_file).aliases['smart'].startswith('openai:')


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


FAKE_CLAUDE = """#!/bin/sh
if [ "$2" != "--output-format" ]; then echo '"Login flow planning"'; exit 0; fi
printf '%s\\n' "$@" > "{args}"
cat > "{prompt}"
echo '{{"type":"system","subtype":"init"}}'
echo 'not json'
echo '{{"type":"assistant","message":{{"content":[{{"type":"text","text":"On it."}}]}}}}'
"""


def test_say_is_a_claude_turn(
	client: TestClient, ws: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
	"""`say` spawns the user's own `claude` against this sidecar's /mcp, and streams it back."""
	from nkqa.server import claude

	script = tmp_path / 'claude'
	args_file, prompt_file = tmp_path / 'args.txt', tmp_path / 'prompt.txt'
	script.write_text(FAKE_CLAUDE.format(args=args_file, prompt=prompt_file))
	script.chmod(0o755)
	monkeypatch.setattr(claude, 'find_claude', lambda: str(script))

	def say(text: str, chat: str = '') -> list[dict[str, Any]]:
		with client.websocket_connect(f'/session?token={TOKEN}') as socket:
			socket.send_json({'type': 'say', 'id': 's1', 'text': text, **({'chat': chat} if chat else {})})
			frames: list[dict[str, Any]] = []
			while True:
				frames.append(socket.receive_json())
				if frames[-1]['type'] == 'result':
					break
			if not chat:  # a new chat is named by Claude once its first reply is in
				frames.append(socket.receive_json())
			return frames

	frames = say('-plan the login flow')
	assert frames[-1]['type'] == 'chat' and frames[-1]['title'] == 'Login flow planning'
	frames = frames[:-1]
	chat_id = next(f['id'] for f in frames if f['type'] == 'chat')
	assert claude.SESSION_ID.match(chat_id)
	said = [f['msg'] for f in frames if f['type'] == 'claude']
	assert said[-1]['message']['content'][0]['text'] == 'On it.' and len(said) == 2  # the junk line is dropped
	assert frames[-1]['code'] == 0
	assert prompt_file.read_text() == '-plan the login flow'  # stdin, so a leading '-' is not a flag
	args = args_file.read_text().splitlines()
	assert args[args.index('--session-id') + 1] == chat_id and '--strict-mcp-config' in args
	# The role goes in on every turn, and approving from chat text is not possible.
	assert args[args.index('--append-system-prompt') + 1] == '# Your role'  # the brief, one line per arg here
	assert 'You are Kiwame, the QA engineer' in args_file.read_text()
	assert 'mcp__nkqa__approve_scenario' in args[args.index('--disallowedTools') + 1]
	assert args[args.index('--setting-sources') + 1] == 'project,local'
	assert '/mcp' in args[args.index('--mcp-config') + 1] and TOKEN in args[args.index('--mcp-config') + 1]
	assert claude.read_titles(ws.chat_titles_file) == {chat_id: 'Login flow planning'}

	# The session file exists once Claude has written to it: the next message resumes it.
	folder = claude.project_dir(ws.root)
	folder.mkdir(parents=True)
	(folder / f'{chat_id}.jsonl').write_text('')
	frames = say('and the logout flow', chat_id)
	assert not any(f['type'] == 'chat' for f in frames)
	args = args_file.read_text().splitlines()
	assert args[args.index('--resume') + 1] == chat_id


def test_say_without_claude_installed_says_so(client: TestClient) -> None:
	frames = run_frames(client, [{'type': 'say', 'id': 's1', 'text': 'hi'}])
	assert frames[-1]['code'] == 2
	assert any('not installed' in f.get('text', '') for f in frames)


def test_empty_say_is_a_usage_error(client: TestClient) -> None:
	frames = run_frames(client, [{'type': 'say', 'id': 's1', 'text': '   '}])
	assert frames[-1]['code'] == 2 and 'empty message' in frames[-1]['error']


def test_chat_routes_read_claude_codes_own_sessions(client: TestClient, ws: Workspace) -> None:
	import json

	from nkqa.server import claude

	sid = '0b7e6c1a-1111-4222-8333-944455556666'
	folder = claude.project_dir(ws.root)
	folder.mkdir(parents=True)
	lines = [
		{'type': 'user', 'isMeta': True, 'message': {'content': 'caveat'}},
		{'type': 'user', 'message': {'content': '<command-name>/model</command-name>'}},
		{'type': 'user', 'message': {'content': 'plan the login flow'}},
		{'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'Drafting.'}]}},
		{'type': 'ai-title', 'aiTitle': 'Login flow scenarios', 'sessionId': sid},
		{'type': 'attachment', 'attachment': {}},
	]
	(folder / f'{sid}.jsonl').write_text('\n'.join(json.dumps(x) for x in lines) + '\nbroken{\n')
	(folder / 'not-a-session.jsonl').write_text('')

	chats = get(client, '/chats').json()['chats']
	assert [(c['id'], c['title'], c['turns']) for c in chats] == [(sid, 'Login flow scenarios', 1)]
	body = get(client, f'/chats/{sid}').json()
	assert body['title'] == 'Login flow scenarios'
	assert [m['type'] for m in body['messages']] == ['user', 'assistant']
	assert get(client, '/chats/0b7e6c1a-1111-4222-8333-000000000000').status_code == 404
	assert get(client, '/chats/..%2F..%2Fetc').status_code == 404


def test_a_custom_title_beats_claudes_and_the_first_message_is_the_fallback(tmp_path: Path) -> None:
	import json

	from nkqa.server import claude

	folder = claude.project_dir(tmp_path)
	folder.mkdir(parents=True)
	a, b = '0b7e6c1a-1111-4222-8333-94445555aaaa', '0b7e6c1a-1111-4222-8333-94445555bbbb'
	(folder / f'{a}.jsonl').write_text(
		'\n'.join(
			json.dumps(x)
			for x in (
				{'type': 'custom-title', 'customTitle': 'mine'},
				{'type': 'user', 'message': {'content': [{'type': 'text', 'text': 'hello'}]}},
				{'type': 'ai-title', 'aiTitle': 'theirs'},
			)
		)
	)
	(folder / f'{b}.jsonl').write_text(json.dumps({'type': 'user', 'message': {'content': 'x' * 100}}))
	titles = {c['id']: c['title'] for c in claude.list_sessions(tmp_path)}
	assert titles == {a: 'mine', b: 'x' * 60}


def test_an_attached_sheet_is_named_like_a_person_would() -> None:
	from nkqa.server.claude import readable

	note = '[Attached: imports/regression.xlsx — sheet "Cases": imports/regression--cases.csv, 3 rows; columns: ID]'
	assert readable(f'{note}\n\nImport these test cases.') == 'Import regression.xlsx'
	assert readable(f'{note}\n\nonly the Projects sheet') == 'only the Projects sheet (regression.xlsx)'
	assert readable('plan the login flow') == 'plan the login flow'


def test_project_dir_matches_claude_codes_naming() -> None:
	from nkqa.server import claude

	assert claude.project_dir(Path('/Users/g/dreams/nkqa/browser-use')).name == '-Users-g-dreams-nkqa-browser-use'
	assert claude.project_dir(Path('/tmp/my.app_x')).name == '-tmp-my-app-x'


def test_claude_never_inherits_an_api_key_from_a_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
	"""Claude Code prefers ANTHROPIC_API_KEY to the user's login: passing nkqa's on would bill it."""
	from nkqa.server.claude import child_env

	monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-from-a-dotenv')
	monkeypatch.setenv('KEEP_ME', '1')
	env = child_env('/opt/somewhere/bin/claude')
	assert 'ANTHROPIC_API_KEY' not in env and env['KEEP_ME'] == '1'
	assert env['PATH'].split(os.pathsep)[0] == '/opt/somewhere/bin'


def test_only_a_paying_signed_in_user_is_ready() -> None:
	from nkqa.server.claude import ready

	assert ready(True, 'claude.ai', 'firstParty', 'max') and ready(True, 'claude.ai', 'firstParty', 'pro')
	assert not ready(True, 'claude.ai', 'firstParty', '') and not ready(True, 'claude.ai', 'firstParty', 'free')
	assert not ready(False, '', 'firstParty', '')
	assert ready(True, 'api_key', 'firstParty', '')  # console billing
	assert ready(False, '', 'bedrock', '')  # the cloud provider's own credentials


def test_open_serves_two_fixed_pages_only(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
	import webbrowser

	opened: list[str] = []

	def fake_open(url: str) -> bool:
		opened.append(url)
		return True

	monkeypatch.setattr(webbrowser, 'open', fake_open)
	headers = {'Authorization': f'Bearer {TOKEN}'}
	assert client.post('/open/upgrade', headers=headers).json() == {'ok': True}
	assert client.post('/open/https:%2F%2Fevil.test', headers=headers).status_code == 404
	assert opened == ['https://claude.ai/upgrade']


def test_mcp_needs_the_token(client: TestClient) -> None:
	body = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}}
	assert client.post('/mcp', json=body).status_code == 401
	accept = {'Accept': 'application/json, text/event-stream'}
	ok = client.post('/mcp', json=body, headers={'Authorization': f'Bearer {TOKEN}', **accept})
	assert ok.status_code != 401
	evil = {'Authorization': f'Bearer {TOKEN}', 'Origin': 'https://evil.test', **accept}
	assert client.post('/mcp', json=body, headers=evil).status_code == 401


def test_a_sheet_uploads_into_imports(client: TestClient, ws: Workspace) -> None:
	assert client.post('/imports?name=cases.csv', content=b'ID,Title\nC1,Login\n').status_code == 401
	headers = {'Authorization': f'Bearer {TOKEN}'}
	body = client.post('/imports?name=cases.csv', content=b'ID,Title\nC1,Login\n', headers=headers).json()
	assert body['path'] == 'imports/cases.csv' and (ws.root / body['path']).is_file()
	assert body['sheets'][0]['rows'] == 1 and body['note'].startswith('[Attached: imports/cases.csv')
	bad = client.post('/imports?name=cases.docx', content=b'x', headers=headers)
	assert bad.status_code == 400 and 'Attach an .xlsx' in bad.json()['detail']
	broken = client.post('/imports?name=cases.xlsx', content=b'not a zip', headers=headers)
	assert broken.status_code == 400 and not (ws.imports_dir / 'cases.xlsx').exists()


def test_the_relay_denies_when_no_window_is_open() -> None:
	from nkqa.server.channel import Relay
	from nkqa.ui import Ask

	assert asyncio.run(Relay().ask(Ask(kind='confirm', prompt='delete?'))) == ''


def test_closing_the_window_forgets_the_session(ws: Workspace) -> None:
	"""The window and Claude share one HITL, so closing the window must end what it granted."""
	auth.set_token(TOKEN)
	app = create_app(ws)
	hitl = app.state.mcp_session.require_hitl()
	with TestClient(app) as c, c.websocket_connect(f'/session?token={TOKEN}') as socket:
		assert app.state.relay.target is not None
		hitl.secrets['password'] = 'hunter2'
		hitl.autonomy = 'allow'
		socket.send_json({'type': 'nonsense'})
		socket.receive_json()
		socket.close()
		time.sleep(0.2)
		assert hitl.secrets == {} and hitl.autonomy == 'ask'
		assert app.state.relay.target is None
	auth.set_token('')


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


def test_a_step_screenshot_is_served_at_the_path_the_step_event_emits(client: TestClient, ws: Workspace) -> None:
	"""Closes the loop: the live pane was handed a temp-dir filesystem path it could never load.

	`stream.step_event` now copies the shot into the run and emits this exact URL shape, so the
	two halves have to agree or the stage goes black again.
	"""
	from nkqa.execution import stream

	run_dir = ws.runs_dir / 'checkout--20260906-1200'
	run_dir.mkdir(parents=True)
	source = ws.root / 'from-a-temp-dir.png'
	source.write_bytes(b'\x89PNG fake')

	url = stream.keep_shot(str(source), run_dir, 7)

	assert url == '/artifacts/runs/checkout--20260906-1200/steps/step-007.png'
	served = get(client, url)
	assert served.status_code == 200
	assert served.content == b'\x89PNG fake'


def test_connecting_jira_shows_up_in_the_workspace(client: TestClient) -> None:
	"""The round trip Settings depends on: press Connect, the row appears.

	`hasJira` in the frontend keys off this list, and it gates plan-from-ticket and file-a-bug
	as well as the Sign in button - so if this does not come back, a third of the UI stays
	hidden with no explanation.
	"""
	assert get(client, '/workspace').json()['connectors'] == []

	frames = run_frames(client, [{'type': 'command', 'id': 'k1', 'name': 'connect', 'args': {'name': 'jira'}}])
	assert frames[-1]['code'] == 0

	connectors = get(client, '/workspace').json()['connectors']
	assert [c['name'] for c in connectors] == ['jira']
	assert 'npx' in connectors[0]['command']
	# The safety default has to survive the round trip, not just the write.
	assert connectors[0]['expose_to_executor'] is False


def test_the_project_key_reaches_the_ui(client: TestClient) -> None:
	run_frames(
		client,
		[{'type': 'command', 'id': 'k1', 'name': 'connect', 'args': {'name': 'jira', 'project': 'proj'}}],
	)
	connectors = get(client, '/workspace').json()['connectors']
	assert connectors[0]['project'] == 'PROJ'


def test_a_connector_can_be_added_while_a_job_is_parked(client: TestClient, ws: Workspace) -> None:
	"""Settings has to work during a run - `connect` is instant for the same reason set-model is."""
	with client.websocket_connect(f'/session?token={TOKEN}') as socket:
		socket.send_json({'type': 'command', 'id': 'j1', 'name': 'approve', 'args': {'id': 'auth/login'}})
		while (parked := socket.receive_json())['type'] != 'ask':
			pass

		socket.send_json({'type': 'command', 'id': 'k1', 'name': 'connect', 'args': {'name': 'jira'}})
		while (frame := socket.receive_json())['type'] != 'result' or frame['job'] != 'k1':
			pass
		assert frame['code'] == 0, 'the connectors page must work during a run'

		socket.send_json({'type': 'answer', 'id': parked['id'], 'value': 'n'})
		while (frame := socket.receive_json())['type'] != 'result' or frame['job'] != 'j1':
			pass

	from nkqa import config as config_mod

	assert config_mod.load(ws.config_file).mcp_server('jira') is not None


def test_a_command_that_raises_still_returns_a_result(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
	"""A handler that throws used to leave the job at `working…` forever.

	The exception escaped the handler, `JobRunner.wait` only catches CancelledError, and the
	task that sends the `result` frame died before sending one - so the UI had no code, no error
	and no way to tell a thrown command from a slow one. Reported three times as "it just keeps
	working", from three different causes; this is the one that covers all of them.
	"""
	from nkqa.shell.commands import REGISTRY

	async def explode(ctx: Any, args: dict[str, Any]) -> int:
		raise RuntimeError('jira fell over')

	monkeypatch.setattr(REGISTRY['scenarios'], 'handler', explode)

	frames = run_frames(client, [{'type': 'command', 'id': 'c1', 'name': 'scenarios'}])

	assert frames[-1]['type'] == 'result'
	assert frames[-1]['code'] == 1
	assert 'jira fell over' in frames[-1]['error']
	# ...and the human is told in the log too, not only in a field a UI might ignore.
	assert any('jira fell over' in f.get('text', '') for f in frames if f['type'] == 'event')


def test_library_tests_move_between_folders(client: TestClient, ws: Workspace) -> None:
	headers = {'Authorization': f'Bearer {TOKEN}'}
	assert client.post('/library/folders', params={'name': 'Smoke'}, headers=headers).json() == {'path': 'smoke'}
	assert get(client, '/library').json()['folders'] == ['auth', 'smoke']
	assert client.post('/library/move', params={'id': 'auth/login', 'folder': 'smoke'}, headers=headers).json() == {
		'id': 'smoke/login'
	}
	refused = client.post('/library/move', params={'id': 'smoke/login', 'folder': '../..'}, headers=headers)
	assert refused.status_code == 400
	assert client.delete('/library/folders', params={'path': 'smoke'}, headers=headers).status_code == 400
	assert client.delete('/library/tests', params={'id': 'smoke/login'}, headers=headers).json() == {'ok': True}
	assert client.delete('/library/folders', params={'path': 'smoke'}, headers=headers).json() == {'ok': True}
	# the webview preflights a DELETE; without it in CORS the delete buttons silently do nothing
	preflight = {'Origin': 'tauri://localhost', 'Access-Control-Request-Method': 'DELETE'}
	assert client.options('/library/tests', headers=preflight).status_code == 200


def test_a_standby_server_takes_its_workspace_later(ws: Workspace) -> None:
	"""The desktop pre-starts one of these, so opening a workspace skips the slow start entirely."""
	import json
	import subprocess
	import sys
	import urllib.request

	proc = subprocess.Popen(
		[sys.executable, '-m', 'nkqa.server.main', '--standby', '--exit-with-parent'],
		stdin=subprocess.PIPE,
		stdout=subprocess.PIPE,
		stderr=subprocess.DEVNULL,
	)
	assert proc.stdin and proc.stdout
	try:
		assert json.loads(proc.stdout.readline()) == {'standby': True}
		proc.stdin.write(json.dumps({'workspace': str(ws.root)}).encode() + b'\n')
		proc.stdin.flush()
		handshake = json.loads(proc.stdout.readline())
		assert handshake['ready'] and handshake['workspace'] == str(ws.root)
		request = urllib.request.Request(
			f'http://127.0.0.1:{handshake["port"]}/health', headers={'Authorization': f'Bearer {handshake["token"]}'}
		)
		with urllib.request.urlopen(request, timeout=10) as response:
			assert json.loads(response.read())['busy'] is False
		proc.stdin.close()  # the app going away
		assert proc.wait(timeout=15) is not None, '--exit-with-parent still watches stdin after the request line'
	finally:
		if proc.poll() is None:
			proc.kill()


def test_an_npm_shim_on_windows_runs_through_node_not_cmd_exe(tmp_path: Path) -> None:
	"""cmd.exe cuts arguments at newlines; the system-prompt brief has plenty."""
	from nkqa.server import claude

	npm = tmp_path / 'npm'
	cli = npm / 'node_modules' / '@anthropic-ai' / 'claude-code' / 'cli.js'
	cli.parent.mkdir(parents=True)
	cli.write_text('// cli')
	(npm / 'node.exe').write_text('')
	shim = npm / 'claude.cmd'
	shim.write_text('@node cli.js %*')
	argv = claude.launcher(str(shim))
	assert argv[-1] == str(cli) and argv[0].endswith(('node', 'node.exe'))
	assert claude.launcher(str(tmp_path / 'claude.exe')) == [str(tmp_path / 'claude.exe')]
	assert claude.launcher('/usr/local/bin/claude') == ['/usr/local/bin/claude']
	cli.unlink()  # a shim we cannot see through is still better than nothing
	assert claude.launcher(str(shim)) == [str(shim)]


def test_claude_is_found_where_npm_puts_it_on_windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	import shutil

	npm = tmp_path / 'AppData' / 'npm'
	npm.mkdir(parents=True)
	(npm / 'claude.cmd').write_text('')
	real_is_file = Path.is_file

	def nothing_on_path(name: str) -> None:
		return None

	def only_ours(self: Path) -> bool:  # a Windows box: no Homebrew, no ~/.local
		return str(self).startswith(str(tmp_path)) and real_is_file(self)

	monkeypatch.setattr(shutil, 'which', nothing_on_path)
	monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'home')
	monkeypatch.setattr(Path, 'is_file', only_ours)
	monkeypatch.setenv('APPDATA', str(tmp_path / 'AppData'))
	monkeypatch.setenv('SHELL', '')
	assert unpatched_find_claude() == str(npm / 'claude.cmd')
