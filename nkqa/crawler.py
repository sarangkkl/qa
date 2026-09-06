"""Optional appmap enrichment: a browser agent explores the app read-only.

Not the onboarding path (that's `qa learn` with an annotated doc) - this verifies and
extends the map against the live app. 2FA is fine: ask_credential collects OTPs live.
"""

# browser-use boundary: its internals are partially untyped, so the Unknown family is off here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

import asyncio
import contextlib
from datetime import datetime

from browser_use import Agent
from browser_use.browser import BrowserProfile

from nkqa import appmap
from nkqa.appmap import AppmapUpdate
from nkqa.config import Config
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
- Visit up to the page budget of distinct pages, main areas first.
- For each page note: purpose, key elements, where it links to, anything odd.
"""


def build_task(config: Config, current_appmap: dict[str, str], pages: int) -> str:
	existing = '\n\n'.join(f'--- appmap/{name} (current) ---\n{content}' for name, content in current_appmap.items())
	return (
		f'Explore the web application at {config.base_url} and update its QA knowledge base '
		f'(the appmap). Page budget: {pages} distinct pages.\n\n'
		f'Current appmap to verify, correct, and extend:\n\n{existing or "(empty)"}\n\n'
		'When the budget is spent (or nothing new remains), return the structured result: '
		"appmap files to write - 'overview.md' (merge, keep human notes), 'pages/<slug>.md' "
		"per visited page, 'flows/<slug>.md' for journeys you observed. Describe only what "
		'you actually saw; put uncertainties in notes.'
	)


async def crawl(
	ws: Workspace,
	config: Config,
	hitl: HumanInTheLoop,
	ch: Channel,
	pages: int,
	model_override: str | None = None,
	stop: StopSignal | None = None,
) -> int:
	if not config.base_url:
		await ch.log('qa crawl needs app.base_url in config.yaml.')
		return 2
	stop = stop or StopSignal()
	run_dir = ws.runs_dir / f'crawl--{datetime.now():%Y%m%d-%H%M%S}'
	run_dir.mkdir(parents=True, exist_ok=True)
	await ch.log(f'🕷️  Mapping {config.base_url} (budget: {pages} pages, read-only). 🎬 Recording video.\n')

	agent: Agent[None, AppmapUpdate] = Agent(
		task=build_task(config, appmap.read_all(ws), pages),
		llm=resolve_llm(config, 'executor', model_override),
		tools=hitl.build_tools(),
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

	async def checkpoint(active_agent: Agent[None, AppmapUpdate]) -> None:
		with contextlib.suppress(Exception):
			active_agent.save_history(run_dir / 'history.json')

	update: AppmapUpdate | None = None
	cancelled = False
	try:
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

	if cancelled or stop.stopped:
		# Distinct from "the model gave us nothing": you stopped it, and that is not a fault.
		await ch.log(f'\n⏹  Stopped - the partial recording is in {run_dir}')
		return 1
	if update is None:
		await ch.log('\n🚧 The crawl returned no structured appmap update; nothing written.')
		return 1
	written = appmap.apply(ws, update, f'appmap: crawl {run_dir.name}')
	for f in written:
		await ch.emit(Event('artifact', f'🗺️  appmap/{f}', {'appmap': f}))
	if update.notes:
		await ch.log(f'\n🗒️  Crawl notes: {update.notes}')
	await ch.log(f'\n📄 Evidence: {run_dir}/  ·  Review:  git diff appmap/')
	return 0
