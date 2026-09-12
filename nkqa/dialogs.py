"""Native dialogs: how the product reaches the human when there is no terminal and no window.

`qa mcp` runs headless under someone else's agent. Its asks - approve a scenario, release a
credential, allow a risky action - must reach a human and never the agent, so they go to an
OS dialog. The agent only ever sees the outcome.

Every failure mode answers '' (Cancel, close, timeout, no dialog available, client gone):
'' is deny everywhere in nkqa, so a dialog that cannot be shown can never grant.
"""

import asyncio
import contextlib
import shutil
import sys
from asyncio.subprocess import PIPE, Process

from nkqa.ui import Ask, Channel, Event

# An unattended prompt denies instead of holding a tool call open forever.
GIVE_UP = 900

# The human-readable half of a one-letter option, as the gates in hitl.py spell them.
LABELS = {'y': 'allow once', 's': 'allow this session', 'a': 'allow always', 'n': 'deny'}

# Text arrives as argv (`on run argv`), never spliced into the script: no AppleScript quoting
# of a scenario body or a prompt, and nothing the human typed is ever interpreted.
CHOOSE = """on run argv
	tell application "System Events"
		activate
		set picked to choose from list (rest of argv) with title "nkqa" with prompt (item 1 of argv) ¬
			OK button name "Choose" cancel button name "Deny"
	end tell
	if picked is false then return ""
	return item 1 of picked
end run"""

SECRET = f"""on run argv
	tell application "System Events"
		activate
		set r to display dialog (item 1 of argv) with title "nkqa needs a credential" ¬
			default answer "" with hidden answer buttons {{"Cancel", "OK"}} default button "OK" ¬
			giving up after {GIVE_UP}
	end tell
	if gave up of r then return ""
	return text returned of r
end run"""

CONFIRM = f"""on run argv
	tell application "System Events"
		activate
		set r to display dialog (item 2 of argv) with title (item 1 of argv) ¬
			buttons {{"Cancel", "Confirm"}} default button "Cancel" giving up after {GIVE_UP}
	end tell
	if gave up of r then return ""
	return button returned of r
end run"""

TEXT = f"""on run argv
	tell application "System Events"
		activate
		set r to display dialog (item 1 of argv) with title "nkqa" default answer "" ¬
			buttons {{"Cancel", "OK"}} default button "OK" giving up after {GIVE_UP}
	end tell
	if gave up of r then return ""
	return text returned of r
end run"""


def label(option: str) -> str:
	return f'{option}  {LABELS[option]}' if option in LABELS else option


async def spawn_osascript(script: str, args: list[str]) -> Process:
	"""The one seam: tests replace this with a fake process."""
	return await asyncio.create_subprocess_exec('osascript', '-e', script, '--', *args, stdout=PIPE, stderr=PIPE)


def has_osascript() -> bool:
	return sys.platform == 'darwin' and shutil.which('osascript') is not None


def tk_ask(request: Ask) -> str:
	"""Off macOS: a bare Tk prompt when the interpreter has one, otherwise no dialog at all."""
	try:
		import tkinter
		from tkinter import messagebox, simpledialog
	except ImportError:
		return ''
	root = tkinter.Tk()
	root.withdraw()
	try:
		if request.kind == 'confirm':
			return 'y' if messagebox.askokcancel('nkqa', f'{request.prompt}\n\n{request.body}'.strip()) else ''
		prompt = request.prompt
		if request.kind == 'choice':
			prompt += '\n' + '\n'.join(label(o) for o in request.options)
		answer = simpledialog.askstring('nkqa', prompt, show='*' if request.kind == 'secret' else '')
		return answer or ''
	finally:
		root.destroy()


class DialogChannel(Channel):
	"""Events go to stderr (the only stream that is ours); asks become OS dialogs."""

	def __init__(self) -> None:
		self.live: set[Process] = set()
		self._closed = False

	async def emit(self, event: Event) -> None:
		if event.kind != 'frame':
			print(f'[{event.kind}] {event.text}', file=sys.stderr, flush=True)

	def abandon(self) -> None:
		"""The client is gone: every open dialog closes, and every pending ask answers ''."""
		self._closed = True
		for proc in list(self.live):
			with contextlib.suppress(ProcessLookupError):
				proc.kill()

	async def ask(self, request: Ask) -> str:
		if self._closed:
			return ''
		if not has_osascript():
			answer = await asyncio.to_thread(tk_ask, request)
			if not answer:
				print(f'[ask] {request.kind} denied: no native dialog available here', file=sys.stderr, flush=True)
			return answer
		if request.kind == 'choice':
			script, args = CHOOSE, [request.prompt, *(label(o) for o in request.options)]
		elif request.kind == 'secret':
			script, args = SECRET, [request.prompt]
		elif request.kind == 'confirm':
			script, args = CONFIRM, [request.prompt or 'Confirm', request.body or request.prompt]
		else:
			script, args = TEXT, [request.prompt]

		proc = await spawn_osascript(script, args)
		self.live.add(proc)
		try:
			out, _ = await proc.communicate()
		except asyncio.CancelledError:
			with contextlib.suppress(ProcessLookupError):
				proc.kill()
			return ''
		finally:
			self.live.discard(proc)
		if proc.returncode != 0:
			return ''  # Cancel, closed, or osascript itself failed: all deny
		answer = out.decode('utf-8', 'replace').strip()
		if request.kind == 'choice':
			return answer.split()[0] if answer else ''
		if request.kind == 'confirm':
			return 'y' if answer == 'Confirm' else ''
		return answer
