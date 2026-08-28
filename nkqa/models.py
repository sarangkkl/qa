"""Role-based model resolution: role -> config entry -> alias -> browser-use LLM.

Override precedence: CLI --model beats config; 'default'/empty means browser-use's own default.
"""

from browser_use.llm.base import BaseChatModel
from browser_use.llm.models import get_llm_by_name

from nkqa.config import Config

ROLES = ('planner', 'executor', 'reflector', 'chat', 'fallback')


def model_name(config: Config, role: str, override: str | None = None) -> str | None:
	name = override or config.models.get(role, '')
	if not name or name == 'default':
		return None
	return config.aliases.get(name, name)


def resolve_llm(config: Config, role: str, override: str | None = None) -> BaseChatModel | None:
	name = model_name(config, role, override)
	return get_llm_by_name(name) if name else None


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
	import os

	name = model_name(config, role)
	if name is None:
		provider, keys = 'browser-use default', PROVIDER_KEYS['bu']
	else:
		provider = name.split('_', 1)[0]
		keys = PROVIDER_KEYS.get(provider, [])
	missing = [k for k in keys if not os.environ.get(k)]
	return name or 'default', provider, keys, missing
