from pathlib import Path

import pytest

from nkqa import cli, workspace
from nkqa import scenarios as scenarios_mod


def run_cli(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> int:
	monkeypatch.setattr('sys.argv', ['qa', *argv])
	with pytest.raises(SystemExit) as exc:
		cli.main()
	return exc.value.code if isinstance(exc.value.code, int) else 1


def test_version_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
	assert run_cli(monkeypatch, ['version']) == 0


def test_no_command_exits_two(monkeypatch: pytest.MonkeyPatch) -> None:
	assert run_cli(monkeypatch, []) == 2


def test_list_outside_workspace_exits_two(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
	monkeypatch.chdir(tmp_path)
	assert run_cli(monkeypatch, ['list']) == 2


def test_init_then_list(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
	monkeypatch.chdir(tmp_path)
	assert run_cli(monkeypatch, ['init']) == 0
	assert workspace.find(tmp_path) is not None
	assert run_cli(monkeypatch, ['list']) == 0  # empty workspace lists fine


def test_replay_unknown_name_exits_two(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
	monkeypatch.chdir(tmp_path)
	workspace.create(tmp_path)
	assert run_cli(monkeypatch, ['replay', 'does-not-exist']) == 2


def write_draft(tmp_path: Path) -> scenarios_mod.Scenario:
	s = scenarios_mod.Scenario(
		id='auth/login',
		path=tmp_path / 'scenarios' / 'auth' / 'login.md',
		title='Login works',
		steps=[scenarios_mod.Step('Open the login page.')],
	)
	scenarios_mod.save(s)
	return s


def test_run_with_url_hints_explore(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
	monkeypatch.chdir(tmp_path)
	workspace.create(tmp_path)
	assert run_cli(monkeypatch, ['run', 'https://example.com']) == 2


def test_run_refuses_draft(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
	monkeypatch.chdir(tmp_path)
	workspace.create(tmp_path)
	write_draft(tmp_path)
	assert run_cli(monkeypatch, ['run', 'auth/login']) == 2
	assert run_cli(monkeypatch, ['run', 'no/such']) == 2


def test_approve_then_scenarios(
	monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
	monkeypatch.chdir(tmp_path)
	workspace.create(tmp_path)
	write_draft(tmp_path)

	monkeypatch.setattr('builtins.input', lambda _prompt='': 'n')
	assert run_cli(monkeypatch, ['approve', 'auth/login']) == 1  # declined

	monkeypatch.setattr('builtins.input', lambda _prompt='': 'y')
	assert run_cli(monkeypatch, ['approve', 'auth/login']) == 0
	assert run_cli(monkeypatch, ['approve', 'auth/login']) == 0  # already approved, idempotent

	assert run_cli(monkeypatch, ['scenarios']) == 0
	out = capsys.readouterr().out
	assert 'approved' in out and 'auth/login' in out
