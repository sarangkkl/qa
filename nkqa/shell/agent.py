"""Free text -> a command call, using the cheap `chat` model role.

The agent's tools come from the registry minus human_only entries, so it structurally
cannot approve a scenario: approving stays a human keystroke.
"""

from typing import Any

from browser_use.llm.messages import AssistantMessage, BaseMessage, SystemMessage, UserMessage
from pydantic import BaseModel, Field

from nkqa import chats as chats_mod
from nkqa import scenarios as scenarios_mod
from nkqa.chats import Turn
from nkqa.models import resolve_llm
from nkqa.shell.commands import REGISTRY, Command, ShellContext, agent_commands
from nkqa.shell.render import paint

MAX_STEPS = 3

CHAT_SYSTEM = """\
You are the interactive front-end of a QA teammate CLI. Turn what the human says into
ONE command call at a time, or just answer them.

Rules:
- Only use the commands listed below, with their exact parameter names.
- If the request is ambiguous (which scenario? which run?), do NOT guess: leave command
  empty and ask in `reply`.
- You CANNOT approve scenarios. If asked, reply that approval is theirs and they should
  type `/approve <id>`.
- Running a scenario only works if it is already approved; if it is a draft or STALE,
  say so and point at /approve instead of running.
- Keep `reply` to one or two short sentences: what you are doing, or what you need.
- After a command runs you are asked whether anything else is needed. Usually the answer
  is no: leave command empty and suggest the natural next step in `reply`.
"""


class ChatDecision(BaseModel):
	reply: str = Field(description='what to tell the human, one or two short sentences')
	command: str = Field(default='', description="command name to run, or '' to just reply")
	args: dict[str, str] = Field(default_factory=dict, description='parameter name -> value, as strings')


def describe_commands() -> str:
	lines: list[str] = []
	for cmd in agent_commands():
		params = ', '.join(f'{p.name}{"*" if p.required else ""} ({p.type})' for p in cmd.params) or 'no parameters'
		lines.append(f'- {cmd.name}: {cmd.help} | params: {params}')
	return '\n'.join(lines)


def replay_history(ctx: ShellContext) -> list[BaseMessage]:
	"""Earlier turns, capped: a long chat must not become a long prompt every message."""
	if ctx.chat is None:
		return []
	messages: list[BaseMessage] = []
	for turn in ctx.chat.recent():
		if turn.role == 'user':
			messages.append(UserMessage(content=turn.text))
		elif turn.role == 'assistant':
			ran = f' (ran {turn.command} -> exit {turn.exit})' if turn.command else ''
			messages.append(AssistantMessage(content=turn.text + ran))
	return messages


def record(ctx: ShellContext, turn: Turn) -> None:
	if ctx.chat is None:
		return
	ctx.chat.add(turn)
	chats_mod.save(ctx.ws, ctx.chat)


def describe_state(ctx: ShellContext) -> str:
	scenarios = scenarios_mod.load_all(ctx.ws.scenarios_dir)
	if scenarios:
		listing = '\n'.join(f'- {s.id} [{s.runnable()}] {s.title}' for s in scenarios)
	else:
		listing = '(no scenarios yet)'
	runs = '\n'.join(f'- {name}' for name in ctx.last_runs[-5:]) or '(no runs listed yet)'
	return (
		f'App: {ctx.config.app_name} ({ctx.config.base_url or "no base_url set"})\n\n'
		f'Scenarios:\n{listing}\n\nRecent runs:\n{runs}'
	)


def coerce(cmd: Command, args: dict[str, str]) -> dict[str, Any]:
	by_name = {p.name: p for p in cmd.params}
	out: dict[str, Any] = {}
	for key, value in args.items():
		param = by_name.get(key)
		if param is None:
			continue
		if param.type == 'integer':
			out[key] = int(value) if str(value).strip().isdigit() else 0
		elif param.type == 'boolean':
			out[key] = str(value).strip().lower() in ('1', 'true', 'yes')
		else:
			out[key] = value
	return out


async def route(ctx: ShellContext, text: str) -> int:
	llm = resolve_llm(ctx.config, 'chat')
	if llm is None:
		await ctx.ch.log("Chat needs a model: set models.chat in config.yaml (e.g. 'fast'), or use /commands.")
		return 2

	history = replay_history(ctx)
	record(ctx, Turn(role='user', text=text))
	messages: list[BaseMessage] = [
		SystemMessage(content=f'{CHAT_SYSTEM}\nCommands you may call:\n{describe_commands()}'),
		*history,
		UserMessage(content=f'Current state:\n{describe_state(ctx)}\n\nThe human says: {text}'),
	]
	last_code = 0
	for _ in range(MAX_STEPS):
		decision = (await llm.ainvoke(messages, output_format=ChatDecision)).completion
		if decision.reply:
			await ctx.ch.log(paint(f'\n{decision.reply}\n', 'cyan'))
		name = decision.command.strip().lstrip('/')
		if not name:
			record(ctx, Turn(role='assistant', text=decision.reply))
			return last_code

		cmd = REGISTRY.get(name)
		if cmd is None or cmd.shell_only:
			await ctx.ch.log(f'(no such command "{name}" - type /help)')
			record(ctx, Turn(role='assistant', text=f'(no such command "{name}")'))
			return 2
		if cmd.human_only:
			target = decision.args.get('id', '<id>')
			refusal = f'Approving is yours to do: type  /approve {target}'
			await ctx.ch.log(paint(refusal, 'yellow'))
			record(ctx, Turn(role='assistant', text=refusal))
			return 1

		last_code = await cmd.handler(ctx, coerce(cmd, decision.args))
		record(ctx, Turn(role='assistant', text=decision.reply, command=name, args=decision.args, exit=last_code))
		messages.append(AssistantMessage(content=f'ran {name} -> exit {last_code}'))
		messages.append(
			UserMessage(content=f'`{name}` finished with exit code {last_code}. Anything else needed for the request?')
		)
	return last_code
