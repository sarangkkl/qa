"""Fix the app map from a sentence (planner role).

The map already had three writers - ingest, reflect, crawl - and every one of them takes a
path, a run name or a page budget. None takes a correction. So when the agent believed
something false about the app, the only fix was to open the file yourself, and the loop
ARCHITECTURE describes ("humans correct it and the human edit wins") had no door.

This is that door. It writes through appmap.apply() like every other writer, so it inherits
the path sandbox and the scoped git commit: the review surface stays `git diff appmap/`, and
a correction that came out wrong is one `git revert` away.
"""

import difflib

from browser_use.llm.messages import SystemMessage, UserMessage

from nkqa import appmap
from nkqa.appmap import AppmapUpdate
from nkqa.config import Config
from nkqa.models import resolve_llm
from nkqa.ui import Channel, Event
from nkqa.workspace import Workspace

CORRECT_SYSTEM = """\
You maintain the QA knowledge base (appmap) of a web application. A human who knows the app
is telling you something you have wrong or are missing. They are the authority: apply it.

Rules:
- Change ONLY what the correction asserts. Return the complete new content of each file you
  touch, and touch as few as possible - usually one.
- Keep every other line word for word. Surrounding knowledge is not yours to tidy.
- Put the correction where it belongs: a screen's own facts in its pages/<slug>.md, a
  journey in flows/<slug>.md, app-wide facts in overview.md. Create a file if none fits.
- Never invent detail the human did not give you. If the correction implies more than it
  states, write what was said and put the rest in notes as a question.
- A page doc keeps its `**Route:** ` line intact - the crawler reads it back.
- If the correction is already reflected in the map, return NO files and say so in notes.
"""


def diff_for(before: str, after: str, name: str) -> str:
	lines = difflib.unified_diff(
		before.splitlines(), after.rstrip().splitlines(), fromfile=f'a/{name}', tofile=f'b/{name}', lineterm='', n=2
	)
	return '\n'.join(lines)


async def correct(ws: Workspace, config: Config, ch: Channel, instruction: str) -> int:
	if not instruction.strip():
		await ch.log('Tell me what is wrong, e.g.  qa correct "client rows are clickable, they open /clients/<id>"')
		return 2
	llm = resolve_llm(config, 'planner')
	if llm is None:
		await ch.log("Correcting needs a real model: set models.planner in config.yaml (e.g. 'smart').")
		return 2

	current = appmap.read_all(ws)
	if not current:
		await ch.log('The app map is empty - there is nothing to correct yet. Try /crawl or /learn first.')
		return 2

	await ch.log('✏️  Correcting the app map...')
	existing = '\n\n'.join(f'--- appmap/{name} ---\n{content}' for name, content in current.items())
	response = await llm.ainvoke(
		[
			SystemMessage(content=CORRECT_SYSTEM),
			UserMessage(content=f'Current app map:\n\n{existing}\n\n### Correction\n{instruction}'),
		],
		output_format=AppmapUpdate,
	)
	update = response.completion

	if not update.files:
		await ch.log(f'🗒️  Nothing changed: {update.notes or "the map already says this."}')
		return 0

	# Shown before the write, because "what did that just do to my notebook" is the question
	# every automatic edit raises, and git alone answers it too late to be reassuring.
	for f in update.files:
		patch = diff_for(current.get(f.file, ''), f.content, f.file)
		await ch.emit(Event('log', patch or f'appmap/{f.file}: no textual change', {'appmap': f.file, 'diff': patch}))

	written = appmap.apply(ws, update, 'appmap: corrected by you')
	for f in written:
		await ch.emit(Event('artifact', f'🗺️  appmap/{f}', {'appmap': f}))
	if update.notes:
		await ch.log(f'\n🗒️  {update.notes}')
	await ch.log(f'\n✅ Corrected {len(written)} file(s).  Undo:  git revert HEAD  ·  Review:  git diff HEAD~1 appmap/')
	return 0
