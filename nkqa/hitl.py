"""Human-in-the-loop tools: ask a question, collect a credential, request permission.

Secrets live only in memory (passed to the Agent as sensitive_data and referenced via
<secret>name</secret> placeholders); the model never sees a value. The channel carries the
prompt, never the answer: a collected value goes straight into self.secrets and is never
put back into an Event.

Credentials the vault knows about follow the release chain in `ask_credential`. Everything
else still works exactly as before: ask the human, hold it for the session, forget it.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from browser_use import ActionResult, Tools

from nkqa.ui import Channel, Event, is_hidden
from nkqa.vault import Vault, load_grants, normalize, origin_allows, save_grants

__all__ = ['Decision', 'HumanInTheLoop', 'is_hidden']

PERMISSION_CHOICES = ['y', 's', 'a', 'n']


@dataclass
class Decision:
	"""What a gate decided, in words the agent acts on. `memory` is the one-line audit trail
	that outlives the channel (it lands in history.json when browser-use is the driver)."""

	ok: bool
	content: str
	memory: str = ''

	def action_result(self) -> ActionResult:
		return ActionResult(extracted_content=self.content, long_term_memory=self.memory or None)


# How the session answers `request_permission` when it would otherwise stop and ask.
# Session-scoped and never persisted: there is no config.yaml key for this, so a workspace
# can never be born permissive, and a reconnect always lands back on 'ask'.
#
# This governs actions the agent itself declares risky. It is NOT a sandbox: 'refuse' stops
# the agent asking for permission, it does not stop a browser from clicking something.
AUTONOMY = ('ask', 'allow', 'refuse')
Autonomy = Literal['ask', 'allow', 'refuse']


class HumanInTheLoop:
	"""Per-run HITL state + the Tools registry exposing it to the agent."""

	def __init__(self, permissions_file: Path, channel: Channel | None = None, vault: Vault | None = None):
		from nkqa.ui import TerminalChannel

		self.permissions_file = permissions_file
		self.channel: Channel = channel or TerminalChannel()
		self.vault = vault
		self.scenario_id = ''  # set by the scenario runner, so grants can be scoped to it
		self.secrets: dict[str, str | dict[str, str]] = {}  # flat, or {origin: {name: value}}
		self.session_grants: set[str] = set()
		self.session_credentials: set[str] = set()  # "allow for this session" vault releases
		self.autonomy: Autonomy = 'ask'  # per session; `mode` sets it, nothing loads it

	# --- permission grants ---------------------------------------------------

	def _load_always_grants(self) -> set[str]:
		return load_grants(self.permissions_file).permissions

	def _save_always_grant(self, key: str) -> None:
		grants = load_grants(self.permissions_file)
		grants.permissions.add(key)
		save_grants(self.permissions_file, grants)

	def _denied(self, key: str, description: str, why: str = '') -> Decision:
		"""A refusal is not an error: the agent notes it and keeps testing everything else.

		Auto-refuse returns the same thing a human 'n' does, so the agent's behaviour after
		a denial is identical however the denial was reached.
		"""
		return Decision(
			False,
			f'Permission "{key}" DENIED. Do not perform this action.{why} '
			'Record it in your report as "not tested - permission denied" and continue with other tests.',
			f'Permission denied for: {description}',
		)

	# --- secret storage ------------------------------------------------------

	def has_secret(self, key: str) -> bool:
		if key in self.secrets:
			return True
		return any(isinstance(v, dict) and key in v for v in self.secrets.values())

	def store_secret(self, key: str, value: str, origin: str = '') -> None:
		"""Origin-bound values are stored under the origin, so browser-use itself refuses
		to substitute them on a page that does not match. Unbound ones stay flat."""
		if not origin:
			self.secrets[key] = value
			return
		bucket = self.secrets.setdefault(origin, {})
		if isinstance(bucket, dict):
			bucket[key] = value

	def forget(self) -> int:
		count = sum(len(v) if isinstance(v, dict) else 1 for v in self.secrets.values())
		self.secrets.clear()
		self.session_grants.clear()
		self.session_credentials.clear()
		return count

	async def collect_secret(self, key: str, prompt: str, origin: str = '') -> str:
		value = await self.channel.ask_secret(key, prompt, interrupt=True)
		if value:
			self.store_secret(key, value, origin)
		return value

	# --- the vault release chain --------------------------------------------

	async def _from_vault(self, key: str, page_url: str | None) -> tuple[str, Decision | None]:
		"""('' , refusal) when denied · (value, None) when released · ('', None) to fall through."""
		if self.vault is None:
			return '', None
		spec = self.vault.specs.get(key)
		if spec is None:
			return '', None  # not a declared credential: ask the human as before

		if not origin_allows(spec.origin, page_url):
			await self.channel.log(f'🚫 credential "{key}" is bound to {spec.origin}; refusing to release it here')
			return '', Decision(
				False,
				f'Credential "{key}" is bound to {spec.origin} and the browser is on {page_url}. '
				'It was NOT released. Do not try again from this page; report the step as '
				'"not tested - credential is bound to another origin".',
				f'Credential "{key}" refused: origin {page_url} does not match {spec.origin}.',
			)

		stored, source = self.vault.get(key)
		if not stored:
			return '', None  # declared but not set: ask the human, then offer to save

		if self.vault.granted(key, self.scenario_id) or key in self.session_credentials:
			self.store_secret(key, stored, spec.origin)
			await self.channel.log(f'🔐 using stored credential "{key}" ({source})')
			return stored, None

		choice = await self.channel.choose(
			f'🔐 The agent wants the stored credential "{key}"'
			+ (f' for scenario {self.scenario_id}' if self.scenario_id else '')
			+ f'.\n    {spec.description or "no description"} · bound to {spec.origin or "any origin"}\n'
			'[y] use once  [s] this session  [a] always  [n] deny: ',
			PERMISSION_CHOICES,
			key=key,
			interrupt=True,
		)
		choice = choice[:1]
		if choice == 'n' or not choice:
			return '', Decision(
				False,
				f'The human denied the stored credential "{key}". '
				'Skip this flow and note it as "not tested - credential denied".',
				f'Credential "{key}" denied by the human.',
			)
		if choice == 's':
			self.session_credentials.add(key)
		elif choice == 'a':
			self.vault.grant(key, self.scenario_id)
		self.store_secret(key, stored, spec.origin)
		return stored, None

	# --- the two gates ---------------------------------------------------------
	# Plain methods, so any driver can call them: browser-use's Agent through the actions in
	# build_tools(), an external agent through `qa mcp`. The words are the contract either way.

	async def release_credential(self, name: str, page_url: str = '') -> Decision:
		"""Make a credential available under its placeholder. The value never leaves this object."""
		key = normalize(name)
		if self.has_secret(key):
			return Decision(True, f'Already available. Type <secret>{key}</secret> into the field.')

		released, refusal = await self._from_vault(key, page_url)
		if refusal is not None:
			return refusal
		if released:
			return Decision(
				True,
				f'Ready. Type the literal text <secret>{key}</secret> into the field.',
				f'Credential "{key}" released from the vault; use <secret>{key}</secret>.',
			)

		spec = self.vault.specs.get(key) if self.vault else None
		value = await self.collect_secret(
			key, f'🔑 QA agent needs "{key}" to continue: ', origin=spec.origin if spec else ''
		)
		if not value:
			return Decision(False, f'Human provided no value for {key}. Skip this flow and note it as untestable.')
		offer_save = spec is not None and self.vault is not None and self.vault.writable
		if offer_save and await self.channel.confirm(f'💾 Save "{key}" to the vault for next time? [y/N]: '):
			assert self.vault is not None
			self.vault.set(key, value)
			await self.channel.log(f'🔐 stored "{key}" in the {self.vault.service} keychain entry')
		return Decision(
			True,
			f'Stored. To use it, type the literal text <secret>{key}</secret> into the field.',
			f'Credential "{key}" collected from human; usable as <secret>{key}</secret>.',
		)

	async def decide_permission(self, permission_key: str, description: str) -> Decision:
		key = permission_key.strip().lower()

		# Above the stored grants on purpose: "refuse everything right now" has to beat a
		# grant someone allowed-always weeks ago. The conservative answer wins.
		if self.autonomy == 'refuse':
			await self.channel.log(f'⛔ auto-refused permission "{key}" (autonomy: refuse)')
			return self._denied(key, description, ' Autonomy is set to refuse for this session.')

		if key in self._load_always_grants():
			return Decision(True, f'Permission "{key}" granted (previously allowed always).')
		if key in self.session_grants:
			return Decision(True, f'Permission "{key}" granted (allowed for this session).')

		# Below the two lookups, so an existing grant keeps its own more specific message
		# and this adds no behaviour delta for already-granted keys. Leaves no residue:
		# nothing is stored, so switching back to 'ask' resumes prompting immediately.
		if self.autonomy == 'allow':
			await self.channel.emit(
				Event('log', f'⚠️  auto-granted permission "{key}": {description}', {'permission': key, 'auto': 'allow'})
			)
			return Decision(
				True,
				f'Permission "{key}" granted automatically (session autonomy: allow).',
				# The channel log dies with the window; this is what reaches history.json,
				# so an unattended run can still be audited afterwards.
				f'Permission "{key}" auto-granted by session autonomy: {description}',
			)

		choice = await self.channel.choose(
			f'⚠️  QA agent requests permission: {description}\n'
			f'    key: {key}\n'
			f'[y] allow once  [s] allow this session  [a] allow always  [n] deny: ',
			PERMISSION_CHOICES,
			key=key,
			interrupt=True,
		)
		choice = choice[:1]
		if choice == 'a':
			self._save_always_grant(key)
			return Decision(True, f'Permission "{key}" granted permanently.')
		if choice == 's':
			self.session_grants.add(key)
			return Decision(True, f'Permission "{key}" granted for this session.')
		if choice == 'y':
			return Decision(True, f'Permission "{key}" granted once. Ask again next time.')
		return self._denied(key, description)

	def build_tools(self) -> Tools[None]:
		tools: Tools[None] = Tools()

		@tools.registry.action(
			'Ask the human developer a question. Use this whenever the task is ambiguous, '
			'you are blocked, or you need information you do not have. Never guess or invent data.'
		)
		async def ask_human(question: str) -> ActionResult:  # pyright: ignore[reportUnusedFunction]
			answer = await self.channel.ask_text(f'🤖 QA agent asks: {question}\nYour answer: ', interrupt=True)
			return ActionResult(
				extracted_content=f'The human answered: {answer}',
				long_term_memory=f'Asked human: "{question}" -> answer: "{answer}"',
			)

		@tools.registry.action(
			'Ask for a credential by name (e.g. name="username" or name="password") when you hit a '
			'login form. It may come from the vault or from the human. You will NOT see the value - '
			'after this, put the placeholder <secret>name</secret> into the input action text and the '
			'real value is filled in. You cannot list what credentials exist: ask for one you know of '
			'from the scenario or the app map.'
		)
		async def ask_credential(name: str, page_url: str = '') -> ActionResult:  # pyright: ignore[reportUnusedFunction]
			return (await self.release_credential(name, page_url)).action_result()

		@tools.registry.action(
			'MUST be called before any dangerous or irreversible action: deleting data, submitting real '
			'orders/payments, sending emails or messages, changing account settings, or anything affecting '
			'real users. Pass a short stable permission_key (e.g. "delete-test-user") and a one-line '
			'description of what you want to do and why. Only proceed if permission is granted.'
		)
		async def request_permission(permission_key: str, description: str) -> ActionResult:  # pyright: ignore[reportUnusedFunction]
			return (await self.decide_permission(permission_key, description)).action_result()

		return tools
