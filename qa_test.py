"""An AI QA teammate that asks YOU when it is unsure.

It can:
  - ask a question when the task is ambiguous or it is stuck
  - ask for username/password when it hits a login (password stays hidden from the LLM)
  - ask permission before dangerous actions: allow once / this session / always / deny

Every recording is saved as a NAMED test under qa_output/tests/<name>/.

Usage:
	python qa_test.py                                  # greet + record a new AI-driven test
	python qa_test.py https://your-app.com "checkout" --name checkout-flow
	python qa_test.py --list                           # show all recorded tests
	python qa_test.py --replay checkout-flow           # rerun one test WITHOUT the LLM
	python qa_test.py --replay-all                     # rerun the whole suite (CI mode)
	python qa_test.py --replay checkout-flow --var email=x@y.com
"""

import argparse
import asyncio
import getpass
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

from browser_use import ActionResult, Agent, Tools
from browser_use.browser import BrowserProfile

OUTPUT_DIR = Path('./qa_output')
TESTS_DIR = OUTPUT_DIR / 'tests'
PERMISSIONS_FILE = OUTPUT_DIR / 'qa_permissions.json'

# Shared state between the actions and the Agent
secrets: dict[str, str | dict[str, str]] = {}  # passed as sensitive_data; mutated live by ask_credential
session_grants: set[str] = set()  # permissions granted for this run only


def slugify(text: str) -> str:
	"""Turn free text into a meaningful folder name: 'Login flow!' -> 'login-flow'."""
	slug = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')
	return slug[:40].rstrip('-') or 'unnamed-test'


def _load_always_grants() -> set[str]:
	try:
		return set(json.loads(PERMISSIONS_FILE.read_text()))
	except (FileNotFoundError, json.JSONDecodeError):
		return set()


def _save_always_grant(key: str) -> None:
	OUTPUT_DIR.mkdir(exist_ok=True)
	grants = _load_always_grants() | {key}
	PERMISSIONS_FILE.write_text(json.dumps(sorted(grants), indent=1))


async def _ask_terminal(prompt: str, hidden: bool = False) -> str:
	"""Ask the human in the terminal without blocking the event loop."""
	print('\n' + '=' * 60)
	if hidden:
		answer = await asyncio.to_thread(getpass.getpass, prompt)
	else:
		answer = await asyncio.to_thread(input, prompt)
	print('=' * 60)
	return answer.strip()


tools = Tools()


@tools.registry.action(
	'Ask the human developer a question. Use this whenever the task is ambiguous, '
	'you are blocked, or you need information you do not have. Never guess or invent data.'
)
async def ask_human(question: str) -> ActionResult:
	answer = await _ask_terminal(f'🤖 QA agent asks: {question}\nYour answer: ')
	return ActionResult(
		extracted_content=f'The human answered: {answer}',
		long_term_memory=f'Asked human: "{question}" -> answer: "{answer}"',
	)


@tools.registry.action(
	'Ask the human for a credential (e.g. name="username" or name="password") when you hit a '
	'login form. The value is stored securely. You will NOT see the value - after this, put the '
	'placeholder <secret>name</secret> into the input action text and the real value is filled in.'
)
async def ask_credential(name: str) -> ActionResult:
	key = name.strip().lower().replace(' ', '_')
	hidden = 'pass' in key or 'token' in key or 'secret' in key or 'otp' in key
	value = await _ask_terminal(f'🔑 QA agent needs "{key}" to continue: ', hidden=hidden)
	if not value:
		return ActionResult(extracted_content=f'Human provided no value for {key}. Skip this flow and note it as untestable.')
	secrets[key] = value
	return ActionResult(
		extracted_content=f'Stored. To use it, type the literal text <secret>{key}</secret> into the field.',
		long_term_memory=f'Credential "{key}" collected from human; usable as <secret>{key}</secret>.',
	)


@tools.registry.action(
	'MUST be called before any dangerous or irreversible action: deleting data, submitting real '
	'orders/payments, sending emails or messages, changing account settings, or anything affecting '
	'real users. Pass a short stable permission_key (e.g. "delete-test-user") and a one-line '
	'description of what you want to do and why. Only proceed if permission is granted.'
)
async def request_permission(permission_key: str, description: str) -> ActionResult:
	key = permission_key.strip().lower()
	if key in _load_always_grants():
		return ActionResult(extracted_content=f'Permission "{key}" granted (previously allowed always).')
	if key in session_grants:
		return ActionResult(extracted_content=f'Permission "{key}" granted (allowed for this session).')

	choice = await _ask_terminal(
		f'⚠️  QA agent requests permission: {description}\n'
		f'    key: {key}\n'
		f'[y] allow once  [s] allow this session  [a] allow always  [n] deny: '
	)
	choice = choice.lower()[:1]
	if choice == 'a':
		_save_always_grant(key)
		return ActionResult(extracted_content=f'Permission "{key}" granted permanently.')
	if choice == 's':
		session_grants.add(key)
		return ActionResult(extracted_content=f'Permission "{key}" granted for this session.')
	if choice == 'y':
		return ActionResult(extracted_content=f'Permission "{key}" granted once. Ask again next time.')
	return ActionResult(
		extracted_content=f'Permission "{key}" DENIED. Do not perform this action. '
		'Record it in your report as "not tested - permission denied" and continue with other tests.',
		long_term_memory=f'Permission denied for: {description}',
	)


QA_RULES = """
You are working as a QA engineer alongside a human developer. Core rules:
1. NEVER guess. If the task is ambiguous or you are blocked, call ask_human.
2. NEVER invent credentials or personal data. If a login/signup blocks you, call ask_credential.
3. Before ANY dangerous or irreversible action (delete, purchase, send, settings change),
   call request_permission first. If denied, skip it and note it in the report.
4. A broken feature is a FINDING, not an obstacle. Do not work around bugs -
   record exact steps to reproduce, expected vs actual behavior, then continue testing other flows.
5. Finish by writing results.md: what was tested, what passed, every bug found, what was skipped and why.
"""


def recorded_tests() -> list[Path]:
	"""All test dirs that contain a recording, oldest first."""
	if not TESTS_DIR.is_dir():
		return []
	dirs = [d for d in TESTS_DIR.iterdir() if (d / 'history.json').is_file()]
	return sorted(dirs, key=lambda d: (d / 'history.json').stat().st_mtime)


def list_tests() -> int:
	tests = recorded_tests()
	if not tests:
		print('No recorded tests yet. Record one: python qa_test.py <url>')
		return 0
	print(f'\n{"TEST":<32} {"RECORDED":<18} STEPS')
	for d in tests:
		hist = d / 'history.json'
		when = datetime.fromtimestamp(hist.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
		try:
			steps = len(json.loads(hist.read_text(encoding='utf-8')).get('history', []))
		except (json.JSONDecodeError, OSError):
			steps = '?'
		print(f'{d.name:<32} {when:<18} {steps}')
	print(f'\nReplay one:  python qa_test.py --replay <name>\nReplay all:  python qa_test.py --replay-all')
	return 0


async def greet(url_arg: str, focus_arg: str, name_arg: str) -> tuple[str, str, str]:
	"""Greet the developer like a colleague and collect the job details."""
	print()
	print('👋 Hey! QA here. Ready when you are.')
	url = url_arg
	while not url:
		url = (await asyncio.to_thread(input, '   Which app should I test today? (paste the link): ')).strip()
	if not url.startswith(('http://', 'https://')):
		url = 'https://' + url
	focus = focus_arg or (await asyncio.to_thread(input, '   Anything specific to focus on? (Enter = full smoke test): ')).strip()
	focus = focus or 'all main user flows'

	name = slugify(name_arg) if name_arg else ''
	while not name:
		parsed = urlparse(url)
		# suggest from the first few words of the focus, so long sentences don't become ugly slugs
		suggestion = (
			slugify(' '.join(focus.split()[:4]))
			if focus != 'all main user flows'
			else slugify(f'{parsed.hostname or "site"} {parsed.path} smoke')
		)
		typed = (await asyncio.to_thread(input, f'   Name this test (Enter = "{suggestion}"): ')).strip()
		name = slugify(typed) if typed else suggestion
		if (TESTS_DIR / name / 'history.json').exists():
			answer = (await asyncio.to_thread(input, f'   Test "{name}" already exists. Overwrite it? [y/N]: ')).strip().lower()
			if answer != 'y':
				name = ''  # ask again

	print(f"   Got it - test \"{name}\" on {url}, focus: {focus}. I'll ask if I need anything. 🎬 Recording video.\n")
	return url, focus, name


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument('url', nargs='?', default='', help='URL to test (record mode)')
	parser.add_argument('focus', nargs='?', default='', help='what to focus on (record mode)')
	parser.add_argument('--name', default='', metavar='TEST_NAME', help='meaningful name for this recording (record mode)')
	parser.add_argument('--list', action='store_true', help='list all recorded tests')
	parser.add_argument(
		'--replay',
		nargs='?',
		const='',
		default=None,
		metavar='TEST_NAME',
		help='replay a recorded test deterministically, without the LLM deciding anything',
	)
	parser.add_argument('--replay-all', action='store_true', help='replay every recorded test (CI mode)')
	parser.add_argument(
		'--var',
		action='append',
		default=[],
		metavar='KEY=VALUE',
		help='override a recorded value during replay (repeatable)',
	)
	return parser.parse_args()


async def _collect_replay_secrets(history_file: Path) -> None:
	"""If the recording used credentials, ask the human for them again (values are never stored)."""
	needed = set(re.findall(r'<secret>(.*?)</secret>', history_file.read_text(encoding='utf-8')))
	for key in sorted(needed - set(secrets)):
		hidden = 'pass' in key or 'token' in key or 'secret' in key or 'otp' in key
		secrets[key] = await _ask_terminal(f'🔑 The recording uses "{key}". Enter it for this replay: ', hidden=hidden)


def _resolve_history_file(name_or_path: str) -> Path | None:
	"""Accept a test name, a test dir, or a direct path to a history.json."""
	if not name_or_path:
		tests = recorded_tests()
		if len(tests) == 1:
			return tests[0] / 'history.json'
		list_tests()
		return None
	candidates = [
		TESTS_DIR / slugify(name_or_path) / 'history.json',
		Path(name_or_path) / 'history.json',
		Path(name_or_path),
	]
	for c in candidates:
		if c.is_file():
			return c
	print(f'No recording found for "{name_or_path}".')
	list_tests()
	return None


async def replay(history_file: Path, var_pairs: list[str]) -> int:
	"""Rerun a recorded session step by step - no LLM decisions, free and repeatable."""
	variables: dict[str, str] = {}
	for pair in var_pairs:
		key, _, value = pair.partition('=')
		variables[key.strip()] = value

	test_dir = history_file.parent if history_file.parent.parent == TESTS_DIR else OUTPUT_DIR
	await _collect_replay_secrets(history_file)
	print(f'\n▶️  Replaying {test_dir.name}' + (f' with overrides {list(variables)}' if variables else ''))

	agent = Agent(
		task=f'Replay of recorded QA test "{test_dir.name}"',
		tools=tools,
		sensitive_data=secrets,
		browser_profile=BrowserProfile(headless=False, record_video_dir=test_dir / 'videos'),
		file_system_path=str(test_dir),
	)
	results = await agent.load_and_rerun(history_file, variables=variables or None, skip_failures=True)

	failed = 0
	print(f'\n=== REPLAY REPORT: {test_dir.name} ===')
	for i, r in enumerate(results, start=1):
		if r.error:
			failed += 1
			print(f'step {i:>2}: ❌ {r.error.splitlines()[0][:120]}')
		else:
			summary = (r.extracted_content or 'ok').splitlines()[0][:120]
			print(f'step {i:>2}: ✅ {summary}')
	print(f'\nVerdict: {"PASS" if failed == 0 else f"FAIL ({failed} step(s) failed)"}')
	return 0 if failed == 0 else 1


async def replay_all(var_pairs: list[str]) -> int:
	tests = recorded_tests()
	if not tests:
		print('No recorded tests yet. Record one: python qa_test.py <url>')
		return 2
	verdicts: dict[str, int] = {}
	for d in tests:
		verdicts[d.name] = await replay(d / 'history.json', var_pairs)
	print('\n=== SUITE SUMMARY ===')
	for name, code in verdicts.items():
		print(f'  {"✅ PASS" if code == 0 else "❌ FAIL"}  {name}')
	failed = sum(1 for c in verdicts.values() if c != 0)
	print(f'\n{len(verdicts) - failed}/{len(verdicts)} tests passed')
	return 0 if failed == 0 else 1


async def record(url_arg: str, focus_arg: str, name_arg: str) -> None:
	url, focus, name = await greet(url_arg, focus_arg, name_arg)
	test_dir = TESTS_DIR / name
	test_dir.mkdir(parents=True, exist_ok=True)

	from browser_use.llm.models import get_llm_by_name

	agent = Agent(
		task=f'Test the web application at {url}. Focus on: {focus}.',
		tools=tools,
		extend_system_message=QA_RULES,
		sensitive_data=secrets,  # same dict ask_credential writes into
		fallback_llm=get_llm_by_name('anthropic_claude_haiku_4_5'),  # cross-provider failover
		browser_profile=BrowserProfile(
			headless=False,
			record_video_dir=test_dir / 'videos',  # full session .mp4
		),
		generate_gif=str(test_dir / 'last_run.gif'),  # step-by-step summary gif
		save_conversation_path=test_dir / 'conversation',  # full LLM transcript, one file per step
		calculate_cost=True,
		file_system_path=str(test_dir),
	)
	async def checkpoint(active_agent: Agent) -> None:
		"""Save the recording after every step, so even a hard kill (double Ctrl+C) loses nothing."""
		try:
			active_agent.save_history(test_dir / 'history.json')
		except Exception:
			pass

	run_error: BaseException | None = None
	try:
		history = await agent.run(max_steps=30, on_step_end=checkpoint)
		print('\n=== FINAL RESULT ===')
		print(history.final_result())
	except (KeyboardInterrupt, asyncio.CancelledError) as e:
		run_error = e
		print('\n🛑 Run interrupted - saving the partial recording so nothing is lost...')
	except Exception as e:
		run_error = e
		print(f'\n💥 Run crashed ({type(e).__name__}: {e}) - saving the partial recording...')
	finally:
		# Even a dirty exit keeps its artifacts: partial history, gif, transcript, video.
		if agent.history.history:
			agent.save_history(test_dir / 'history.json')  # structured record, secrets redacted
			if not (test_dir / 'last_run.gif').exists():
				try:
					from browser_use.agent.gif import create_history_gif

					create_history_gif(task=agent.task, history=agent.history, output_path=str(test_dir / 'last_run.gif'))
				except Exception:
					pass  # gif needs at least one screenshot; skip quietly
			print(f'\n📼 Transcript: {test_dir / "conversation"}/')
			print(f'📄 Recording:  {test_dir / "history.json"}')
			print(f'▶️  Replay it anytime WITHOUT the LLM:  python qa_test.py --replay {name}')
			print('🎬 The video file is finalized only now - open it AFTER this message, not mid-run.')
		else:
			print('\nNo step completed, so there is nothing to save for this test.')

	if run_error is None:
		try:
			from browser_use.agent.variable_detector import detect_variables_in_history

			detected = detect_variables_in_history(agent.history)
			if detected:
				print('🔁 Values you can change on replay:')
				for var_name, var in detected.items():
					print(f'   --var {var_name}=...   (recorded: {var.original_value[:40]})')
		except Exception:
			pass
	elif not isinstance(run_error, KeyboardInterrupt):
		sys.exit(1)


async def main() -> None:
	args = parse_args()
	if args.list:
		sys.exit(list_tests())
	if args.replay_all:
		sys.exit(await replay_all(args.var))
	if args.replay is not None:
		history_file = _resolve_history_file(args.replay)
		if history_file is None:
			sys.exit(2)
		sys.exit(await replay(history_file, args.var))
	await record(args.url, args.focus, args.name)


if __name__ == '__main__':
	asyncio.run(main())
