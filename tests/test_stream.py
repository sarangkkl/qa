"""Making a run visible: the narration forwarded, and the screenshot the UI can fetch.

A crawl once ran for seventeen minutes showing nothing at all - no narration, no live view -
because it was the one runner that never entered `forward`. And the live pane's black box was
not an empty state: the step screenshot it was handed was a temp-directory filesystem path,
so the <img> could never load it.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeChannel

from nkqa.execution import stream
from nkqa.ui import TerminalChannel


class FakeHistory:
	def __init__(self, shot: str | None):
		self.shot = shot

	def model_thoughts(self) -> list[Any]:
		return []

	def action_names(self) -> list[str]:
		return ['click']

	def screenshot_paths(self) -> list[str | None]:
		return [self.shot]

	def urls(self) -> list[str]:
		return ['https://shop.test/cart']


class FakeAgent:
	def __init__(self, shot: str | None = None):
		self.history = FakeHistory(shot)


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
	d = tmp_path / 'runs' / 'checkout--20260906-1200'
	d.mkdir(parents=True)
	return d


def test_the_step_screenshot_becomes_something_the_ui_can_fetch(tmp_path: Path, run_dir: Path) -> None:
	"""browser-use writes it to a temp dir, which /artifacts will not serve and <img> cannot load."""
	elsewhere = tmp_path / 'T' / 'browser_use_agent_abc' / 'screenshots'
	elsewhere.mkdir(parents=True)
	shot = elsewhere / 'step_1.png'
	shot.write_bytes(b'\x89PNG fake')

	event = stream.step_event(FakeAgent(str(shot)), 3, run_dir)

	url = event.data['screenshot']
	assert url == '/artifacts/runs/checkout--20260906-1200/steps/step-003.png'
	assert not Path(url).is_absolute() or url.startswith('/artifacts/'), 'never a filesystem path'
	# ...and the bytes are now inside the run, so the evidence survives the temp dir being cleaned.
	assert (run_dir / 'steps' / 'step-003.png').read_bytes() == b'\x89PNG fake'


def test_a_step_with_no_usable_screenshot_omits_the_key(run_dir: Path) -> None:
	"""Better an empty stage with a caption than a broken image nobody can diagnose."""
	assert 'screenshot' not in stream.step_event(FakeAgent(None), 1, run_dir).data
	# A path that does not exist must not produce a key either.
	assert 'screenshot' not in stream.step_event(FakeAgent('/nope/gone.png'), 1, run_dir).data
	# No run dir to copy into (the CLI's own path) is the same story.
	assert 'screenshot' not in stream.step_event(FakeAgent('/nope/gone.png'), 1, None).data


def test_the_step_still_carries_what_it_did(run_dir: Path) -> None:
	event = stream.step_event(FakeAgent(None), 2, run_dir)
	assert event.kind == 'step'
	assert event.data['n'] == 2 and event.data['action'] == 'click'
	assert event.data['url'] == 'https://shop.test/cart'


def _forwarded(ch: FakeChannel) -> list[str]:
	return [e.text for e in ch.events if e.data.get('source') == 'browser-use']


def test_browser_use_narration_reaches_the_channel() -> None:
	ch = FakeChannel()

	async def go() -> None:
		async with stream.forward(ch):
			logging.getLogger('browser_use.Agent').warning('Eval: Success - clicked the button')
			await asyncio.sleep(0)  # let the pump drain

	asyncio.run(go())
	assert 'Eval: Success - clicked the button' in _forwarded(ch)


def test_a_line_carrying_a_credential_is_withheld() -> None:
	"""The feed puts this narration permanently on screen, so the leak has to be impossible.

	The <secret> placeholder contract means a real value should not appear here in the first
	place; this is the backstop for when something else prints one.
	"""
	ch = FakeChannel()
	secrets: dict[str, Any] = {'password': 'hunter2', 'per_domain': {'shop.test': 'swordfish'}}

	async def go() -> None:
		async with stream.forward(ch, secrets):
			log = logging.getLogger('browser_use')
			log.warning('typed hunter2 into the field')
			log.warning('and swordfish too')
			log.warning('this line is fine')
			await asyncio.sleep(0)

	asyncio.run(go())
	out = '\n'.join(_forwarded(ch))
	assert 'hunter2' not in out and 'swordfish' not in out
	assert 'withheld' in out
	assert 'this line is fine' in out


def test_secret_values_flattens_the_per_domain_shape() -> None:
	assert sorted(stream.secret_values({'a': 'x', 'b': {'shop.test': 'y'}, 'c': ''})) == ['x', 'y']
	assert stream.secret_values(None) == []


def test_forwarding_is_a_no_op_on_a_terminal() -> None:
	"""browser-use already writes there; forwarding would print every line twice."""
	ch = TerminalChannel()

	async def go() -> None:
		async with stream.forward(ch):
			assert not isinstance(__import__('sys').stdout, stream._LineTee)  # pyright: ignore[reportPrivateUsage]

	asyncio.run(go())
