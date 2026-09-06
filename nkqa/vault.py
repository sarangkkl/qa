"""Credential vault: store a credential once, release it only under a human grant.

Values live in the OS keychain, never in the workspace - the workspace is git-tracked and
meant to be shared. What the workspace *does* carry is `vault.yaml`: the names, what they
are for, and which origin each is bound to. Clone the repo, run `qa vault status`, and you
are told exactly what you must supply before anything can run.

Two independent gates stop a credential reaching the wrong page:

1. **Release** (here): the value is not even read out of the keychain when the browser's
   current origin does not match the spec's `origin`.
2. **Substitution** (browser-use's own): an origin-bound value is held as
   `secrets[origin][name]`, and browser-use only substitutes `<secret>name</secret>` on a
   page matching that origin. So even a value already in memory cannot be typed elsewhere.

Neither gate ever shows the value to the model: the `<secret>` placeholder contract is
untouched.
"""

import contextlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

import yaml
from browser_use.utils import match_url_with_domain_pattern

from nkqa.workspace import Workspace

SERVICE_PREFIX = 'nkqa'
ENV_PREFIX = 'NKQA_SECRET_'

VAULT_TEMPLATE = """\
# What credentials this project needs. Names and origins only - never values.
# Values live in your OS keychain (`qa vault set <name>`) or, for CI, in
# NKQA_SECRET_<NAME> environment variables.
#
# `origin` binds a credential to one site: the agent cannot pull it out of the keychain
# while the browser is anywhere else, and browser-use will not type it on another page.
# Subdomain globs are fine; a bare '*' is refused.
credentials: {}
#   qa_user:
#     description: QA account email
#     origin: https://dev.example.com
#   qa_password:
#     description: password for qa_user
#     origin: https://*.example.com
"""


@dataclass
class CredentialSpec:
	name: str
	description: str = ''
	origin: str = ''


@dataclass
class Grants:
	"""What the human has allowed. Never contains a credential value."""

	permissions: set[str] = field(default_factory=set[str])
	credentials: dict[str, list[str]] = field(default_factory=dict[str, list[str]])  # name -> scenario ids, [] = any

	def allows(self, name: str, scenario_id: str = '') -> bool:
		scoped = self.credentials.get(name)
		if scoped is None:
			return False
		return not scoped or scenario_id in scoped


def load_grants(path: Path) -> Grants:
	"""Tolerates the pre-vault format, which was a bare list of permission keys."""
	try:
		raw: Any = json.loads(path.read_text())
	except (FileNotFoundError, json.JSONDecodeError):
		return Grants()
	if isinstance(raw, list):
		legacy = cast(list[Any], raw)
		return Grants(permissions={str(k) for k in legacy})
	data = cast(dict[str, Any], raw) if isinstance(raw, dict) else {}
	raw_credentials: dict[str, Any] = data.get('credentials') or {}
	credentials: dict[str, list[str]] = {}
	for name, raw_entry in raw_credentials.items():
		entry = cast(dict[str, Any], raw_entry) if isinstance(raw_entry, dict) else {}
		scenarios: list[Any] = entry.get('scenarios') or []
		credentials[str(name)] = [str(item) for item in scenarios]
	raw_permissions: list[Any] = data.get('permissions') or []
	return Grants(permissions={str(k) for k in raw_permissions}, credentials=credentials)


def save_grants(path: Path, grants: Grants) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(
		json.dumps(
			{
				'permissions': sorted(grants.permissions),
				'credentials': {name: {'scenarios': sorted(s)} for name, s in sorted(grants.credentials.items())},
			},
			indent=1,
		)
	)


def normalize(name: str) -> str:
	return name.strip().lower().replace(' ', '_').replace('-', '_')


def load_specs(vault_file: Path) -> dict[str, CredentialSpec]:
	if not vault_file.is_file():
		return {}
	loaded: Any = yaml.safe_load(vault_file.read_text(encoding='utf-8')) or {}
	data = cast(dict[str, Any], loaded) if isinstance(loaded, dict) else {}
	raw_credentials: dict[str, Any] = data.get('credentials') or {}
	specs: dict[str, CredentialSpec] = {}
	for raw_name, raw_entry in raw_credentials.items():
		entry = cast(dict[str, Any], raw_entry) if isinstance(raw_entry, dict) else {}
		origin = str(entry.get('origin') or '').strip()
		if origin == '*':
			raise ValueError(f'vault.yaml: credential "{raw_name}" has origin "*" - bind it to a real site or omit it')
		key = normalize(str(raw_name))
		specs[key] = CredentialSpec(name=key, description=str(entry.get('description') or ''), origin=origin)
	return specs


def origin_allows(origin: str, page_url: str | None) -> bool:
	"""No origin declared, or no page to check against, means this gate does not apply."""
	if not origin or not page_url:
		return True
	try:
		return bool(match_url_with_domain_pattern(page_url, origin))
	except Exception:
		return False


class Backend(Protocol):
	label: str
	writable: bool

	def get(self, service: str, key: str) -> str | None: ...
	def set(self, service: str, key: str, value: str) -> None: ...
	def delete(self, service: str, key: str) -> None: ...


class EnvBackend:
	"""NKQA_SECRET_<NAME>. The CI path, and the fallback where there is no keychain."""

	label = 'environment'
	writable = False

	def get(self, service: str, key: str) -> str | None:
		return os.environ.get(ENV_PREFIX + key.upper()) or None

	def set(self, service: str, key: str, value: str) -> None:
		raise RuntimeError(f'set {ENV_PREFIX}{key.upper()} in the environment instead')

	def delete(self, service: str, key: str) -> None:
		raise RuntimeError(f'unset {ENV_PREFIX}{key.upper()} in the environment instead')


class KeyringBackend:
	"""macOS Keychain / Windows Credential Manager / libsecret, via the keyring package."""

	label = 'OS keychain'
	writable = True

	def __init__(self) -> None:
		import keyring

		self.keyring = keyring
		# Probe with a real read. keyring.get_keyring() succeeds even where there is no
		# usable backend - it hands back a "fail" backend that raises on first use - so only
		# an actual call tells the truth. Without this a headless box crashes mid-run instead
		# of quietly falling back to the environment.
		self.keyring.get_password(f'{SERVICE_PREFIX}:probe', 'probe')

	def get(self, service: str, key: str) -> str | None:
		return self.keyring.get_password(service, key)

	def set(self, service: str, key: str, value: str) -> None:
		self.keyring.set_password(service, key, value)

	def delete(self, service: str, key: str) -> None:
		with contextlib.suppress(Exception):  # already gone is success
			self.keyring.delete_password(service, key)


def open_keychain() -> Backend | None:
	"""None on a machine with no usable keychain - CI, a bare container, some Linux boxes."""
	try:
		return KeyringBackend()
	except Exception:
		return None


class Vault:
	def __init__(
		self, ws: Workspace, keychain: Backend | None = None, env: Backend | None = None, discover: bool = True
	):
		"""`discover=False` means "this machine has no keychain" - the CI shape, and testable."""
		self.ws = ws
		self.specs = load_specs(ws.vault_file)
		self.env: Backend = env or EnvBackend()
		self.keychain = keychain or (open_keychain() if discover else None)

	@property
	def service(self) -> str:
		return f'{SERVICE_PREFIX}:{self.ws.identity()}'

	@property
	def writable(self) -> bool:
		return self.keychain is not None

	def get(self, name: str) -> tuple[str | None, str]:
		"""(value, where it came from). The environment wins, so CI can override a keychain."""
		key = normalize(name)
		value = self.env.get(self.service, key)
		if value:
			return value, self.env.label
		if self.keychain is not None:
			stored = self.keychain.get(self.service, key)
			if stored:
				return stored, self.keychain.label
		return None, ''

	def set(self, name: str, value: str) -> None:
		if self.keychain is None:
			raise RuntimeError(
				f'No OS keychain available on this machine. Set {ENV_PREFIX}{normalize(name).upper()} instead.'
			)
		self.keychain.set(self.service, normalize(name), value)

	def delete(self, name: str) -> None:
		if self.keychain is not None:
			self.keychain.delete(self.service, normalize(name))

	# --- grants -------------------------------------------------------------

	def grants(self) -> Grants:
		return load_grants(self.ws.permissions_file)

	def granted(self, name: str, scenario_id: str = '') -> bool:
		return self.grants().allows(normalize(name), scenario_id)

	def grant(self, name: str, scenario_id: str = '') -> None:
		grants = self.grants()
		key = normalize(name)
		existing = grants.credentials.get(key, [])
		grants.credentials[key] = (
			sorted({*existing, scenario_id}) if scenario_id and existing else ([scenario_id] if scenario_id else [])
		)
		save_grants(self.ws.permissions_file, grants)

	def revoke(self, name: str) -> bool:
		grants = self.grants()
		if grants.credentials.pop(normalize(name), None) is None:
			return False
		save_grants(self.ws.permissions_file, grants)
		return True

	# --- reporting ----------------------------------------------------------

	def status(self) -> list[dict[str, str]]:
		"""One row per declared credential: is it set, where from, and is it granted."""
		grants = self.grants()
		rows: list[dict[str, str]] = []
		for key, spec in sorted(self.specs.items()):
			value, source = self.get(key)
			scoped = grants.credentials.get(key)
			rows.append(
				{
					'name': key,
					'description': spec.description,
					'origin': spec.origin or '(any - unbound)',
					'stored': source if value else '',
					'grant': '' if scoped is None else (', '.join(scoped) if scoped else 'always'),
				}
			)
		return rows
