"""The live view's two failure modes: an unacked frame kills the stream, a slow UI must not.

Verified against a real browser too (see docs/DESKTOP-PROGRESS.md): 20 fps to a fast
consumer with nothing dropped, 4.5 fps and 75 dropped to one taking 0.4s per frame - the
run itself never slowed down.
"""

import asyncio
from types import SimpleNamespace
from typing import Any

from conftest import FakeChannel

from nkqa.execution import screencast
from nkqa.ui import Channel, Event, TerminalChannel


class FakeCDP:
	"""Just enough CDP to drive the callback the way Chrome would."""

	def __init__(self) -> None:
		self.acked: list[Any] = []
		self.started: dict[str, Any] | None = None
		self.stopped = False
		self.handler: Any = None
		page = SimpleNamespace(
			startScreencast=self._start,
			stopScreencast=self._stop,
			screencastFrameAck=self._ack,
		)
		self.send = SimpleNamespace(Page=page)
		self.register = SimpleNamespace(Page=SimpleNamespace(screencastFrame=self._on))

	def _on(self, handler: Any) -> None:
		self.handler = handler

	async def _start(self, params: dict[str, Any], session_id: str | None = None) -> None:
		self.started = params

	async def _stop(self, session_id: str | None = None) -> None:
		self.stopped = True

	async def _ack(self, params: dict[str, Any], session_id: str | None = None) -> None:
		self.acked.append(params['sessionId'])

	def deliver(self, n: int) -> None:
		"""A synchronous burst - the worst case, where nothing gets a chance to drain."""
		for i in range(n):
			self.handler({'data': f'FRAME{i}', 'sessionId': i, 'metadata': {'deviceWidth': 800, 'deviceHeight': 600}})

	async def deliver_paced(self, n: int) -> None:
		"""How frames really arrive: one per turn of the CDP socket's event loop."""
		for i in range(n):
			self.handler({'data': f'FRAME{i}', 'sessionId': i, 'metadata': {'deviceWidth': 800, 'deviceHeight': 600}})
			await asyncio.sleep(0.01)


def fake_agent(cdp: FakeCDP) -> Any:
	session = SimpleNamespace(
		get_or_create_cdp_session=lambda: _resolved(SimpleNamespace(cdp_client=cdp, session_id='s1'))
	)
	return SimpleNamespace(browser_session=session)


def _resolved(value: Any) -> Any:
	async def coro() -> Any:
		return value

	return coro()


class SlowChannel(Channel):
	"""A UI that cannot keep up. Frames must be shed, not queued behind the browser."""

	def __init__(self, delay: float):
		self.delay = delay
		self.frames: list[dict[str, Any]] = []

	async def emit(self, event: Event) -> None:
		if event.kind != 'frame':
			return
		await asyncio.sleep(self.delay)
		self.frames.append(event.data)

	async def ask(self, request: Any) -> str:
		return ''


def test_every_frame_is_acked_even_when_dropped() -> None:
	"""Chrome stops sending after a couple of unacked frames - dropped still means acked."""

	async def scenario() -> None:
		cdp = FakeCDP()
		channel = SlowChannel(delay=0.05)
		async with screencast.stream(fake_agent(cdp), channel) as cast:
			cdp.deliver(20)
			await asyncio.sleep(0.4)

		assert cast is not None
		assert len(cdp.acked) == 20  # all twenty, whatever the UI managed to render
		assert cast.dropped > 0  # and some were shed
		assert cast.sent + cast.dropped == 20

	asyncio.run(scenario())


def test_a_slow_ui_never_blocks_the_browser() -> None:
	async def scenario() -> None:
		cdp = FakeCDP()
		channel = SlowChannel(delay=0.2)
		async with screencast.stream(fake_agent(cdp), channel) as cast:
			started = asyncio.get_running_loop().time()
			cdp.deliver(50)  # the callback is what the CDP client calls; it must return at once
			elapsed = asyncio.get_running_loop().time() - started
			assert elapsed < 0.05, f'delivering frames blocked for {elapsed:.2f}s'
			await asyncio.sleep(0.3)

		assert cast is not None and cast.dropped >= 40

	asyncio.run(scenario())


def test_start_and_stop_are_paired_and_report_counts() -> None:
	async def scenario() -> None:
		cdp = FakeCDP()
		channel = FakeChannel()
		async with screencast.stream(fake_agent(cdp), channel):
			await cdp.deliver_paced(3)

		assert cdp.started is not None and cdp.started['format'] == 'jpeg'
		assert cdp.stopped is True
		off = channel.events[-1]
		assert off.kind == 'progress' and off.data['screencast'] is False
		assert off.data['frames'] == 3 and off.data['dropped'] == 0  # a keeping-up UI loses nothing

	asyncio.run(scenario())


def test_a_burst_sheds_rather_than_queues() -> None:
	"""Frames arriving faster than anything can drain are dropped, never buffered."""

	async def scenario() -> None:
		cdp = FakeCDP()
		channel = FakeChannel()
		async with screencast.stream(fake_agent(cdp), channel) as cast:
			cdp.deliver(10)
			await asyncio.sleep(0.05)

		assert cast is not None
		assert cast.sent == screencast.MAX_PENDING and cast.dropped == 10 - screencast.MAX_PENDING
		assert len(cdp.acked) == 10  # every one still acked, or the stream would stop

	asyncio.run(scenario())


def test_a_browser_that_will_not_screencast_does_not_fail_the_run() -> None:
	async def scenario() -> None:
		broken = SimpleNamespace(browser_session=SimpleNamespace(get_or_create_cdp_session=_boom))
		channel = FakeChannel()
		async with screencast.stream(broken, channel) as cast:
			assert cast is None  # the run carries on; the step filmstrip still works
		assert channel.events[-1].data['screencast'] is False
		assert 'unavailable' in channel.events[-1].text

	asyncio.run(scenario())


def _boom() -> Any:
	async def coro() -> Any:
		raise RuntimeError('no cdp here')

	return coro()


def test_terminal_channel_gets_no_screencast_at_all() -> None:
	async def scenario() -> None:
		cdp = FakeCDP()
		async with screencast.stream(fake_agent(cdp), TerminalChannel()) as cast:
			assert cast is None
		assert cdp.started is None  # never even asked the browser

	asyncio.run(scenario())


def test_terminal_channel_drops_frame_events() -> None:
	async def scenario() -> None:
		await TerminalChannel().emit(Event('frame', '', {'image': 'BASE64'}))  # must not print

	asyncio.run(scenario())
