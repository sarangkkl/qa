"""Role-based model resolution: role -> config entry -> alias -> browser-use LLM.

Override precedence: CLI --model beats config; 'default'/empty means browser-use's own default.
"""

from browser_use.llm.base import BaseChatModel
from browser_use.llm.models import get_llm_by_name

from nkqa.config import Config

ROLES = ('planner', 'executor', 'reflector', 'fallback')


def model_name(config: Config, role: str, override: str | None = None) -> str | None:
	name = override or config.models.get(role, '')
	if not name or name == 'default':
		return None
	return config.aliases.get(name, name)


def resolve_llm(config: Config, role: str, override: str | None = None) -> BaseChatModel | None:
	name = model_name(config, role, override)
	return get_llm_by_name(name) if name else None
