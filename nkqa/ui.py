"""How the product talks to a human.

Every surface implements Channel: the CLI passes a TerminalChannel and behaves exactly
as it always has; the desktop sidecar passes a channel that puts the same events on a
WebSocket. Nothing below this module prints or reads stdin directly, which is what lets
one core serve both.

Adding an EventKind or an AskKind is a contract change - every surface must render it.
"""

import asyncio
import getpass
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

EventKind = Literal['log', 'step', 'verdict', 'artifact', 'progress', 'frame', 'done']
AskKind = Literal['text', 'secret', 'confirm', 'choice']

RULE = '=' * 60


@dataclass
class Event:
	kind: EventKind = 'log'
	text: str = ''
	data: dict[str, Any] = field(default_factory=dict[str, Any])


@dataclass
class Ask:
	kind: AskKind = 'text'
	prompt: str = ''
	key: str = ''  # credential name or permission key
	options: list[str] = field(default_factory=list[str])
	body: str = ''  # long content to show first: a scenario, a bug preview
	interrupt: bool = False  # raised from inside a running job, not part of a dialogue


class Channel(ABC):
	"""Two methods to implement; the rest are shorthands so call sites stay short."""

	@abstractmethod
	async def emit(self, event: Event) -> None: ...

	@abstractmethod
	async def ask(self, request: Ask) -> str: ...

	async def log(self, text: str = '') -> None:
		await self.emit(Event('log', text))

	async def step(self, text: str, **data: Any) -> None:
		await self.emit(Event('step', text, data))

	async def verdict(self, text: str, **data: Any) -> None:
		await self.emit(Event('verdict', text, data))

	async def artifact(self, text: str, **data: Any) -> None:
		await self.emit(Event('artifact', text, data))

	async def done(self, text: str = '', **data: Any) -> None:
		await self.emit(Event('done', text, data))

	async def ask_text(self, prompt: str, interrupt: bool = False) -> str:
		return await self.ask(Ask('text', prompt, interrupt=interrupt))

	async def ask_secret(self, key: str, prompt: str, interrupt: bool = False) -> str:
		return await self.ask(Ask('secret', prompt, key=key, interrupt=interrupt))

	async def confirm(self, prompt: str, body: str = '') -> bool:
		return (await self.ask(Ask('confirm', prompt, body=body))).strip().lower().startswith('y')

	async def choose(self, prompt: str, options: list[str], key: str = '', interrupt: bool = False) -> str:
		answer = await self.ask(Ask('choice', prompt, key=key, options=options, interrupt=interrupt))
		return answer.strip().lower()


def is_hidden(key: str) -> bool:
	return any(marker in key for marker in ('pass', 'token', 'secret', 'otp'))


class TerminalChannel(Channel):
	"""The CLI surface: events print, asks read stdin. Output is the shipped contract."""

	async def emit(self, event: Event) -> None:
		if event.kind == 'frame':
			return  # base64 JPEG in a terminal is nonsense
		print(event.text)

	async def ask(self, request: Ask) -> str:
		if request.body:
			print(request.body)
		if request.interrupt:
			print('\n' + RULE)
		if request.kind == 'secret' and is_hidden(request.key):
			answer = await asyncio.to_thread(getpass.getpass, request.prompt)
		else:
			answer = await asyncio.to_thread(input, request.prompt)
		if request.interrupt:
			print(RULE)
		return answer.strip()
