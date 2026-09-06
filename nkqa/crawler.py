"""Optional appmap enrichment: a browser agent explores the app read-only.

Not the onboarding path (that's `qa learn` with an annotated doc) - this verifies and
extends the map against the live app. 2FA is fine: ask_credential collects OTPs live.
"""

# browser-use boundary: its internals are partially untyped, so the Unknown family is off here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

import asyncio
import contextlib
from datetime import datetime
from urllib.parse import urlsplit

from browser_use import ActionResult, Agent, Tools
from browser_use.browser import BrowserProfile

from nkqa import appmap
from nkqa.appmap import AppmapUpdate, FileUpdate
from nkqa.config import Config
from nkqa.execution import screencast, stream
from nkqa.hitl import HumanInTheLoop
from nkqa.models import resolve_llm
from nkqa.prompts import QA_RULES
from nkqa.stop import StopSignal
from nkqa.ui import Channel, Event
from nkqa.workspace import Workspace

CRAWL_RULES = """
You are MAPPING the application, not testing it. Additional hard rules for this run:
- READ-ONLY: never submit forms that create/change/delete data, never buy, never send.
  Logging in (via ask_credential, including OTP codes) and navigation are allowed.
- Call record_page the MOMENT you finish looking at a screen, before you navigate away.
  Do not save your findings up to report at the end: anything not recorded is lost if the
  run stops, and a recorded page is one you never have to visit again.
- Skip screens that are already mapped - you are told which those are. Spend the budget on
  what is not known yet.
- For each page note: purpose, key elements, where it links to, anything odd.
- Record a flow with record_flow once you have seen a journey end to end.
"""


class PageRecorder:
	"""The crawl's memory: what is already known, and what this run has written.

	This exists because the crawl used to hand back one structured result at the very end.
	Miss that - stop it, crash it, or run out of steps before the model calls done - and the
	whole session was lost; a real 17-minute crawl over five screens wrote nothing at all.
	Each page now lands as its own file and its own commit, the moment it is understood.
	"""

	def __init__(self, ws: Workspace, ch: Channel, config: Config, budget: int, refresh: bool = False):
		self.ws, self.ch, self.budget = ws, ch, budget
		# --refresh re-maps everything: the app changed, so what we know is what is stale.
		self.known = {} if refresh else appmap.known_routes(ws)
		self.host = urlsplit(config.base_url).netloc.lower()
		self.recorded: dict[str, str] = {}
		self.flows: list[str] = []

	@property
	def spent(self) -> bool:
		return len(self.recorded) >= self.budget

	def _write(self, rel: str, content: str, message: str) -> None:
		appmap.apply(self.ws, AppmapUpdate(files=[FileUpdate(file=rel, content=content)]), message)

	async def page(self, route: str, title: str, markdown: str) -> str:
		"""Returns what to tell the agent. Never raises: a refusal is information, not a failure."""
		host = urlsplit(route).netloc.lower()
		if host and self.host and host != self.host:
			# The last real crawl spent ~8 of 50 steps on login.microsoftonline.com. An identity
			# provider is not a page of the app and must never take a slot in its map.
			return f'{host} is not part of this app - do not map it. Get back to {self.host} and continue.'
		if self.spent:
			return f'Page budget spent ({self.budget} pages recorded). Record any flows you saw, then call done.'

		normalized = appmap.normalize_route(route)
		covered = appmap.covering_route({**self.known, **self.recorded}, normalized)
		if covered:
			where = {**self.known, **self.recorded}[covered]
			return f'{normalized} is already mapped in appmap/{where} - do not record it again. Move to a new screen.'

		rel = f'pages/{appmap.route_slug(normalized)}.md'
		# The Route line is not decoration: known_routes() reads it back to build the skip list
		# on the next crawl. Its shape is the same one the hand-written page docs use.
		body = f'# {title.strip() or normalized}\n\n**Route:** `{normalized}`\n\n{markdown.strip()}\n'
		self._write(rel, body, f'appmap: crawl {normalized}')
		self.recorded[normalized] = rel
		await self.ch.emit(Event('artifact', f'🗺️  appmap/{rel}  ({len(self.recorded)}/{self.budget})', {'appmap': rel}))
		left = self.budget - len(self.recorded)
		return f'Recorded {normalized} to appmap/{rel}. {left} page{"" if left == 1 else "s"} left in the budget.'

	async def flow(self, name: str, markdown: str) -> str:
		rel = f'flows/{appmap.route_slug(name)}.md'
		if rel in self.flows or (not self.known.get(rel) and (self.ws.appmap_dir / rel).is_file()):
			return f'appmap/{rel} already exists - do not overwrite it.'
		self._write(rel, f'# {name.strip()}\n\n{markdown.strip()}\n', f'appmap: crawl flow {name}')
		self.flows.append(rel)
		await self.ch.emit(Event('artifact', f'🗺️  appmap/{rel}', {'appmap': rel}))
		return f'Recorded the flow to appmap/{rel}.'

	def register(self, tools: 'Tools[None]') -> None:
		@tools.registry.action(
			'Record what you learned about ONE screen. Call this the moment you finish looking at a '
			'screen and BEFORE you navigate away - it is written to the knowledge base immediately, '
			'so nothing is lost if the run ends early. route is the URL path (e.g. "/clients"); '
			'markdown is what you saw: purpose, key elements, links out, anything odd.'
		)
		async def record_page(route: str, title: str, markdown: str) -> ActionResult:  # pyright: ignore[reportUnusedFunction]
			said = await self.page(route, title, markdown)
			return ActionResult(extracted_content=said, long_term_memory=said)

		@tools.registry.action('Record a user journey that spans several screens, once you have seen it end to end.')
		async def record_flow(name: str, markdown: str) -> ActionResult:  # pyright: ignore[reportUnusedFunction]
			said = await self.flow(name, markdown)
			return ActionResult(extracted_content=said, long_term_memory=said)


def build_task(config: Config, current_appmap: dict[str, str], pages: int, known: dict[str, str]) -> str:
	existing = '\n\n'.join(f'--- appmap/{name} (current) ---\n{content}' for name, content in current_appmap.items())
	skip = (
		'Already mapped - do NOT spend budget revisiting these:\n'
		+ '\n'.join(f'- {route}' for route in sorted(known))
		+ '\n\n'
		if known
		else ''
	)
	return (
		f'Explore the web application at {config.base_url} and update its QA knowledge base '
		f'(the appmap). Page budget: {pages} pages you have not mapped before.\n\n'
		f'{skip}'
		f'Current appmap to verify, correct, and extend:\n\n{existing or "(empty)"}\n\n'
		'Call record_page for each NEW screen as you finish with it - that is how pages get '
		'written. At the end, return the structured result for what does not belong to a single '
		"page: 'overview.md' (merge, keep human notes) and any 'flows/<slug>.md' you did not "
		'already record. Describe only what you actually saw; put uncertainties in notes.'
	)


async def crawl(
	ws: Workspace,
	config: Config,
	hitl: HumanInTheLoop,
	ch: Channel,
	pages: int,
	model_override: str | None = None,
	stop: StopSignal | None = None,
	refresh: bool = False,
) -> int:
	if not config.base_url:
		await ch.log('qa crawl needs app.base_url in config.yaml.')
		return 2
	stop = stop or StopSignal()
	run_dir = ws.runs_dir / f'crawl--{datetime.now():%Y%m%d-%H%M%S}'
	run_dir.mkdir(parents=True, exist_ok=True)
	await ch.log(f'🕷️  Mapping {config.base_url} (budget: {pages} pages, read-only). 🎬 Recording video.\n')

	recorder = PageRecorder(ws, ch, config, pages, refresh)
	if recorder.known:
		await ch.log(f'📚 Already mapped, skipping {len(recorder.known)}: {", ".join(sorted(recorder.known))}')
		await ch.log('   Re-map them with:  qa crawl --refresh\n')
	tools = hitl.build_tools()
	recorder.register(tools)

	agent: Agent[None, AppmapUpdate] = Agent(
		task=build_task(config, appmap.read_all(ws), pages, recorder.known),
		llm=resolve_llm(config, 'executor', model_override),
		tools=tools,
		extend_system_message=QA_RULES + CRAWL_RULES,
		sensitive_data=hitl.secrets,
		fallback_llm=resolve_llm(config, 'fallback'),
		browser_profile=BrowserProfile(headless=config.headless, record_video_dir=run_dir / 'videos'),
		# nkqa owns SIGINT/SIGTERM in every surface; see nkqa/stop.py.
		enable_signal_handler=False,
		register_should_stop_callback=stop.should_stop,
		generate_gif=str(run_dir / 'last_run.gif'),
		save_conversation_path=run_dir / 'conversation',
		calculate_cost=True,
		file_system_path=str(run_dir),
		output_model_schema=AppmapUpdate,
	)
	stop.attach(agent)

	steps = 0

	async def checkpoint(active_agent: Agent[None, AppmapUpdate]) -> None:
		nonlocal steps
		steps += 1
		with contextlib.suppress(Exception):
			active_agent.save_history(run_dir / 'history.json')
		with contextlib.suppress(Exception):
			await ch.emit(stream.step_event(active_agent, steps, run_dir))

	update: AppmapUpdate | None = None
	cancelled = False
	try:
		# The line every other runner already had. Without it the longest job in the product -
		# a crawl runs for tens of minutes - narrated nothing and showed no live view, so there
		# was no way to tell a working crawl from a stuck one.
		async with stream.forward(ch, hitl.secrets), screencast.stream(agent, ch):
			history = await agent.run(max_steps=max(config.max_steps, pages * 4), on_step_end=checkpoint)
		update = history.structured_output
	except (KeyboardInterrupt, asyncio.CancelledError):
		cancelled = True
		await ch.log('\n🛑 Crawl interrupted - evidence kept.')
	except Exception as e:
		await ch.log(f'\n💥 Crawl crashed ({type(e).__name__}: {e}) - evidence kept.')
	finally:
		if agent.history.history:
			agent.save_history(run_dir / 'history.json')

	# Whatever happened above, the pages recorded along the way are already on disk and
	# committed. The end-of-run update is now a bonus - overview.md and any flow the agent
	# saved for last - not the one chance to keep the session.
	kept = len(recorder.recorded)
	if update is not None and not (cancelled or stop.stopped):
		written = appmap.apply(ws, update, f'appmap: crawl {run_dir.name}')
		for f in written:
			await ch.emit(Event('artifact', f'🗺️  appmap/{f}', {'appmap': f}))
		if update.notes:
			await ch.log(f'\n🗒️  Crawl notes: {update.notes}')
	elif not (cancelled or stop.stopped):
		await ch.log('\n🚧 The crawl ended without a closing summary, so overview.md was not merged.')

	if cancelled or stop.stopped:
		await ch.log(f'\n⏹  Stopped. The recording is in {run_dir}')
	await ch.log(f'\n🗺️  {kept} new page(s) mapped. Evidence: {run_dir}/  ·  Review:  git diff appmap/')
	# Mapping pages is the job. A stopped crawl that mapped six screens did the job, and
	# reporting that as a failure is what made stopping feel like losing the work.
	return 0 if kept or update is not None else 1
