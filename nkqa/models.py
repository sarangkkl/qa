"""Role-based model resolution: role -> config entry -> alias -> browser-use LLM.

Two name forms:
  anthropic_claude_haiku_4_5   browser-use's own naming (underscores become dashes)
  openai:gpt-5.1-mini          provider:exact-id - passed through verbatim, so ids
                               containing dots or dates stay intact

Override precedence: CLI --model beats config; 'default'/empty means browser-use's own default.
"""

import os
import re

from browser_use.llm.base import BaseChatModel
from browser_use.llm.models import get_llm_by_name

from nkqa.config import Config

ROLES = ('planner', 'executor', 'reflector', 'chat', 'fallback')

# The two tiers every role points at by default: one model worth thinking with, one worth
# repeating. Settings edits these rather than the five roles, because the split is the whole
# reason the roles exist - collapsing them onto one model makes every run either slow or dim.
TIERS = ('smart', 'fast')

# What the settings page offers, newest first. Ids are passed to the provider verbatim (the
# `provider:id` form), so this list is presentation, not validation: a model shipped after this
# release is still reachable by typing its id, and nothing here has to be edited for that to work.
#
# Every id here was checked against GET /v1/models/{id}. That matters: the list this replaced
# offered `gpt-5.1-mini`, which the API 404s on, as OpenAI's *default* fast model - so switching
# provider pointed executor, reflector, chat and fallback at a model that does not exist. Being a
# catalogue and not a validator excuses a model that is missing from it, never one that is invented.
#
# Chat models only. The providers also serve image, audio, tts, realtime, transcribe, embedding
# and moderation models, plus `-codex` and `-chat-latest` variants and dated snapshots of what is
# already here; none of them are what an agent drives a browser with.
CATALOGUE: dict[str, list[dict[str, str]]] = {
	'anthropic': [
		{'id': 'claude-opus-5', 'label': 'Claude Opus 5', 'tier': 'smart'},
		{'id': 'claude-sonnet-5', 'label': 'Claude Sonnet 5', 'tier': 'smart'},
		{'id': 'claude-fable-5-1', 'label': 'Claude Fable 5.1', 'tier': 'smart'},
		{'id': 'claude-fable-5', 'label': 'Claude Fable 5', 'tier': 'smart'},
		{'id': 'claude-opus-4-8', 'label': 'Claude Opus 4.8', 'tier': 'smart'},
		{'id': 'claude-opus-4-7', 'label': 'Claude Opus 4.7', 'tier': 'smart'},
		{'id': 'claude-opus-4-6', 'label': 'Claude Opus 4.6', 'tier': 'smart'},
		{'id': 'claude-sonnet-4-6', 'label': 'Claude Sonnet 4.6', 'tier': 'smart'},
		{'id': 'claude-opus-4-5', 'label': 'Claude Opus 4.5', 'tier': 'smart'},
		{'id': 'claude-sonnet-4-5', 'label': 'Claude Sonnet 4.5', 'tier': 'smart'},
		{'id': 'claude-haiku-4-5', 'label': 'Claude Haiku 4.5', 'tier': 'fast'},
	],
	'openai': [
		{'id': 'gpt-6-astra', 'label': 'GPT-6 Astra', 'tier': 'smart'},
		{'id': 'gpt-5.6-luna', 'label': 'GPT-5.6 Luna', 'tier': 'smart'},
		{'id': 'gpt-5.6-sol', 'label': 'GPT-5.6 Sol', 'tier': 'smart'},
		{'id': 'gpt-5.6-terra', 'label': 'GPT-5.6 Terra', 'tier': 'smart'},
		{'id': 'gpt-5.5', 'label': 'GPT-5.5', 'tier': 'smart'},
		{'id': 'gpt-5.5-pro', 'label': 'GPT-5.5 Pro', 'tier': 'smart'},
		{'id': 'gpt-5.4', 'label': 'GPT-5.4', 'tier': 'smart'},
		{'id': 'gpt-5.4-pro', 'label': 'GPT-5.4 Pro', 'tier': 'smart'},
		{'id': 'gpt-5.4-mini', 'label': 'GPT-5.4 mini', 'tier': 'fast'},
		{'id': 'gpt-5.4-nano', 'label': 'GPT-5.4 nano', 'tier': 'fast'},
		{'id': 'gpt-5.2', 'label': 'GPT-5.2', 'tier': 'smart'},
		{'id': 'gpt-5.2-pro', 'label': 'GPT-5.2 Pro', 'tier': 'smart'},
		{'id': 'gpt-5.1', 'label': 'GPT-5.1', 'tier': 'smart'},
		{'id': 'gpt-5', 'label': 'GPT-5', 'tier': 'smart'},
		{'id': 'gpt-5-pro', 'label': 'GPT-5 Pro', 'tier': 'smart'},
		{'id': 'gpt-5-mini', 'label': 'GPT-5 mini', 'tier': 'fast'},
		{'id': 'gpt-5-nano', 'label': 'GPT-5 nano', 'tier': 'fast'},
		{'id': 'o4-mini', 'label': 'o4-mini', 'tier': 'fast'},
		{'id': 'o3', 'label': 'o3', 'tier': 'smart'},
		{'id': 'o3-mini', 'label': 'o3-mini', 'tier': 'fast'},
		{'id': 'o1', 'label': 'o1', 'tier': 'smart'},
		{'id': 'o1-pro', 'label': 'o1-pro', 'tier': 'smart'},
	],
}

# What choosing a provider alone means. Explicit rather than "the first entry of that tier",
# because that rule made the list's display order silently decide which model runs - and it was
# re-implemented a second time in the settings page, free to disagree with this one.
# Not the newest by name: gpt-6-astra and the gpt-5.6 variants lead the dropdown, but a default
# is what runs when nobody chose, so it goes to the newest plain-numbered pair instead.
DEFAULTS: dict[str, dict[str, str]] = {
	'anthropic': {'smart': 'claude-opus-5', 'fast': 'claude-haiku-4-5'},
	'openai': {'smart': 'gpt-5.5', 'fast': 'gpt-5.4-mini'},
}

PROVIDER_LABELS = {'anthropic': 'Anthropic', 'openai': 'OpenAI'}

# Legal in a `provider:id` value. Deliberately strict: this string is written into config.yaml
# and read back by every surface, so a newline or a quote in it is a corrupted workspace.
_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')


def default_tier_models(provider: str) -> dict[str, str]:
	"""The provider's own smart/fast picks - what choosing a provider alone means."""
	return dict(DEFAULTS[provider])


def qualify(provider: str, model: str) -> str:
	"""('openai', 'gpt-5.1') -> 'openai:gpt-5.1', validated. Accepts an already-qualified id."""
	if ':' in model:
		provider, _, model = model.partition(':')
	provider, model = provider.strip().lower(), model.strip()
	if provider not in CATALOGUE:
		known = ', '.join(sorted(CATALOGUE))
		raise ValueError(f'Unknown provider "{provider}". Use one of: {known}')
	if not _ID.match(model):
		raise ValueError(f'"{model}" is not a usable model id (letters, digits, dot, dash, underscore)')
	return f'{provider}:{model}'


def split_model(name: str) -> tuple[str, str]:
	"""A stored alias -> (provider, model id), for either name form. Display only."""
	if ':' in name:
		provider, _, model = name.partition(':')
		return provider, model
	provider, _, rest = name.partition('_')  # browser-use naming: underscores become dashes
	return provider, rest.replace('_', '-')


def model_name(config: Config, role: str, override: str | None = None) -> str | None:
	name = override or config.models.get(role, '')
	if not name or name == 'default':
		return None
	return config.aliases.get(name, name)


# Neither provider client sets a request timeout by default, and both retry hard (OpenAI 5
# times, Anthropic 10). A slow or unreachable model therefore hangs with no ceiling at all -
# which in the app looks exactly like "working…" forever and no way to tell what is wrong.
# Generous, because a reasoning model on a large prompt is legitimately slow; finite, because
# unbounded is never the right answer when a human is waiting.
REQUEST_TIMEOUT = 120.0


def build_exact(name: str) -> BaseChatModel:
	"""'openai:gpt-5.1' -> that exact model id, bypassing underscore mangling."""
	provider, _, model_id = name.partition(':')
	if not model_id:
		raise ValueError(f'Model "{name}" is missing an id after the colon (e.g. openai:gpt-5.1)')
	from browser_use.llm import ChatAnthropic, ChatAzureOpenAI, ChatGoogle, ChatOpenAI

	if provider == 'openai':
		return ChatOpenAI(model=model_id, api_key=os.getenv('OPENAI_API_KEY'), timeout=REQUEST_TIMEOUT)
	if provider == 'anthropic':
		return ChatAnthropic(model=model_id, api_key=os.getenv('ANTHROPIC_API_KEY'), timeout=REQUEST_TIMEOUT)
	if provider == 'google':
		return ChatGoogle(model=model_id, api_key=os.getenv('GOOGLE_API_KEY'))
	if provider == 'azure':
		return ChatAzureOpenAI(
			model=model_id,
			api_key=os.getenv('AZURE_OPENAI_KEY') or os.getenv('AZURE_OPENAI_API_KEY'),
			azure_endpoint=os.getenv('AZURE_OPENAI_ENDPOINT'),
			timeout=REQUEST_TIMEOUT,
		)
	known = 'openai, anthropic, google, azure'
	raise ValueError(f'Unknown provider "{provider}" in "{name}". Use one of: {known} (or browser-use naming).')


def resolve_llm(config: Config, role: str, override: str | None = None) -> BaseChatModel | None:
	name = model_name(config, role, override)
	if not name:
		return None
	return build_exact(name) if ':' in name else get_llm_by_name(name)


# env keys each provider prefix needs (verified against browser_use/llm/models.py)
PROVIDER_KEYS = {
	'anthropic': ['ANTHROPIC_API_KEY'],
	'openai': ['OPENAI_API_KEY'],
	'google': ['GOOGLE_API_KEY'],
	'mistral': ['MISTRAL_API_KEY'],
	'codestral': ['MISTRAL_API_KEY'],
	'pixtral': ['MISTRAL_API_KEY'],
	'cerebras': ['CEREBRAS_API_KEY'],
	'azure': ['AZURE_OPENAI_KEY', 'AZURE_OPENAI_ENDPOINT'],
	'bu': ['BROWSER_USE_API_KEY'],
}


def describe_role(config: Config, role: str) -> tuple[str, str, list[str], list[str]]:
	"""(resolved model name, provider, required env keys, missing env keys) for one role."""
	name = model_name(config, role)
	if name is None:
		provider, keys = 'browser-use default', PROVIDER_KEYS['bu']
	else:
		provider = name.split(':', 1)[0] if ':' in name else name.split('_', 1)[0]
		keys = PROVIDER_KEYS.get(provider, [])
	missing = [k for k in keys if not os.environ.get(k)]
	return name or 'default', provider, keys, missing
