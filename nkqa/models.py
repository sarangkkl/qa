"""Role-based model resolution: role -> config entry -> alias -> browser-use LLM.

Two name forms:
  anthropic_claude_haiku_4_5   browser-use's own naming (underscores become dashes)
  openai:gpt-5.1-mini          provider:exact-id - passed through verbatim, so ids
                               containing dots or dates stay intact

Override precedence: CLI --model beats config; 'default'/empty means browser-use's own default.
"""

import os

from browser_use.llm.base import BaseChatModel
from browser_use.llm.models import get_llm_by_name

from nkqa.config import Config

ROLES = ('planner', 'executor', 'reflector', 'chat', 'fallback')


def model_name(config: Config, role: str, override: str | None = None) -> str | None:
	name = override or config.models.get(role, '')
	if not name or name == 'default':
		return None
	return config.aliases.get(name, name)


def build_exact(name: str) -> BaseChatModel:
	"""'openai:gpt-5.1' -> that exact model id, bypassing underscore mangling."""
	provider, _, model_id = name.partition(':')
	if not model_id:
		raise ValueError(f'Model "{name}" is missing an id after the colon (e.g. openai:gpt-5.1)')
	from browser_use.llm import ChatAnthropic, ChatAzureOpenAI, ChatGoogle, ChatOpenAI

	if provider == 'openai':
		return ChatOpenAI(model=model_id, api_key=os.getenv('OPENAI_API_KEY'))
	if provider == 'anthropic':
		return ChatAnthropic(model=model_id, api_key=os.getenv('ANTHROPIC_API_KEY'))
	if provider == 'google':
		return ChatGoogle(model=model_id, api_key=os.getenv('GOOGLE_API_KEY'))
	if provider == 'azure':
		return ChatAzureOpenAI(
			model=model_id,
			api_key=os.getenv('AZURE_OPENAI_KEY') or os.getenv('AZURE_OPENAI_API_KEY'),
			azure_endpoint=os.getenv('AZURE_OPENAI_ENDPOINT'),
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
