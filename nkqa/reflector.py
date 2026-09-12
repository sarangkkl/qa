"""Learn from runs: after each run, distill what actually happened into the appmap.

auto_reflect() is the hook the runners call - it can never raise, so a reflection
problem can never change a run's outcome or exit code.
"""

import json
from pathlib import Path
from typing import Any, cast

from browser_use.llm.messages import BaseMessage, SystemMessage, UserMessage

from nkqa import appmap
from nkqa.appmap import AppmapUpdate
from nkqa.config import Config
from nkqa.models import resolve_llm
from nkqa.ui import Channel
from nkqa.workspace import Workspace

REFLECT_SYSTEM = """\
You maintain the QA knowledge base (appmap) of a web application. You are given the
current appmap and the facts of one automated test run. Update ONLY what the run gives
evidence for: pages visited that the map lacks or describes wrongly, quirks observed
(slowness, surprising behavior, flaky spots), navigation facts, corrections. Merge
around existing content - never delete knowledge, keep files short and factual.
Return NO files when the run taught nothing new (routine pass on known ground).
Put uncertainties in notes instead of guessing. Paths are relative like 'pages/x.md'.
"""


def run_facts(run_dir: Path) -> str:
	"""Deterministic facts about a run: verdicts, pages visited, errors. No LLM."""
	parts: list[str] = [f'Run: {run_dir.name}']

	results = run_dir / 'results.json'
	if results.is_file():
		parts.append('Verdicts: ' + results.read_text(encoding='utf-8'))

	history = run_dir / 'history.json'
	step_log = run_dir / 'steps.json'
	urls: list[str] = []
	errors: list[str] = []
	executed = 0
	if history.is_file():
		try:
			steps: list[dict[str, Any]] = json.loads(history.read_text(encoding='utf-8')).get('history', [])
		except (json.JSONDecodeError, OSError):
			steps = []
		executed = len(steps)
		for step in steps:
			state: dict[str, Any] = step.get('state') or {}
			_note_url(urls, str(state.get('url') or ''))
			results_raw: list[Any] = step.get('result') or []
			for r in results_raw:
				if not isinstance(r, dict):
					continue
				error: Any = cast(dict[str, Any], r).get('error')
				if error:
					errors.append(str(error).splitlines()[0][:200])
	elif step_log.is_file():
		# The MCP driver's log: the external agent chose each action, nkqa recorded it.
		try:
			recorded: list[dict[str, Any]] = json.loads(step_log.read_text(encoding='utf-8')).get('steps', [])
		except (json.JSONDecodeError, OSError):
			recorded = []
		executed = len(recorded)
		for step in recorded:
			_note_url(urls, str(step.get('url_after') or ''))
			if step.get('error'):
				errors.append(str(step['error']).splitlines()[0][:200])
	if history.is_file() or step_log.is_file():
		parts.append(f'Steps executed: {executed}')
		if urls:
			parts.append('Pages visited:\n' + '\n'.join(f'- {u}' for u in urls))
		if errors:
			parts.append('Errors seen:\n' + '\n'.join(f'- {e}' for e in errors))

	return '\n\n'.join(parts)


def _note_url(urls: list[str], url: str) -> None:
	if url and url not in urls and not url.startswith('about:'):
		urls.append(url)


def _messages(ws: Workspace, facts: str) -> list[BaseMessage]:
	current = appmap.read_all(ws)
	existing = '\n\n'.join(f'--- appmap/{name} (current) ---\n{content}' for name, content in current.items())
	return [
		SystemMessage(content=REFLECT_SYSTEM),
		UserMessage(content=f'Current appmap:\n\n{existing or "(empty)"}\n\n### The run\n\n{facts}'),
	]


async def reflect(ws: Workspace, config: Config, ch: Channel, run_dir: Path) -> list[str]:
	"""Reflect on one run; returns written appmap files. Raises on real errors (qa reflect surfaces them)."""
	llm = resolve_llm(config, 'reflector')
	if llm is None:
		return []
	facts = run_facts(run_dir)
	response = await llm.ainvoke(_messages(ws, facts), output_format=AppmapUpdate)
	update = response.completion
	written = appmap.apply(ws, update, f'appmap: learned from {run_dir.name}')
	if update.notes:
		await ch.log(f'🗒️  Reflection notes: {update.notes}')
	return written


async def auto_reflect(ws: Workspace, config: Config, ch: Channel, run_dir: Path) -> None:
	"""Post-run hook. Never raises; a reflection failure must not touch the run's outcome."""
	if not config.auto_reflect:
		return
	try:
		written = await reflect(ws, config, ch, run_dir)
		if written:
			await ch.log(f'🧠 appmap updated from this run: {", ".join(written)}')
	except Exception as e:
		await ch.log(f'⚠️  Reflection skipped ({type(e).__name__}: {str(e)[:120]})')
