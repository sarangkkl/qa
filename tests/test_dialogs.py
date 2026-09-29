"""The native-dialog channel: every way a dialog can fail answers '', which is deny.

Three backends print the same three shapes, so the parser is shared - and so are these tests:
every platform case runs the same assertions against its own argv.
"""

import asyncio
from typing import Any

import pytest

from nkqa import dialogs
from nkqa.ui import Ask, Event

PLATFORMS = ['macos', 'windows', 'linux']


class FakeProcess:
	def __init__(self, returncode: int, stdout: str, hold: 'asyncio.Event | None' = None):
		self.returncode = returncode
		self.stdout = stdout
		self.hold = hold
		self.killed = False

	async def communicate(self) -> tuple[bytes, bytes]:
		if self.hold is not None:
			await self.hold.wait()
		return self.stdout.encode(), b''

	def kill(self) -> None:
		self.killed = True
		if self.hold is not None:
			self.returncode = -9
			self.hold.set()


def scripted(
	monkeypatch: pytest.MonkeyPatch, which: str, returncode: int, stdout: str
) -> list[tuple[list[str], dict[str, str]]]:
	"""Pretend to be `which` and answer every dialog with a canned result."""
	calls: list[tuple[list[str], dict[str, str]]] = []

	async def fake_spawn(argv: list[str], env: dict[str, str]) -> Any:
		calls.append((argv, env))
		return FakeProcess(returncode, stdout)

	monkeypatch.setattr(dialogs, 'spawn', fake_spawn)
	monkeypatch.setattr(dialogs, 'backend', lambda: which)
	monkeypatch.setattr(dialogs, 'powershell', lambda: 'powershell')
	return calls


def ask(request: Ask) -> str:
	return asyncio.run(dialogs.DialogChannel().ask(request))


def everything(argv: list[str], env: dict[str, str]) -> str:
	"""All the text a backend was handed, wherever it put it."""
	return '\n'.join([*argv, *env.values()])


@pytest.mark.parametrize('which', PLATFORMS)
def test_choice_returns_the_letter(monkeypatch: pytest.MonkeyPatch, which: str) -> None:
	calls = scripted(monkeypatch, which, 0, 'a  allow always\n')
	prompt = 'QA agent requests permission: delete "the" user\nkey: del-user'
	assert ask(Ask('choice', prompt, options=['y', 's', 'a', 'n'])) == 'a'
	text = everything(*calls[0])
	assert prompt in text  # verbatim: quotes and newlines are never spliced into a script
	for option in ('y  allow once', 's  allow this session', 'a  allow always', 'n  deny'):
		assert option in text


@pytest.mark.parametrize('which', PLATFORMS)
def test_cancel_and_timeout_deny(monkeypatch: pytest.MonkeyPatch, which: str) -> None:
	scripted(monkeypatch, which, 1, 'cancelled')
	assert ask(Ask('choice', 'p', options=['y', 'n'])) == ''
	assert ask(Ask('secret', 'p', key='password')) == ''
	assert ask(Ask('confirm', 'Approve?', body='...')) == ''
	scripted(monkeypatch, which, 0, '')  # closed, or gave up waiting
	assert ask(Ask('text', 'p')) == ''
	assert ask(Ask('confirm', 'Approve?', body='...')) == ''


@pytest.mark.parametrize('which', PLATFORMS)
def test_confirm_shows_the_body_and_maps_the_button(monkeypatch: pytest.MonkeyPatch, which: str) -> None:
	body = '# Login works\n1. Open the page'
	calls = scripted(monkeypatch, which, 0, 'Confirm')
	assert ask(Ask('confirm', 'Approve this scenario? [y/N]: ', body=body)) == 'y'
	assert body in everything(*calls[0])
	scripted(monkeypatch, which, 0, 'Cancel')
	assert ask(Ask('confirm', 'Approve?', body=body)) == ''


@pytest.mark.parametrize('which', PLATFORMS)
def test_secret_is_hidden_and_never_emitted(
	monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], which: str
) -> None:
	calls = scripted(monkeypatch, which, 0, 'hunter2\n')
	ch = dialogs.DialogChannel()
	assert asyncio.run(ch.ask(Ask('secret', 'password for qa: ', key='password'))) == 'hunter2'
	argv, env = calls[0]
	masked = {'macos': 'with hidden answer', 'windows': 'UseSystemPasswordChar', 'linux': '--hide-text'}[which]
	assert masked in '\n'.join(argv)
	assert env.get('NKQA_KIND', 'secret') == 'secret'
	asyncio.run(ch.emit(Event('log', 'typed the credential')))
	asyncio.run(ch.emit(Event('frame', 'base64...')))
	err = capsys.readouterr().err
	assert 'hunter2' not in err and '[log] typed the credential' in err and 'base64' not in err


def test_windows_sends_text_through_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
	"""PowerShell quoting is where a scenario body would otherwise become executable script."""
	calls = scripted(monkeypatch, 'windows', 0, 'Confirm')
	ask(Ask('confirm', 'Approve?', body='"; Remove-Item -Recurse C:\\ #'))
	argv, env = calls[0]
	assert argv[0] == 'powershell' and argv[-1] == dialogs.WINDOWS
	assert env['NKQA_BODY'] == '"; Remove-Item -Recurse C:\\ #'
	assert 'Remove-Item' not in '\n'.join(argv)


def test_linux_confirm_prints_the_token_the_parser_expects(monkeypatch: pytest.MonkeyPatch) -> None:
	"""zenity --question answers with an exit code and no output, so the shell echoes it."""
	calls = scripted(monkeypatch, 'linux', 0, 'Confirm')
	ask(Ask('confirm', 'Approve?', body='the scenario'))
	argv, _ = calls[0]
	assert argv[0] == 'sh' and 'echo Confirm' in argv[2]
	assert argv[-1] == 'the scenario'  # argv, not interpolated into the script


def test_backend_picks_by_platform(monkeypatch: pytest.MonkeyPatch) -> None:
	def installed(name: str) -> str:
		return f'/usr/bin/{name}'

	def missing(name: str) -> str | None:
		return None

	monkeypatch.setattr(dialogs.shutil, 'which', installed)
	for platform, expected in (('darwin', 'macos'), ('win32', 'windows'), ('linux', 'linux')):
		monkeypatch.setattr(dialogs.sys, 'platform', platform)
		assert dialogs.backend() == expected
	monkeypatch.setattr(dialogs.shutil, 'which', missing)
	assert dialogs.backend() == ''


def test_abandon_closes_the_dialog_and_denies(monkeypatch: pytest.MonkeyPatch) -> None:
	"""The client died mid-prompt: the human's dialog must go away and the ask must resolve ''."""
	hold = asyncio.Event()
	proc = FakeProcess(0, 'Confirm', hold)

	async def fake_spawn(argv: list[str], env: dict[str, str]) -> Any:
		return proc

	monkeypatch.setattr(dialogs, 'spawn', fake_spawn)
	monkeypatch.setattr(dialogs, 'backend', lambda: 'macos')

	async def go() -> str:
		ch = dialogs.DialogChannel()
		pending = asyncio.ensure_future(ch.ask(Ask('confirm', 'Approve?', body='x')))
		await asyncio.sleep(0)
		assert proc in ch.live
		ch.abandon()
		answer = await pending
		assert proc.killed and not ch.live
		assert await ch.ask(Ask('text', 'late')) == ''  # nothing opens after the client is gone
		return answer

	assert asyncio.run(go()) == ''


def test_no_native_dialog_means_deny_out_loud(
	monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
	monkeypatch.setattr(dialogs, 'backend', lambda: '')
	monkeypatch.setattr(dialogs, 'tk_ask', lambda request: '')  # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]
	assert ask(Ask('choice', 'p', options=['y', 'n'])) == ''
	assert 'no native dialog' in capsys.readouterr().err
