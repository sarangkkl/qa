"""Human-in-the-loop tools: ask a question, collect a credential, request permission.

Secrets live only in memory (passed to the Agent as sensitive_data and referenced via
<secret>name</secret> placeholders); they are never written to disk or logs.
"""

import asyncio
import getpass
import json
from pathlib import Path

from browser_use import ActionResult, Tools


def is_hidden(key: str) -> bool:
	return any(marker in key for marker in ('pass', 'token', 'secret', 'otp'))


async def ask_terminal(prompt: str, hidden: bool = False) -> str:
	"""Ask the human in the terminal without blocking the event loop."""
	print('\n' + '=' * 60)
	if hidden:
		answer = await asyncio.to_thread(getpass.getpass, prompt)
	else:
		answer = await asyncio.to_thread(input, prompt)
	print('=' * 60)
	return answer.strip()


class HumanInTheLoop:
	"""Per-run HITL state + the Tools registry exposing it to the agent."""

	def __init__(self, permissions_file: Path):
		self.permissions_file = permissions_file
		self.secrets: dict[str, str | dict[str, str]] = {}  # mutated live by ask_credential
		self.session_grants: set[str] = set()

	def _load_always_grants(self) -> set[str]:
		try:
			return set(json.loads(self.permissions_file.read_text()))
		except (FileNotFoundError, json.JSONDecodeError):
			return set()

	def _save_always_grant(self, key: str) -> None:
		self.permissions_file.parent.mkdir(parents=True, exist_ok=True)
		grants = self._load_always_grants() | {key}
		self.permissions_file.write_text(json.dumps(sorted(grants), indent=1))

	async def collect_secret(self, key: str, prompt: str) -> str:
		value = await ask_terminal(prompt, hidden=is_hidden(key))
		if value:
			self.secrets[key] = value
		return value

	def build_tools(self) -> Tools[None]:
		tools: Tools[None] = Tools()

		@tools.registry.action(
			'Ask the human developer a question. Use this whenever the task is ambiguous, '
			'you are blocked, or you need information you do not have. Never guess or invent data.'
		)
		async def ask_human(question: str) -> ActionResult:  # pyright: ignore[reportUnusedFunction]
			answer = await ask_terminal(f'🤖 QA agent asks: {question}\nYour answer: ')
			return ActionResult(
				extracted_content=f'The human answered: {answer}',
				long_term_memory=f'Asked human: "{question}" -> answer: "{answer}"',
			)

		@tools.registry.action(
			'Ask the human for a credential (e.g. name="username" or name="password") when you hit a '
			'login form. The value is stored securely. You will NOT see the value - after this, put the '
			'placeholder <secret>name</secret> into the input action text and the real value is filled in.'
		)
		async def ask_credential(name: str) -> ActionResult:  # pyright: ignore[reportUnusedFunction]
			key = name.strip().lower().replace(' ', '_')
			value = await self.collect_secret(key, f'🔑 QA agent needs "{key}" to continue: ')
			if not value:
				return ActionResult(
					extracted_content=f'Human provided no value for {key}. Skip this flow and note it as untestable.'
				)
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
		async def request_permission(permission_key: str, description: str) -> ActionResult:  # pyright: ignore[reportUnusedFunction]
			key = permission_key.strip().lower()
			if key in self._load_always_grants():
				return ActionResult(extracted_content=f'Permission "{key}" granted (previously allowed always).')
			if key in self.session_grants:
				return ActionResult(extracted_content=f'Permission "{key}" granted (allowed for this session).')

			choice = await ask_terminal(
				f'⚠️  QA agent requests permission: {description}\n'
				f'    key: {key}\n'
				f'[y] allow once  [s] allow this session  [a] allow always  [n] deny: '
			)
			choice = choice.lower()[:1]
			if choice == 'a':
				self._save_always_grant(key)
				return ActionResult(extracted_content=f'Permission "{key}" granted permanently.')
			if choice == 's':
				self.session_grants.add(key)
				return ActionResult(extracted_content=f'Permission "{key}" granted for this session.')
			if choice == 'y':
				return ActionResult(extracted_content=f'Permission "{key}" granted once. Ask again next time.')
			return ActionResult(
				extracted_content=f'Permission "{key}" DENIED. Do not perform this action. '
				'Record it in your report as "not tested - permission denied" and continue with other tests.',
				long_term_memory=f'Permission denied for: {description}',
			)

		return tools
