"""The vault's gates. A credential is released only under a grant, and only on its origin."""

# browser-use boundary: its tool registry is partially untyped.
# pyright: reportUnknownMemberType=false

import asyncio
import json
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, cast

import pytest
from browser_use import Tools
from conftest import FakeChannel

from nkqa import workspace as workspace_mod
from nkqa.hitl import HumanInTheLoop
from nkqa.vault import ENV_PREFIX, Grants, Vault, load_grants, load_specs, origin_allows, save_grants
from nkqa.workspace import Workspace

SITE = 'https://dev.example.com'
VAULT_YAML = f"""\
credentials:
  qa_user:
    description: QA account email
    origin: {SITE}
  qa_password:
    description: password for qa_user
    origin: https://*.example.com
  unbound_token:
    description: no origin on purpose
"""


class FakeKeychain:
	label = 'fake keychain'
	writable = True

	def __init__(self, seed: dict[str, str] | None = None):
		self.store: dict[str, str] = dict(seed or {})

	def get(self, service: str, key: str) -> str | None:
		return self.store.get(f'{service}/{key}')

	def set(self, service: str, key: str, value: str) -> None:
		self.store[f'{service}/{key}'] = value

	def delete(self, service: str, key: str) -> None:
		self.store.pop(f'{service}/{key}', None)


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
	workspace = workspace_mod.create(tmp_path)
	workspace.vault_file.write_text(VAULT_YAML)
	return workspace


def make(ws: Workspace, answers: list[str], stored: dict[str, str] | None = None) -> tuple[HumanInTheLoop, FakeChannel]:
	keychain = FakeKeychain()
	vault = Vault(ws, keychain=keychain)
	for key, value in (stored or {}).items():
		vault.set(key, value)
	ch = FakeChannel(answers)
	return HumanInTheLoop(ws.permissions_file, ch, vault), ch


def call(tools: Tools[None], action: str, **kwargs: object) -> str:
	fn = cast(Callable[..., Coroutine[Any, Any, object]], tools.registry.registry.actions[action].function)
	return str(asyncio.run(fn(**kwargs)))


# --- spec parsing -----------------------------------------------------------


def test_specs_parse_and_normalize(ws: Workspace) -> None:
	specs = load_specs(ws.vault_file)
	assert set(specs) == {'qa_user', 'qa_password', 'unbound_token'}
	assert specs['qa_user'].origin == SITE
	assert specs['unbound_token'].origin == ''


def test_bare_wildcard_origin_is_refused(ws: Workspace) -> None:
	ws.vault_file.write_text("credentials:\n  anything:\n    origin: '*'\n")
	with pytest.raises(ValueError, match='bind it to a real site'):
		load_specs(ws.vault_file)


def test_origin_matching(ws: Workspace) -> None:
	assert origin_allows(SITE, f'{SITE}/login') is True
	assert origin_allows(SITE, 'https://evil.example/login') is False
	assert origin_allows('https://*.example.com', 'https://a.example.com/x') is True
	assert origin_allows('https://*.example.com', 'http://a.example.com/x') is False  # no scheme downgrade
	assert origin_allows('', 'https://anywhere.test') is True  # unbound
	assert origin_allows(SITE, None) is True  # nothing to check against


# --- grants persist without ever holding a value ----------------------------


def test_grants_round_trip_and_tolerate_the_legacy_format(tmp_path: Path) -> None:
	path = tmp_path / 'perms.json'
	path.write_text(json.dumps(['delete-user']))  # pre-vault workspaces stored a bare list
	grants = load_grants(path)
	assert grants.permissions == {'delete-user'} and grants.credentials == {}

	grants.credentials['qa_password'] = ['auth/login']
	save_grants(path, grants)
	again = load_grants(path)
	assert again.permissions == {'delete-user'}
	assert again.allows('qa_password', 'auth/login') is True
	assert again.allows('qa_password', 'other/flow') is False
	assert again.allows('qa_user') is False


def test_an_unscoped_grant_covers_every_scenario() -> None:
	grants = Grants(credentials={'qa_user': []})
	assert grants.allows('qa_user', 'anything') is True


def test_the_environment_overrides_the_keychain(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
	vault = Vault(ws, keychain=FakeKeychain())
	vault.set('qa_user', 'from-keychain')
	assert vault.get('qa_user') == ('from-keychain', 'fake keychain')

	monkeypatch.setenv(f'{ENV_PREFIX}QA_USER', 'from-ci')
	assert vault.get('qa_user') == ('from-ci', 'environment')  # CI wins


def test_two_clones_do_not_share_entries(tmp_path: Path) -> None:
	one = workspace_mod.create(tmp_path / 'a')
	two = workspace_mod.create(tmp_path / 'b')
	assert one.identity() != two.identity()
	assert one.identity() == one.identity()  # stable across calls


# --- the release chain ------------------------------------------------------


def test_origin_mismatch_refuses_before_reading_the_keychain(ws: Workspace) -> None:
	hitl, ch = make(ws, answers=[], stored={'qa_user': 'qa@example.com'})
	result = call(hitl.build_tools(), 'ask_credential', name='qa_user', page_url='https://evil.example/login')

	assert 'NOT released' in result
	assert ch.asks == []  # refused without even asking the human
	assert not hitl.has_secret('qa_user')
	assert 'qa@example.com' not in json.dumps([e.__dict__ for e in ch.events])


def test_granted_credential_is_released_without_a_prompt(ws: Workspace) -> None:
	hitl, ch = make(ws, answers=[], stored={'qa_user': 'qa@example.com'})
	hitl.vault.grant('qa_user')  # pyright: ignore[reportOptionalMemberAccess]
	result = call(hitl.build_tools(), 'ask_credential', name='qa_user', page_url=f'{SITE}/login')

	assert '<secret>qa_user</secret>' in result
	assert ch.asks == []
	assert hitl.secrets == {SITE: {'qa_user': 'qa@example.com'}}  # origin-scoped for browser-use
	assert 'qa@example.com' not in ch.out  # the name is logged, never the value


def test_ungranted_credential_asks_and_deny_withholds_it(ws: Workspace) -> None:
	hitl, ch = make(ws, answers=['n'], stored={'qa_user': 'qa@example.com'})
	result = call(hitl.build_tools(), 'ask_credential', name='qa_user', page_url=f'{SITE}/login')

	assert 'denied' in result.lower()
	assert ch.asks[0].kind == 'choice' and ch.asks[0].options == ['y', 's', 'a', 'n']
	assert not hitl.has_secret('qa_user')


def test_allow_once_releases_but_stores_no_grant(ws: Workspace) -> None:
	hitl, _ = make(ws, answers=['y'], stored={'qa_user': 'qa@example.com'})
	call(hitl.build_tools(), 'ask_credential', name='qa_user', page_url=f'{SITE}/login')

	assert hitl.has_secret('qa_user')
	assert load_grants(ws.permissions_file).credentials == {}  # nothing persisted


def test_allow_always_persists_a_grant_scoped_to_the_scenario(ws: Workspace) -> None:
	hitl, _ = make(ws, answers=['a'], stored={'qa_user': 'qa@example.com'})
	hitl.scenario_id = 'auth/login'
	call(hitl.build_tools(), 'ask_credential', name='qa_user', page_url=f'{SITE}/login')

	grants = load_grants(ws.permissions_file)
	assert grants.allows('qa_user', 'auth/login') is True
	assert grants.allows('qa_user', 'checkout/coupon') is False  # scoped, not blanket


def test_revoking_takes_effect_on_the_next_request(ws: Workspace) -> None:
	hitl, _ = make(ws, answers=['a'], stored={'qa_user': 'qa@example.com'})
	call(hitl.build_tools(), 'ask_credential', name='qa_user', page_url=f'{SITE}/login')
	assert hitl.vault.granted('qa_user') is True  # pyright: ignore[reportOptionalMemberAccess]

	assert hitl.vault.revoke('qa_user') is True  # pyright: ignore[reportOptionalMemberAccess]
	hitl.forget()
	hitl.channel = FakeChannel(['n'])  # asked again, and this time denied
	result = call(hitl.build_tools(), 'ask_credential', name='qa_user', page_url=f'{SITE}/login')
	assert 'denied' in result.lower()


def test_undeclared_credential_still_just_asks_the_human(ws: Workspace) -> None:
	"""The pre-vault behaviour has to keep working for anything vault.yaml does not mention."""
	hitl, ch = make(ws, answers=['typed-by-hand'])
	result = call(hitl.build_tools(), 'ask_credential', name='some_other_thing')

	assert '<secret>some_other_thing</secret>' in result
	assert hitl.secrets == {'some_other_thing': 'typed-by-hand'}  # flat: no origin declared
	assert 'typed-by-hand' not in ch.out


def test_a_declared_but_unset_credential_is_offered_for_saving(ws: Workspace) -> None:
	hitl, ch = make(ws, answers=['s3cret', 'y'])  # the value, then "save it?"
	call(hitl.build_tools(), 'ask_credential', name='qa_password', page_url='https://a.example.com/login')

	assert hitl.vault.get('qa_password') == ('s3cret', 'fake keychain')  # pyright: ignore[reportOptionalMemberAccess]
	assert 's3cret' not in ch.out
	assert 's3cret' not in json.dumps([a.__dict__ for a in ch.asks])


def test_declining_to_save_keeps_it_for_the_session_only(ws: Workspace) -> None:
	hitl, _ = make(ws, answers=['s3cret', 'n'])
	call(hitl.build_tools(), 'ask_credential', name='qa_password', page_url='https://a.example.com/login')

	assert hitl.has_secret('qa_password')
	assert hitl.vault.get('qa_password') == (None, '')  # pyright: ignore[reportOptionalMemberAccess]


def test_a_value_already_in_the_session_is_reused(ws: Workspace) -> None:
	hitl, ch = make(ws, answers=[])  # no scripted answers: any ask would raise
	hitl.store_secret('qa_user', 'qa@example.com', SITE)
	result = call(hitl.build_tools(), 'ask_credential', name='qa_user', page_url=f'{SITE}/login')
	assert 'Already available' in result and ch.asks == []


def test_forget_clears_scoped_and_flat_secrets(ws: Workspace) -> None:
	hitl, _ = make(ws, answers=[])
	hitl.store_secret('qa_user', 'a', SITE)
	hitl.store_secret('qa_password', 'b', SITE)
	hitl.store_secret('loose', 'c')
	hitl.session_credentials.add('qa_user')

	assert hitl.forget() == 3
	assert hitl.secrets == {} and hitl.session_credentials == set()


# --- status reporting -------------------------------------------------------


def test_status_says_what_a_fresh_clone_must_supply(ws: Workspace) -> None:
	vault = Vault(ws, keychain=FakeKeychain())
	vault.set('qa_user', 'qa@example.com')
	vault.grant('qa_user', 'auth/login')

	rows = {row['name']: row for row in vault.status()}
	assert rows['qa_user']['stored'] == 'fake keychain' and rows['qa_user']['grant'] == 'auth/login'
	assert rows['qa_password']['stored'] == ''  # not set: what a new engineer must provide
	assert rows['unbound_token']['origin'] == '(any - unbound)'


def test_no_keychain_means_read_only_and_a_clear_error(ws: Workspace) -> None:
	vault = Vault(ws, discover=False)
	assert vault.writable is False
	with pytest.raises(RuntimeError, match='NKQA_SECRET_QA_USER'):
		vault.set('qa_user', 'x')
