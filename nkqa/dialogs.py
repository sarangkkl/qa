"""Native dialogs: how the product reaches the human when there is no terminal and no window.

`qa mcp` runs headless under someone else's agent. Its asks - approve a scenario, release a
credential, allow a risky action - must reach a human and never the agent, so they go to an
OS dialog. The agent only ever sees the outcome.

One backend per platform, each shelling out to something already on the machine: osascript,
PowerShell, zenity. They all print the same three shapes, so there is one parser:
  choice  -> the chosen line, e.g. "a  allow always"
  confirm -> the literal "Confirm", or nothing
  text/secret -> the value, or nothing

Every failure mode answers '' (Cancel, close, timeout, no dialog available, client gone):
'' is deny everywhere in nkqa, so a dialog that cannot be shown can never grant.
"""

import asyncio
import contextlib
import os
import shutil
import sys
from asyncio.subprocess import PIPE, Process

from nkqa.ui import Ask, Channel, Event

# An unattended prompt denies instead of holding a tool call open forever.
GIVE_UP = 900

# The human-readable half of a one-letter option, as the gates in hitl.py spell them.
LABELS = {'y': 'allow once', 's': 'allow this session', 'a': 'allow always', 'n': 'deny'}

# --- macOS ------------------------------------------------------------------
#
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

# --- Windows ----------------------------------------------------------------
#
# Values travel in the environment, not the command line: PowerShell quoting is the one place
# a scenario body full of quotes and newlines would turn into executable script.

WINDOWS = """$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$kind = $env:NKQA_KIND
if ($kind -eq 'confirm') {
	$r = [Windows.Forms.MessageBox]::Show($env:NKQA_BODY, $env:NKQA_PROMPT, 'OKCancel')
	if ($r -eq 'OK') { 'Confirm' }
	exit
}
$f = New-Object Windows.Forms.Form
$f.Text = 'nkqa'; $f.Width = 540; $f.Height = 260; $f.TopMost = $true; $f.StartPosition = 'CenterScreen'
$l = New-Object Windows.Forms.Label; $l.Text = $env:NKQA_PROMPT; $l.SetBounds(12, 10, 500, 70)
$f.Controls.Add($l)
if ($kind -eq 'choice') {
	$c = New-Object Windows.Forms.ListBox; $c.SetBounds(12, 85, 500, 80)
	foreach ($o in ($env:NKQA_OPTIONS -split "`n")) { [void]$c.Items.Add($o) }
	if ($c.Items.Count -gt 0) { $c.SelectedIndex = 0 }
} else {
	$c = New-Object Windows.Forms.TextBox; $c.SetBounds(12, 85, 500, 24)
	if ($kind -eq 'secret') { $c.UseSystemPasswordChar = $true }
}
$f.Controls.Add($c)
$ok = New-Object Windows.Forms.Button; $ok.Text = 'OK'; $ok.DialogResult = 'OK'; $ok.SetBounds(330, 180, 84, 28)
$no = New-Object Windows.Forms.Button; $no.Text = 'Cancel'; $no.DialogResult = 'Cancel'; $no.SetBounds(424, 180, 84, 28)
$f.Controls.AddRange(@($ok, $no)); $f.AcceptButton = $ok; $f.CancelButton = $no
if ($f.ShowDialog() -eq 'OK') { if ($kind -eq 'choice') { $c.SelectedItem } else { $c.Text } }
"""


def label(option: str) -> str:
	return f'{option}  {LABELS[option]}' if option in LABELS else option


def powershell() -> str:
	return shutil.which('pwsh') or shutil.which('powershell') or ''


def backend() -> str:
	"""Which dialog tool this machine has, '' if none."""
	if sys.platform == 'darwin' and shutil.which('osascript'):
		return 'macos'
	if sys.platform == 'win32' and powershell():
		return 'windows'
	if shutil.which('zenity'):
		return 'linux'  # GNOME ships it; KDE users get the tkinter fallback below
	return ''


def command(request: Ask, which: str) -> tuple[list[str], dict[str, str]]:
	"""The argv (and any extra environment) that puts this ask on the human's screen."""
	options = [label(o) for o in request.options]
	if which == 'macos':
		if request.kind == 'choice':
			return ['osascript', '-e', CHOOSE, '--', request.prompt, *options], {}
		if request.kind == 'secret':
			return ['osascript', '-e', SECRET, '--', request.prompt], {}
		if request.kind == 'confirm':
			return ['osascript', '-e', CONFIRM, '--', request.prompt or 'Confirm', request.body or request.prompt], {}
		return ['osascript', '-e', TEXT, '--', request.prompt], {}

	if which == 'windows':
		env = {
			'NKQA_KIND': request.kind,
			'NKQA_PROMPT': request.prompt or 'nkqa',
			'NKQA_BODY': request.body or request.prompt,
			'NKQA_OPTIONS': '\n'.join(options),
		}
		return [powershell(), '-NoProfile', '-NonInteractive', '-STA', '-Command', WINDOWS], env

	title = ['--title=nkqa', f'--timeout={GIVE_UP}']
	if request.kind == 'choice':
		return ['zenity', '--list', *title, f'--text={request.prompt}', '--column=Choice', *options], {}
	if request.kind == 'secret':
		return ['zenity', '--entry', *title, f'--text={request.prompt}', '--hide-text'], {}
	if request.kind == 'confirm':
		# zenity --question says yes with an exit code and prints nothing, so the shell prints
		# the token the parser expects. Both values are argv, never spliced into the script.
		script = 'zenity --question --title=nkqa --default-cancel --text="$2" --ok-label="$1" && echo Confirm'
		return ['sh', '-c', script, 'sh', 'Confirm', request.body or request.prompt], {}
	return ['zenity', '--entry', *title, f'--text={request.prompt}'], {}


def parse(request: Ask, returncode: int | None, out: bytes) -> str:
	if returncode != 0:
		return ''  # Cancel, timeout, or the dialog tool itself failed: all deny
	answer = out.decode('utf-8', 'replace').strip()
	if request.kind == 'choice':
		return answer.split()[0] if answer else ''
	if request.kind == 'confirm':
		return 'y' if answer == 'Confirm' else ''
	return answer


async def spawn(argv: list[str], env: dict[str, str]) -> Process:
	"""The one seam: tests replace this with a fake process."""
	return await asyncio.create_subprocess_exec(
		*argv, stdout=PIPE, stderr=PIPE, env={**os.environ, **env} if env else None
	)


def tk_ask(request: Ask) -> str:
	"""The last resort: a bare Tk prompt when the interpreter has one."""
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
		if request.kind == 'choice':
			return (answer or '').strip().split()[0] if answer else ''
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
		which = backend()
		if not which:
			answer = await asyncio.to_thread(tk_ask, request)
			if not answer:
				print(f'[ask] {request.kind} denied: no native dialog available here', file=sys.stderr, flush=True)
			return answer

		argv, env = command(request, which)
		proc = await spawn(argv, env)
		self.live.add(proc)
		try:
			out, _ = await proc.communicate()
		except asyncio.CancelledError:
			with contextlib.suppress(ProcessLookupError):
				proc.kill()
			return ''
		finally:
			self.live.discard(proc)
		return parse(request, proc.returncode, out)
