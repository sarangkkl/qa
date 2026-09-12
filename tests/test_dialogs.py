"""The native-dialog channel: every way a dialog can fail answers '', which is deny."""

import asyncio
from typing import Any

import pytest

from nkqa import dialogs
from nkqa.ui import Ask, Event


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


def scripted(monkeypatch: pytest.MonkeyPatch, returncode: int, stdout: str) -> list[tuple[str, list[str]]]:
	"""Replace osascript with a canned answer; return the calls it received."""
	calls: list[tuple[str, list[str]]] = []

	async def fake_spawn(script: str, args: list[str]) -> Any:
		calls.append((script, args))
		return FakeProcess(returncode, stdout)

	monkeypatch.setattr(dialogs, 'spawn_osascript', fake_spawn)
	monkeypatch.setattr(dialogs, 'has_osascript', lambda: True)
	return calls


def ask(request: Ask) -> str:
	return asyncio.run(dialogs.DialogChannel().ask(request))


def test_choice_returns_the_letter_and_passes_text_as_argv(monkeypatch: pytest.MonkeyPatch) -> None:
	calls = scripted(monkeypatch, 0, 'a  allow always\n')
	prompt = 'QA agent requests permission: delete "the" user\nkey: del-user'
	assert ask(Ask('choice', prompt, options=['y', 's', 'a', 'n'])) == 'a'
	script, args = calls[0]
	assert script == dialogs.CHOOSE
	assert args[0] == prompt  # verbatim: quotes and newlines are never spliced into the script
	assert args[1:] == ['y  allow once', 's  allow this session', 'a  allow always', 'n  deny']


def test_cancel_and_failure_both_deny(monkeypatch: pytest.MonkeyPatch) -> None:
	scripted(monkeypatch, 1, 'User canceled.')
	assert ask(Ask('choice', 'p', options=['y', 'n'])) == ''
	assert ask(Ask('secret', 'p', key='password')) == ''
	assert ask(Ask('confirm', 'Approve?', body='...')) == ''
	scripted(monkeypatch, 0, '')  # gave up after the timeout
	assert ask(Ask('text', 'p')) == ''


def test_confirm_maps_the_button_and_shows_the_body(monkeypatch: pytest.MonkeyPatch) -> None:
	calls = scripted(monkeypatch, 0, 'Confirm')
	assert ask(Ask('confirm', 'Approve this scenario? [y/N]: ', body='# Login works\n1. Open the page')) == 'y'
	script, args = calls[0]
	assert script == dialogs.CONFIRM
	assert args == ['Approve this scenario? [y/N]: ', '# Login works\n1. Open the page']
	scripted(monkeypatch, 0, 'Cancel')
	assert ask(Ask('confirm', 'Approve?', body='x')) == ''


def test_secret_comes_back_hidden_and_is_never_emitted(
	monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
	calls = scripted(monkeypatch, 0, 'hunter2\n')
	ch = dialogs.DialogChannel()
	assert asyncio.run(ch.ask(Ask('secret', 'password for qa: ', key='password'))) == 'hunter2'
	assert calls[0][0] == dialogs.SECRET and 'with hidden answer' in dialogs.SECRET
	asyncio.run(ch.emit(Event('log', 'typed the credential')))
	asyncio.run(ch.emit(Event('frame', 'base64...')))
	err = capsys.readouterr().err
	assert 'hunter2' not in err and '[log] typed the credential' in err and 'base64' not in err


def test_abandon_closes_the_dialog_and_denies(monkeypatch: pytest.MonkeyPatch) -> None:
	"""The client died mid-prompt: the human's dialog must go away and the ask must resolve ''."""
	hold = asyncio.Event()
	proc = FakeProcess(0, 'Confirm', hold)

	async def fake_spawn(script: str, args: list[str]) -> Any:
		return proc

	monkeypatch.setattr(dialogs, 'spawn_osascript', fake_spawn)
	monkeypatch.setattr(dialogs, 'has_osascript', lambda: True)

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
	monkeypatch.setattr(dialogs, 'has_osascript', lambda: False)
	monkeypatch.setattr(dialogs, 'tk_ask', lambda request: '')  # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]
	assert ask(Ask('choice', 'p', options=['y', 'n'])) == ''
	assert 'no native dialog' in capsys.readouterr().err
