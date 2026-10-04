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


def test_no_command_outside_workspace_exits_two(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
	monkeypatch.chdir(tmp_path)  # bare `qa` opens the shell, which needs a workspace
	assert run_cli(monkeypatch, []) == 2


def test_list_outside_workspace_exits_two(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
	monkeypatch.chdir(tmp_path)
	assert run_cli(monkeypatch, ['list']) == 2


def test_init_then_list(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
	monkeypatch.chdir(tmp_path)
	assert run_cli(monkeypatch, ['init']) == 0
	assert workspace.find(tmp_path) is not None
	assert run_cli(monkeypatch, ['list']) == 0  # empty workspace lists fine


def test_init_warns_when_the_folder_already_had_its_own_claude_md(tmp_path: Path) -> None:
	"""Claude Code skips its native AGENTS.md read whenever a CLAUDE.md exists, and we refuse to
	edit one we did not write - so the human has to be told, or the brief is silently unread."""
	import asyncio

	from conftest import FakeChannel

	from nkqa import actions

	(tmp_path / 'CLAUDE.md').write_text('# their own conventions\n')
	ch = FakeChannel()
	assert asyncio.run(actions.init(ch, tmp_path)) == 0
	assert '@AGENTS.md' in ch.out and 'already had its own CLAUDE.md' in ch.out

	quiet = FakeChannel()
	assert asyncio.run(actions.init(quiet, tmp_path / 'fresh')) == 0
	assert 'already had its own' not in quiet.out


def test_mcp_is_a_workspace_free_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
	"""`kiwame mcp` must start outside a workspace (the agent picks one later), but a --workspace
	that is not one is a usage error, like every other bad argument."""
	args = cli.build_parser().parse_args(['mcp', '--workspace', 'x'])
	assert args.command == 'mcp' and args.workspace == 'x'
	monkeypatch.chdir(tmp_path)
	assert run_cli(monkeypatch, ['mcp', '--workspace', str(tmp_path / 'nope')]) == 2


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
