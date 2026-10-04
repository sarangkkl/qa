from pathlib import Path

import pytest

from nkqa import library
from nkqa import scenarios as scenarios_mod
from nkqa import workspace as workspace_mod
from nkqa.execution.playback import Recording
from nkqa.workspace import Workspace


def saved_test(ws: Workspace, test_id: str) -> None:
	s = scenarios_mod.Scenario(
		id=test_id,
		path=ws.scenarios_dir / f'{test_id}.md',
		title='Login works',
		steps=[scenarios_mod.Step('Open the login page.', 'the form shows')],
	)
	scenarios_mod.approve(s, 'qa')
	scenarios_mod.save(s)
	library.write(ws.recording_file(test_id), Recording(scenario_id=test_id, approved_hash=s.approved_hash))


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
	workspace = workspace_mod.create(tmp_path)
	saved_test(workspace, 'auth/login')
	return workspace


def test_a_moved_test_keeps_its_approval_and_recording(ws: Workspace) -> None:
	assert library.make_folder(ws, '', 'Smoke Tests!') == 'smoke-tests'
	assert library.make_folder(ws, 'smoke-tests', 'login') == 'smoke-tests/login'
	assert (ws.scenarios_dir / 'smoke-tests' / 'login' / '.gitkeep').is_file()
	assert library.folders(ws) == ['auth', 'smoke-tests', 'smoke-tests/login']

	assert library.move(ws, 'auth/login', 'smoke-tests/login') == 'smoke-tests/login/login'
	assert not (ws.scenarios_dir / 'auth' / 'login.md').exists()
	assert not ws.recording_file('auth/login').exists()
	moved = library.load(ws, 'smoke-tests/login/login')
	assert moved and moved.scenario_id == 'smoke-tests/login/login'
	assert [(t['id'], t['folder']) for t in library.entries(ws)] == [('smoke-tests/login/login', 'smoke-tests/login')]

	assert library.move(ws, 'smoke-tests/login/login', '') == 'login'
	assert [t['id'] for t in library.entries(ws)] == ['login']


def test_moves_that_clash_or_leave_the_library_are_refused(ws: Workspace) -> None:
	saved_test(ws, 'login')
	with pytest.raises(ValueError, match='already exists'):
		library.move(ws, 'auth/login', '')
	for bad in ('..', '../x', '/tmp', 'auth/../..', '.nkqa'):
		with pytest.raises(ValueError):
			library.move(ws, 'auth/login', bad)
	with pytest.raises(ValueError, match='No folder'):
		library.move(ws, 'auth/login', 'nowhere')
	with pytest.raises(ValueError):
		library.make_folder(ws, '..', 'x')


def test_delete_takes_the_test_and_keeps_its_runs(ws: Workspace) -> None:
	run = ws.scenario_run_dir('auth/login')
	run.mkdir(parents=True)
	library.delete(ws, 'auth/login')
	assert not (ws.scenarios_dir / 'auth' / 'login.md').exists()
	assert not ws.recording_file('auth/login').exists()
	assert run.is_dir()
	with pytest.raises(ValueError, match='No test'):
		library.delete(ws, 'auth/login')


def test_only_an_empty_folder_can_be_removed(ws: Workspace) -> None:
	with pytest.raises(ValueError, match='move or delete'):
		library.remove_folder(ws, 'auth')
	library.make_folder(ws, 'auth', 'empty')
	library.delete(ws, 'auth/login')
	library.remove_folder(ws, 'auth')
	assert library.folders(ws) == []
	with pytest.raises(ValueError):
		library.remove_folder(ws, '')
