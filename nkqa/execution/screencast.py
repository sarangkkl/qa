"""Stream the live browser to the channel while a run is happening.

Spike S1 established this is reachable through the CDP session browser-use already holds,
and measured ~20 fps at ~336 KB/s for a page repainting every 50ms - a deliberate worst
case. See docs/DESKTOP-PROGRESS.md.

Two things this must get right or the stream dies quietly:
- **Ack every frame.** Chrome stops sending after a couple of unacked frames. That is the
  single most common way a screencast "works" for a second and then freezes.
- **Never block the run.** Frames are dropped, not queued, when the socket is slower than
  the browser. A laggy UI must never slow down the browser it is watching.

No-op for a TerminalChannel: base64 JPEG in a terminal is nonsense.
"""

# browser-use boundary: its internals are partially untyped, so the Unknown family is off here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from typing import Any

from nkqa.ui import Channel, Event, TerminalChannel

QUALITY = 60
MAX_WIDTH = 1280
MAX_HEIGHT = 800
MAX_PENDING = 2  # frames in flight to the UI before we start dropping


class Screencast:
	def __init__(self, channel: Channel):
		self.channel = channel
		self.sent = 0
		self.dropped = 0
		self._inflight = 0
		self._tasks: set[asyncio.Task[None]] = set()

	def _spawn(self, coro: Any) -> None:
		task = asyncio.create_task(coro)
		self._tasks.add(task)
		task.add_done_callback(self._tasks.discard)

	async def _deliver(self, data: str, metadata: dict[str, Any]) -> None:
		try:
			await self.channel.emit(
				Event(
					'frame',
					'',
					{
						'image': data,
						'format': 'jpeg',
						'width': metadata.get('deviceWidth'),
						'height': metadata.get('deviceHeight'),
					},
				)
			)
			self.sent += 1
		finally:
			self._inflight -= 1

	def on_frame(self, client: Any, session_id: str | None, event: dict[str, Any]) -> None:
		# Ack first and unconditionally: an unacked frame stops the stream, and a dropped
		# frame the UI never sees still has to be acked or there will be no next one.
		self._spawn(self._ack(client, session_id, event.get('sessionId')))
		if self._inflight >= MAX_PENDING:
			self.dropped += 1
			return
		self._inflight += 1
		self._spawn(self._deliver(str(event.get('data', '')), event.get('metadata') or {}))

	async def _ack(self, client: Any, session_id: str | None, frame_session: Any) -> None:
		with contextlib.suppress(Exception):
			await client.send.Page.screencastFrameAck(params={'sessionId': frame_session}, session_id=session_id)

	async def drain(self) -> None:
		if self._tasks:
			await asyncio.gather(*list(self._tasks), return_exceptions=True)


@contextlib.asynccontextmanager
async def stream(agent: Any, channel: Channel) -> AsyncGenerator[Screencast | None]:
	"""Stream the agent's browser for the duration of the block. Never raises into the run."""
	if isinstance(channel, TerminalChannel):
		yield None
		return

	cast_state = Screencast(channel)
	client: Any = None
	session_id: Any = None
	try:
		session = agent.browser_session
		cdp = await session.get_or_create_cdp_session()
		client, session_id = cdp.cdp_client, cdp.session_id

		def handle(event: dict[str, Any], frame_session: str | None = None) -> None:
			cast_state.on_frame(client, frame_session or session_id, event)

		client.register.Page.screencastFrame(handle)
		await client.send.Page.startScreencast(
			params={
				'format': 'jpeg',
				'quality': QUALITY,
				'maxWidth': MAX_WIDTH,
				'maxHeight': MAX_HEIGHT,
				'everyNthFrame': 1,
			},
			session_id=session_id,
		)
		await channel.emit(Event('progress', '🎥 live view on', {'screencast': True}))
	except Exception as e:
		# A browser that will not screencast is not a reason to fail a QA run: the step
		# filmstrip still works, so say so once and carry on.
		await channel.emit(Event('progress', f'live view unavailable ({type(e).__name__})', {'screencast': False}))
		yield None
		return

	try:
		yield cast_state
	finally:
		with contextlib.suppress(Exception):
			await client.send.Page.stopScreencast(session_id=session_id)
		await cast_state.drain()
		await channel.emit(
			Event(
				'progress',
				'🎥 live view off',
				{'screencast': False, 'frames': cast_state.sent, 'dropped': cast_state.dropped},
			)
		)
