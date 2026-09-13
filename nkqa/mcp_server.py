"""nkqa as an MCP server: the user's own coding agent is the brain and the hands.

`qa mcp` speaks MCP over stdio to Claude Code, Codex or Cursor. The agent reads the appmap,
writes scenarios, drives the browser one action at a time and reports verdicts; nkqa provides
the workspace, a recorded browser, the evidence, and the three gates - approval, permission,
credentials - which reach the human through native dialogs the agent cannot answer.

Nothing on this path calls a model. The tool docstrings are the agent's documentation: they
say when to call a tool and what to do next, because that is all the agent ever reads.

stdout is the protocol. Everything else this process says goes to stderr.
"""

# The tools and prompts in build_server are registered by decorator; pyright cannot see that use.
# pyright: reportUnusedFunction=false

import asyncio
import contextlib
import io
import os
import signal
import sys
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from pathlib import Path
from typing import IO, Any

import anyio
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP, Image
from mcp.server.stdio import stdio_server

from nkqa import actions, appmap
from nkqa import config as config_mod
from nkqa import scenarios as scenarios_mod
from nkqa import workspace as workspace_mod
from nkqa.appmap import AppmapUpdate, FileUpdate
from nkqa.config import Config
from nkqa.dialogs import DialogChannel
from nkqa.execution.driver import Driver, read_step_log
from nkqa.execution.evidence import recorded_runs, recording_file, step_count
from nkqa.execution.report import ScenarioResult, StepVerdict, last_verdict, write_results
from nkqa.execution.scenario_runner import build_task
from nkqa.execution.stream import redact
from nkqa.hitl import HumanInTheLoop
from nkqa.jira import issue_key, read_issue
from nkqa.planner import PLANNER_SYSTEM, DraftScenario, gather_context, write_drafts
from nkqa.prompts import QA_RULES_MCP
from nkqa.reflector import run_facts
from nkqa.scenarios import Scenario
from nkqa.ui import Ask, Channel, Event
from nkqa.vault import Vault
from nkqa.workspace import Workspace

INSTRUCTIONS = """\
nkqa is a QA workspace for a web app. You are the QA engineer; nkqa gives you the notebook
(appmap), the scenario files, a real recorded browser and the evidence. It never calls a model.
Workflow: workspace_status -> read_appmap -> write_scenario per draft -> the human reviews ->
approve_scenario only when the human says so -> start_run -> loop { browser_state -> one action }
-> finish_run -> update_appmap if the run taught something -> tell the human.
Hard rules:
1. Ask before anything irreversible: request_permission before deleting, paying, sending or
   changing settings. Denied means skip that step and report it, never retry.
2. Never invent credentials: ask_credential(name), then type the literal <secret>name</secret>.
   You never see values; do not try to.
3. A broken feature is a finding, not an obstacle: record expected vs actual, keep testing.
Approval, permissions and credentials are the human's keystrokes in a native dialog on their
screen. Only scenarios reported as `ok` can run. When you need information, ask the human in
chat - there is no tool for that.
"""

REFUSALS = {
	'draft': 'is not approved yet. Ask the human to review it; call approve_scenario("{id}") when they say so.',
	'stale': 'was edited after approval, so the approval is stale. Ask the human to re-review it; '
	'call approve_scenario("{id}") when they say so.',
	'deprecated': 'is deprecated and will not run.',
}
DRIVER_EVIDENCE = [
	'- [Step log (steps.json)](steps.json)',
	'- [Screenshots](steps/)',
	'- [Videos](videos/)',
]
ICONS = {'pass': '✅', 'fail': '❌', 'blocked': '🚧'}
EXIT_GRACE = 60  # seconds a stuck tool call may hold the process after the client is gone


class CapturingChannel(Channel):
	"""Reuse the CLI verbs unchanged: their log lines become the tool result, their asks
	become dialogs. Approval therefore stays the one code path it always was."""

	def __init__(self, asks: Channel):
		self.asks = asks
		self.lines: list[str] = []

	async def emit(self, event: Event) -> None:
		if event.kind != 'frame' and event.text:
			self.lines.append(event.text)

	async def ask(self, request: Ask) -> str:
		return await self.asks.ask(request)

	@property
	def text(self) -> str:
		return '\n'.join(self.lines).strip()


class Session:
	"""Process-wide state: one workspace, one HITL, at most one open browser."""

	def __init__(self, dialogs: Channel | None = None):
		self.dialogs: Channel = dialogs or DialogChannel()
		self.ws: Workspace | None = None
		self.config = Config()
		self.hitl: HumanInTheLoop | None = None
		self.driver: Driver | None = None
		self.scenario: Scenario | None = None
		self.run_dir: Path | None = None

	def use(self, ws: Workspace) -> None:
		self.ws = ws
		load_dotenv(ws.root / '.env')  # `env:NAME` indirections in config.yaml resolve from here
		self.config = config_mod.load(ws.config_file)
		# The Vault is not optional: without it the release chain short-circuits and every
		# credential is typed by hand, origin binding included. Same rule as the sidecar.
		self.hitl = HumanInTheLoop(ws.permissions_file, self.dialogs, Vault(ws))

	def require_ws(self) -> Workspace:
		if self.ws is None:
			raise ValueError(
				'No workspace is active. Call list_workspaces then use_workspace(path), '
				'or init_workspace(path, app_name, base_url) for a new project.'
			)
		return self.ws

	def require_hitl(self) -> HumanInTheLoop:
		self.require_ws()
		assert self.hitl is not None
		return self.hitl

	def require_run(self) -> Driver:
		if self.driver is None:
			raise ValueError('No active run. Call start_run(scenario_id) or start_explore(name, url) first.')
		return self.driver

	def require_fresh(self) -> Driver:
		driver = self.require_run()
		if not driver.fresh:
			raise ValueError('Element indices are stale: call browser_state first, then use an index from it.')
		return driver

	async def end_run(self, aborted: bool = False) -> Path:
		driver = self.require_run()
		await driver.close(aborted=aborted)
		self.driver = None
		run_dir, self.run_dir = driver.run_dir, None
		return run_dir

	def abandon(self) -> None:
		if isinstance(self.dialogs, DialogChannel):
			self.dialogs.abandon()

	async def close(self) -> None:
		self.abandon()
		if self.driver is not None:
			with contextlib.suppress(Exception):
				await self.end_run(aborted=True)


def _clean(session: Session, text: str) -> str:
	return redact(text, session.hitl.secrets if session.hitl else None)


def build_server(session: Session) -> FastMCP:
	server = FastMCP('nkqa', instructions=INSTRUCTIONS)
	tool: Callable[..., Callable[[Any], Any]] = lambda: server.tool(structured_output=False)  # noqa: E731

	# --- workspace -----------------------------------------------------------

	@tool()
	async def workspace_status() -> str:
		"""Which nkqa workspace is active: app name, base URL, scenarios by state, runs recorded.
		Call this first. If it reports no workspace, call list_workspaces then use_workspace,
		or init_workspace for a new project."""
		ws = session.ws
		if ws is None:
			here = workspace_mod.find()
			hint = f' The current directory is inside one: use_workspace("{here.root}").' if here else ''
			return 'No workspace is active.' + hint + ' Otherwise: list_workspaces, or init_workspace.'
		cfg = session.config
		states = [s.runnable() for s in scenarios_mod.load_all(ws.scenarios_dir)]
		by_state = ', '.join(
			f'{k}: {states.count(k)}' for k in ('ok', 'draft', 'stale', 'deprecated') if states.count(k)
		)
		active = f'Active run: {session.run_dir.name}' if session.run_dir else 'No run active.'
		jira = 'configured' if session.config.mcp_server('jira') else 'not configured'
		return (
			f'Workspace: {ws.root}\nApp: {cfg.app_name or "(unnamed)"} — {cfg.base_url or "(no base URL)"}\n'
			f'Scenarios: {len(states)} ({by_state or "none"})\nRuns recorded: {len(recorded_runs(ws.runs_dir))}\n'
			f'{active}\nJira: {jira}'
		)

	@tool()
	async def list_workspaces() -> str:
		"""Workspaces on this machine: the one the current directory is in, plus those recently
		opened in the nkqa desktop app. Pick one with use_workspace(path)."""
		found: dict[Path, str] = {}
		here = workspace_mod.find()
		if here is not None:
			found[here.root] = 'current directory'
		for ws in workspace_mod.recent_workspaces():
			found.setdefault(ws.root, 'recent in the desktop app')
		if not found:
			return (
				'No workspaces found. Ask the human for the folder and call use_workspace(path), '
				'or init_workspace(path, app_name, base_url) to create one.'
			)
		lines = [
			f'- {root}  ({config_mod.load(root / "config.yaml").app_name or "unnamed"}; {how})'
			for root, how in found.items()
		]
		return '\n'.join(lines) + '\nCall use_workspace(path) to pick one.'

	@tool()
	async def use_workspace(path: str) -> str:
		"""Switch the active workspace to a folder that has config.yaml and appmap/. Refused while
		a run is active. Re-reads the config and credentials for that workspace."""
		if session.driver is not None:
			raise ValueError('A run is active: finish_run or abort_run first.')
		ws = workspace_mod.at(Path(path).expanduser())
		if ws is None:
			raise ValueError(
				f'{path} is not a QA workspace (no config.yaml + appmap/). Use init_workspace to create one.'
			)
		session.use(ws)
		return f'Using {ws.root} ({session.config.app_name or "unnamed"}). Next: read_appmap, list_scenarios.'

	@tool()
	async def init_workspace(path: str, app_name: str, base_url: str) -> str:
		"""Create a new nkqa workspace layout in `path` (idempotent; never overwrites), then make
		it active. Use when the human wants to start testing an app that has no workspace yet."""
		root = Path(path).expanduser().resolve()
		if root in (Path.home(), Path('/')):
			raise ValueError('Refusing to create a workspace in the home or root directory. Pick a project folder.')
		ch = CapturingChannel(session.dialogs)
		await actions.init(ch, root, app_name, base_url)
		created = workspace_mod.at(root)
		assert created is not None
		session.use(created)
		return ch.text + '\nIt is now the active workspace. Next: read_appmap, then draft scenarios.'

	# --- knowledge -------------------------------------------------------------

	@tool()
	async def read_appmap() -> str:
		"""The whole QA notebook (appmap/): what is known about the app - pages, flows, roles, the
		sign-in procedure, quirks. Read it before planning or running; it is the only ground truth
		you have about the app."""
		ws = session.require_ws()
		files = appmap.read_all(ws)
		if not files:
			return '(the appmap is empty - learn the app by exploring it, then update_appmap)'
		return '\n\n'.join(f'--- appmap/{name} ---\n{content}' for name, content in files.items())

	@tool()
	async def update_appmap(files: list[FileUpdate], message: str = '') -> str:
		"""Write appmap files (paths relative to appmap/, .md or .json, full-file replacement,
		never deletes) and git-commit them. Call after finish_run when a run taught something the
		map lacks or gets wrong; merge around existing text and keep it factual."""
		ws = session.require_ws()
		written = appmap.apply(ws, AppmapUpdate(files=files), message or 'appmap: updated by the QA agent')
		return ('Wrote: ' + ', '.join(written)) if written else 'Nothing written.'

	# --- scenarios ---------------------------------------------------------------

	@tool()
	async def list_scenarios() -> str:
		"""Every scenario id with its state (ok = approved and unchanged, draft, stale = edited
		after approval, deprecated), title and last verdict. Only `ok` scenarios can be run."""
		ws = session.require_ws()
		found = scenarios_mod.load_all(ws.scenarios_dir)
		if not found:
			return 'No scenarios yet. Draft some with write_scenario after reading the appmap.'
		return '\n'.join(
			f'- {s.id} [{s.runnable()}] {s.title}' + (f' — last: {v}' if (v := last_verdict(ws.runs_dir, s.id)) else '')
			for s in found
		)

	@tool()
	async def read_scenario(id: str) -> str:
		"""The scenario file: title, preconditions, numbered steps with expectations, out-of-scope
		list and approval metadata."""
		ws = session.require_ws()
		s = scenarios_mod.find(ws.scenarios_dir, id)
		if s is None:
			raise ValueError(f'No scenario "{id}". See list_scenarios.')
		return f'State: {s.runnable()}\n\n{s.path.read_text(encoding="utf-8")}'

	@tool()
	async def write_scenario(draft: DraftScenario, force: bool = False, ticket: str = '') -> str:
		"""Write one draft scenario file under scenarios/<area>/<slug>.md. A draft is never
		runnable until a human approves it: after writing, tell the human which ids to review and
		call approve_scenario only for the ones they say to. Existing files are kept unless
		force=true. Keep each scenario one journey, 3-8 steps, each with an expectation."""
		ws = session.require_ws()
		written, skipped = write_drafts(ws, [draft], force, ticket)
		if written:
			s = written[0]
			return (
				f'Wrote draft {s.id} ({s.path}).\n'
				f'Ask the human to review it; approve_scenario("{s.id}") when they say so.'
			)
		return f'{skipped[0]} already exists. Pass force=true to overwrite it, or pick another slug.'

	@tool()
	async def approve_scenario(id: str) -> str:
		"""Ask the human to approve a scenario for execution. This opens a native dialog on the
		human's screen showing the full scenario text with Confirm/Cancel - you cannot approve
		anything yourself, and you must not call this unless the human asked you to. The approval
		is bound to the file's content: any later edit makes it stale again."""
		ws = session.require_ws()
		ch = CapturingChannel(session.dialogs)
		await actions.approve(ws, ch, id)
		return ch.text

	# --- runs ----------------------------------------------------------------------

	@tool()
	async def start_run(scenario_id: str) -> str:
		"""Open a recorded browser and start a run of an APPROVED scenario (refused for draft,
		stale or deprecated; one run at a time). Returns the steps with expectations, preconditions,
		out-of-scope items and what nkqa knows about the app, sign-in procedure included. Then
		drive: browser_state -> one action (navigate/click/type_text/...) -> browser_state, verify
		every EXPECT, and end with finish_run."""
		ws = session.require_ws()
		hitl = session.require_hitl()
		if session.driver is not None:
			raise ValueError('A run is already active: finish_run or abort_run it first.')
		s = scenarios_mod.find(ws.scenarios_dir, scenario_id)
		if s is None:
			raise ValueError(f'No scenario "{scenario_id}". See list_scenarios.')
		state = s.runnable()
		if state != 'ok':
			raise ValueError(f'Scenario "{s.id}" {REFUSALS[state].format(id=s.id)}')
		hitl.scenario_id = s.id
		run_dir = ws.scenario_run_dir(s.id)
		driver = Driver(run_dir, hitl.secrets, session.config.headless, s.id)
		await driver.start()
		session.driver, session.scenario, session.run_dir = driver, s, run_dir
		task = build_task(s, session.config.base_url, appmap.context_for_run(ws))
		return (
			f'Run {run_dir.name} started; the browser is open and recording.\n\n{task}\n\n'
			'The structured result is finish_run(steps=[{step, verdict, note}], summary). '
			'Start with browser_state, then one action per call.'
		)

	@tool()
	async def start_explore(name: str, url: str = '') -> str:
		"""Freeform testing with no scenario: opens a recorded browser (at `url` if given) with
		evidence under runs/<name>/. Read-only unless request_permission grants otherwise. End
		with finish_explore(summary)."""
		ws = session.require_ws()
		hitl = session.require_hitl()
		if session.driver is not None:
			raise ValueError('A run is already active: finish_run or abort_run it first.')
		run_dir = ws.run_dir(name)
		if run_dir.exists():
			raise ValueError(f'runs/{run_dir.name} already exists. Pick another name.')
		hitl.scenario_id = ''
		driver = Driver(run_dir, hitl.secrets, session.config.headless)
		await driver.start()
		session.driver, session.scenario, session.run_dir = driver, None, run_dir
		opened = await driver.act('navigate', {'url': url}) if url else 'No URL given: navigate first.'
		return _clean(
			session,
			f'Exploring; evidence under runs/{run_dir.name}/.\n{opened}\n\nWhat nkqa knows:\n'
			f'{appmap.context_for_run(ws) or "(nothing yet)"}\n\nEnd with finish_explore(summary).',
		)

	@tool()
	async def browser_state(screenshot: bool = False) -> str | list[str | Image]:
		"""The current page: URL, title, tabs, scroll position and every interactive element as
		`[index]<tag attrs>text</tag>`. Indices are only valid until your next action - call this
		again after every navigate/click/type/scroll before using an index. screenshot=true also
		returns a PNG of the viewport; use it when the text is ambiguous or to check visuals."""
		text, shot = await session.require_run().state(screenshot)
		text = _clean(session, text)
		return text if shot is None else [text, Image(data=shot, format='png')]

	@tool()
	async def navigate(url: str, new_tab: bool = False) -> str:
		"""Go to a URL in the current tab (or a new one)."""
		return _clean(session, await session.require_run().act('navigate', {'url': url, 'new_tab': new_tab}))

	@tool()
	async def click(index: int) -> str:
		"""Click the element with this index from the latest browser_state. Refused if
		browser_state has not been called since your last action."""
		return _clean(session, await session.require_fresh().act('click', {'index': index}))

	@tool()
	async def type_text(index: int, text: str, clear: bool = True) -> str:
		"""Type into the input with this index. For credentials NEVER type a real value: call
		ask_credential(name) first, then pass the literal placeholder `<secret>name</secret>` as
		`text` - nkqa substitutes the real value inside the browser and it never reaches you."""
		return _clean(
			session, await session.require_fresh().act('input', {'index': index, 'text': text, 'clear': clear})
		)

	@tool()
	async def scroll(direction: str = 'down', pages: float = 1.0, index: int | None = None) -> str:
		"""Scroll the page (or a scrollable element by index) by a number of viewport pages;
		10 reaches the end. direction is 'down' or 'up'."""
		params: dict[str, Any] = {'down': direction != 'up', 'pages': pages, 'index': index}
		return _clean(session, await session.require_run().act('scroll', params))

	@tool()
	async def send_keys(keys: str) -> str:
		"""Send keys or a shortcut to the page: Enter, Escape, Tab, PageDown, Control+a, Meta+c."""
		return _clean(session, await session.require_run().act('send_keys', {'keys': keys}))

	@tool()
	async def go_back() -> str:
		"""Go back one page in the browser history, like the Back button. Then call browser_state."""
		return _clean(session, await session.require_run().act('go_back', {}))

	@tool()
	async def list_tabs() -> str:
		"""Open tabs with their 4-character ids, for switch_tab / close_tab."""
		return await session.require_run().tabs()

	@tool()
	async def switch_tab(tab_id: str) -> str:
		"""Make a tab current, by the 4-character id from browser_state or list_tabs."""
		return _clean(session, await session.require_run().act('switch', {'tab_id': tab_id}))

	@tool()
	async def close_tab(tab_id: str) -> str:
		"""Close a tab by its 4-character id."""
		return _clean(session, await session.require_run().act('close', {'tab_id': tab_id}))

	@tool()
	async def wait(seconds: float = 3) -> str:
		"""Pause (max 30s) for a slow page or an animation, then call browser_state."""
		return await session.require_run().wait(seconds)

	@tool()
	async def request_permission(key: str, description: str) -> str:
		"""MUST be called before anything irreversible or affecting real users or data: deleting,
		submitting real orders or payments, sending emails or messages, changing settings. Pass a
		short stable key like `delete-test-user` and one line of why. A native dialog asks the
		human (allow once / this session / always / deny) unless a prior grant applies. If DENIED:
		do not do it, record the step as "not tested - permission denied" in finish_run, and
		continue with the rest."""
		return (await session.require_hitl().decide_permission(key, description)).content

	@tool()
	async def ask_credential(name: str) -> str:
		"""Get a credential (names come from the scenario, the appmap or vault.yaml, e.g.
		`qa_user`, `qa_password`) released for the current page. It comes from the vault (the
		human grants it in a dialog; some are bound to an origin and refused elsewhere) or is
		typed by the human into a hidden dialog. You never see the value: on success, type the
		literal text `<secret>name</secret>` with type_text. If refused, skip that flow and note
		the step as "not tested - credential denied"."""
		page_url = await session.require_run().current_url()
		return (await session.require_hitl().release_credential(name, page_url)).content

	@tool()
	async def finish_run(steps: list[StepVerdict], summary: str = '') -> str:
		"""End the scenario run with one verdict per scenario step: {step, verdict, note}.
		pass = the action worked and the expectation held; fail = the app misbehaved (note MUST
		say expected vs actual); blocked = could not attempt it (never reached, permission or
		credential denied). Writes results.md/results.json, closes the browser (video saved),
		and returns the overall verdict plus facts about the run. Then call update_appmap if the
		run taught something, and tell the human the verdict and where the evidence is."""
		s = session.scenario
		if s is None:
			session.require_run()
			raise ValueError('This is an explore, not a scenario run: use finish_explore(summary).')
		run_dir = await session.end_run()
		verdict = write_results(run_dir, s, ScenarioResult(steps=steps, summary=summary), evidence=DRIVER_EVIDENCE)
		session.scenario = None
		return _clean(
			session,
			f'{ICONS[verdict]} {s.id}: {verdict.upper()}\nReport: {run_dir / "results.md"}\n'
			f'Evidence: {run_dir}/steps.json, steps/, videos/\n\n{run_facts(run_dir)}\n\n'
			'If this run taught something the appmap lacks or gets wrong, call update_appmap. '
			'Then tell the human the verdict and where the evidence is.',
		)

	@tool()
	async def finish_explore(summary: str = '') -> str:
		"""End an explore run: closes the browser (video saved) and writes your summary to
		runs/<name>/notes.md. Then update_appmap with what you learned about the app."""
		if session.scenario is not None:
			raise ValueError('This is a scenario run: use finish_run(steps, summary).')
		run_dir = await session.end_run()
		(run_dir / 'notes.md').write_text(
			f'# Explore — {datetime.now():%Y-%m-%d %H:%M}\n\n{summary.strip() or "(no summary)"}\n', encoding='utf-8'
		)
		return _clean(
			session,
			f'Closed. Notes: {run_dir / "notes.md"}\nEvidence: {run_dir}/steps.json, steps/, videos/\n\n'
			f'{run_facts(run_dir)}\n\nNow update_appmap with what you learned.',
		)

	@tool()
	async def abort_run() -> str:
		"""Stop the active run early: evidence so far is kept, a scenario run gets a blocked
		verdict, and the browser closes."""
		s = session.scenario
		run_dir = await session.end_run(aborted=True)
		session.scenario = None
		if s is not None:
			write_results(run_dir, s, None, evidence=DRIVER_EVIDENCE)
			return f'Aborted {s.id}: verdict BLOCKED. Evidence kept under {run_dir}.'
		return f'Aborted. Evidence kept under {run_dir}.'

	@tool()
	async def list_runs() -> str:
		"""Recorded runs, newest first, with when they ran and how many steps were recorded."""
		ws = session.require_ws()
		runs = list(reversed(recorded_runs(ws.runs_dir)))
		if not runs:
			return 'No runs recorded yet.'
		lines: list[str] = []
		for d in runs:
			rec = recording_file(d)
			when = datetime.fromtimestamp(rec.stat().st_mtime).strftime('%Y-%m-%d %H:%M') if rec else ''
			lines.append(f'- {d.name}  {when}  {step_count(rec) if rec else "?"} steps')
		return '\n'.join(lines)

	@tool()
	async def read_run(name: str) -> str:
		"""A past run: its report (results.md or notes.md) and the step log - actions, URLs,
		errors and screenshot paths."""
		ws = session.require_ws()
		run_dir = ws.runs_dir / name
		if not run_dir.is_dir():
			raise ValueError(f'No run "{name}". See list_runs.')
		parts: list[str] = []
		for report in ('results.md', 'notes.md'):
			if (run_dir / report).is_file():
				parts.append((run_dir / report).read_text(encoding='utf-8'))
		log = read_step_log(run_dir)
		if log is not None:
			parts.append(
				'Steps:\n'
				+ '\n'.join(
					f'{st.n}. {st.action} {st.params} -> {st.url_after}'
					+ (f' ERROR {st.error}' if st.error else '')
					+ (f' [{st.screenshot}]' if st.screenshot else '')
					for st in log.steps
				)
			)
		elif recording_file(run_dir) is not None:
			parts.append('(recorded by browser-use; see history.json)')
		return _clean(session, '\n\n'.join(parts) or '(empty run)')

	# --- jira ------------------------------------------------------------------------

	@tool()
	async def read_ticket(key: str) -> str:
		"""Read a Jira issue (key or browse URL) - summary, status, description - to plan
		scenarios from its acceptance criteria. Needs a `jira` MCP server in config.yaml."""
		session.require_ws()
		issue = issue_key(key)
		if not issue:
			raise ValueError(f'"{key}" does not look like a Jira key or URL.')
		ch = CapturingChannel(session.dialogs)
		text, error = await read_issue(session.config, ch, issue)
		if error:
			raise ValueError(error)
		return text

	@tool()
	async def file_bug(run: str, step: int = 0) -> str:
		"""Only when the human asks: compose a bug from a failed step of a scenario run and ask the
		human to confirm it in a native dialog before creating it in Jira."""
		ws = session.require_ws()
		ch = CapturingChannel(session.dialogs)
		await actions.file_bug(ws, ch, run, step, config=session.config)
		return ch.text

	# --- prompts: the workflows, as slash commands ---------------------------------------

	def _context() -> str:
		if session.ws is None:
			return '(No workspace is active: call list_workspaces, then use_workspace, then read_appmap.)'
		return gather_context(session.ws, session.config)

	@server.prompt()
	def plan(ask: str = '', ticket: str = '', area: str = '') -> str:
		"""Draft test scenarios from what nkqa knows about the app (no browser)."""
		want = ask.strip() or "the human's request in this conversation"
		extra = f'\nRead the ticket first: read_ticket("{ticket}").' if ticket else ''
		extra += f'\nPut every scenario under the area "{area}".' if area else ''
		return (
			f'{PLANNER_SYSTEM}\n{_context()}\n\n### Ask\n{want}{extra}\n\n'
			'For each scenario call write_scenario. Then list the ids you wrote and ask the human to review them; '
			'call approve_scenario only for the ones they say to approve.'
		)

	@server.prompt()
	def run(scenario_id: str = '') -> str:
		"""Execute an approved scenario in the browser and record evidence."""
		which = f'"{scenario_id}"' if scenario_id else 'the scenario the human named (see list_scenarios)'
		return (
			f'{QA_RULES_MCP}\nExecute {which}: start_run, then loop browser_state -> one action, verifying '
			'every EXPECT. Use ask_credential for logins and request_permission before anything irreversible. '
			'End with finish_run(steps, summary), update_appmap if you learned something, and report the '
			'verdict and the run folder to the human.'
		)

	@server.prompt()
	def explore(url: str = '', focus: str = '') -> str:
		"""Explore the app freely and record what you find."""
		where = f' at {url}' if url else ''
		what = f' Focus on: {focus}.' if focus else ''
		return (
			f'{QA_RULES_MCP}\nExplore the app{where} with start_explore(name, url), read-only unless '
			f'request_permission grants otherwise.{what} Look for broken flows and note expected vs actual. '
			'End with finish_explore(summary) and update_appmap with what you learned.'
		)

	return server


class WatchedStdin(anyio.AsyncFile[str]):
	"""Client gone = stdin closed. Nothing else tells this process the agent has left."""

	def __init__(self, fp: IO[str], on_eof: Callable[[], None]):
		super().__init__(fp)
		self.on_eof = on_eof

	async def __aiter__(self) -> AsyncIterator[str]:  # pyright: ignore[reportIncompatibleMethodOverride]
		async for line in super().__aiter__():
			yield line
		self.on_eof()


def serve(session: Session, server: FastMCP) -> int:
	# stdout is the protocol from here on. Anything that prints - browser-use, a library, a
	# stray debug line - lands on stderr, and the transport keeps the only handle to the real thing.
	real_stdout = sys.stdout
	sys.stdout = sys.stderr

	async def run() -> None:
		loop = asyncio.get_running_loop()

		def on_eof() -> None:
			# Every open dialog closes and answers '' (deny), so a tool blocked on one returns.
			# A tool stuck in the browser gets a grace period, then the process ends regardless.
			session.abandon()
			loop.call_later(EXIT_GRACE, os._exit, 1)

		for sig in (signal.SIGTERM, signal.SIGINT):
			with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
				loop.add_signal_handler(sig, on_eof)
		stdin = WatchedStdin(io.TextIOWrapper(sys.stdin.buffer, encoding='utf-8'), on_eof)
		stdout = anyio.wrap_file(io.TextIOWrapper(real_stdout.buffer, encoding='utf-8'))
		try:
			async with stdio_server(stdin=stdin, stdout=stdout) as (read, write):
				with contextlib.suppress(BrokenPipeError):
					await server._mcp_server.run(  # pyright: ignore[reportPrivateUsage]
						read,
						write,
						server._mcp_server.create_initialization_options(),  # pyright: ignore[reportPrivateUsage]
					)
		finally:
			await session.close()  # an open browser closes and its evidence lands

	anyio.run(run)
	return 0


def main(workspace: str = '') -> int:
	os.environ.setdefault('BROWSER_USE_LOGGING_LEVEL', 'warning')
	session = Session()
	if workspace:
		ws = workspace_mod.at(Path(workspace).expanduser())
		if ws is None:
			print(f'{workspace} is not a QA workspace (no config.yaml + appmap/).', file=sys.stderr)
			return 2
	else:
		ws = workspace_mod.find()  # the agent's cwd is usually the project; else use_workspace
	if ws is not None:
		session.use(ws)
	return serve(session, build_server(session))


if __name__ == '__main__':
	sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ''))
