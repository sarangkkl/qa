from pathlib import Path

import pytest

from nkqa import cli, workspace


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
