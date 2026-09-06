"""Shared test doubles. FakeChannel lets every module be tested with no terminal."""

from nkqa.ui import Ask, Channel, Event


class FakeChannel(Channel):
	"""Records what was emitted; answers asks from a scripted queue."""

	def __init__(self, answers: list[str] | None = None):
		self.events: list[Event] = []
		self.asks: list[Ask] = []
		self.answers: list[str] = list(answers or [])

	async def emit(self, event: Event) -> None:
		self.events.append(event)

	async def ask(self, request: Ask) -> str:
		self.asks.append(request)
		if not self.answers:
			raise AssertionError(f'unexpected ask: {request.kind} {request.prompt!r}')
		return self.answers.pop(0)

	@property
	def out(self) -> str:
		"""Everything emitted, as the terminal would have printed it."""
		return '\n'.join(e.text for e in self.events)
