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
	if history.is_file():
		try:
			steps: list[dict[str, Any]] = json.loads(history.read_text(encoding='utf-8')).get('history', [])
		except (json.JSONDecodeError, OSError):
			steps = []
		urls: list[str] = []
		errors: list[str] = []
		for step in steps:
			state: dict[str, Any] = step.get('state') or {}
			url = str(state.get('url') or '')
			if url and url not in urls and not url.startswith('about:'):
				urls.append(url)
			results_raw: list[Any] = step.get('result') or []
			for r in results_raw:
				if not isinstance(r, dict):
					continue
				error: Any = cast(dict[str, Any], r).get('error')
				if error:
					errors.append(str(error).splitlines()[0][:200])
		parts.append(f'Steps executed: {len(steps)}')
		if urls:
			parts.append('Pages visited:\n' + '\n'.join(f'- {u}' for u in urls))
		if errors:
			parts.append('Errors seen:\n' + '\n'.join(f'- {e}' for e in errors))

	return '\n\n'.join(parts)


def _messages(ws: Workspace, facts: str) -> list[BaseMessage]:
	current = appmap.read_all(ws)
	existing = '\n\n'.join(f'--- appmap/{name} (current) ---\n{content}' for name, content in current.items())
	return [
		SystemMessage(content=REFLECT_SYSTEM),
		UserMessage(content=f'Current appmap:\n\n{existing or "(empty)"}\n\n### The run\n\n{facts}'),
	]


async def reflect(ws: Workspace, config: Config, run_dir: Path) -> list[str]:
	"""Reflect on one run; returns written appmap files. Raises on real errors (qa reflect surfaces them)."""
	llm = resolve_llm(config, 'reflector')
	if llm is None:
		return []
	facts = run_facts(run_dir)
	response = await llm.ainvoke(_messages(ws, facts), output_format=AppmapUpdate)
	update = response.completion
	written = appmap.apply(ws, update, f'appmap: learned from {run_dir.name}')
	if update.notes:
		print(f'🗒️  Reflection notes: {update.notes}')
	return written


async def auto_reflect(ws: Workspace, config: Config, run_dir: Path) -> None:
	"""Post-run hook. Never raises; a reflection failure must not touch the run's outcome."""
	if not config.auto_reflect:
		return
	try:
		written = await reflect(ws, config, run_dir)
		if written:
			print(f'🧠 appmap updated from this run: {", ".join(written)}')
	except Exception as e:
		print(f'⚠️  Reflection skipped ({type(e).__name__}: {str(e)[:120]})')
