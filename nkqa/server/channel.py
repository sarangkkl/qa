"""The Channel implementation that speaks the wire protocol.

Same contract as TerminalChannel: emit puts a frame on the socket, ask sends a frame and
waits for the matching answer. A cancelled or disconnected job resolves every pending ask
with '' - the value every call site already treats as "no value / deny", so a vanished
window can never leave a run hanging or accidentally grant something.

The channel carries the prompt, never the answer: a secret goes from the socket straight
into HumanInTheLoop.secrets and is never echoed back as an Event.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any

from nkqa.ui import Ask, Channel, Event

Send = Callable[[dict[str, Any]], Awaitable[None]]


class SocketChannel(Channel):
	def __init__(self, send: Send, job_id: str = ''):
		self.send = send
		self.job_id = job_id
		self.pending: dict[str, asyncio.Future[str]] = {}
		self._next = 0

	async def emit(self, event: Event) -> None:
		if event.kind == 'frame':
			# Its own frame type, and no `text`: at 20 fps this is the one message where
			# an extra field per frame actually costs something.
			await self.send({'type': 'frame', 'job': self.job_id, **event.data})
			return
		await self.send(
			{'type': 'event', 'job': self.job_id, 'kind': event.kind, 'text': event.text, 'data': event.data}
		)

	async def ask(self, request: Ask) -> str:
		self._next += 1
		ask_id = f'a{self._next}'
		future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
		self.pending[ask_id] = future
		await self.send({'type': 'ask', 'id': ask_id, 'job': self.job_id, **asdict(request)})
		try:
			return await future
		except asyncio.CancelledError:
			return ''  # the job was cancelled while waiting: same as "no answer"
		finally:
			self.pending.pop(ask_id, None)

	def answer(self, ask_id: str, value: str) -> bool:
		future = self.pending.get(ask_id)
		if future is None or future.done():
			return False
		future.set_result(value)
		return True

	def abandon(self) -> None:
		"""Socket closed or job cancelled: unblock everything waiting, denying by default."""
		for future in list(self.pending.values()):
			if not future.done():
				future.set_result('')
		self.pending.clear()
