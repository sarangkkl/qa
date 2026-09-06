"""Free text -> a command call, using the cheap `chat` model role.

The agent's tools come from the registry minus human_only entries, so it structurally
cannot approve a scenario: approving stays a human keystroke.
"""

import asyncio
from typing import Any

from browser_use.llm.messages import AssistantMessage, BaseMessage, SystemMessage, UserMessage
from pydantic import BaseModel, Field

from nkqa import appmap
from nkqa import chats as chats_mod
from nkqa import scenarios as scenarios_mod
from nkqa.chats import Turn
from nkqa.models import model_name, resolve_llm
from nkqa.shell.commands import REGISTRY, Command, ShellContext, agent_commands
from nkqa.shell.render import paint
from nkqa.ui import Ask, Channel, Event

MAX_STEPS = 3
# A ceiling on one routing call. The client timeout in models.py bounds the HTTP request;
# this bounds the whole thing including the provider's own retries, which are 5 deep on
# OpenAI and 10 on Anthropic and would otherwise multiply straight through it.
CHAT_TIMEOUT = 90.0

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

Answering questions about the app:
- The app map below is what you know. Answer questions about screens, routes, roles and
  flows from it directly - leave `command` empty and put the answer in `reply`. For this
  kind of answer the two-sentence limit does not apply; be as detailed as the map allows.
- Say when the map does not cover something instead of guessing. "The map does not say"
  is a useful answer; an invented one is a bug that ends up in a test.
- Never state a route, field or behaviour that is not in the map. If a file is listed as
  not loaded, say it exists and that you have not read it.
- When the human tells you something about the app that the map gets wrong or omits, call
  `correct` with their correction as `instruction`. Pass on what THEY said - never your
  own guesses, and never a correction they did not make.

Jira tickets, when a jira connector is listed under Connectors below:
- A ticket key (PROJ-123) or an Atlassian URL means READ IT FIRST: call `ticket` with what
  they gave you, url and all. Never say you cannot reach Jira when jira is listed - you can,
  and saying otherwise sends them off to fix something that is not broken.
- You are shown what `ticket` returned. Then think like a QA engineer, in `reply`:
  what the ticket asks for, which screens and flows in the app map it touches, and what the
  map does not cover.
- Then ASK about what is genuinely ambiguous - acceptance criteria that cannot be checked as
  written, roles or permissions the ticket assumes, test data it would need, an edge case it
  is silent on. One or two sharp questions, not a checklist, and stop there.
- Draft scenarios with `plan` (ticket=KEY) only once they have answered or said go ahead. Put
  what they told you into `ask` so it reaches the drafts. Do not plan on the first message.
"""


class ChatArg(BaseModel):
	name: str = Field(description='parameter name, exactly as listed for that command')
	value: str = Field(description='the value, as a string')


class ChatDecision(BaseModel):
	reply: str = Field(description='what to tell the human, one or two short sentences')
	command: str = Field(default='', description="command name to run, or '' to just reply")
	# A list of pairs and not a dict, which is the obvious shape and does not work. OpenAI's
	# structured outputs run in strict mode (browser-use hardcodes it), and strict mode rejects
	# any object that does not declare its properties - so `dict[str, str]` reaches the API as
	# an object with no properties and no required, and every chat message 400s. A list of
	# declared objects is expressible everywhere; `args_of` puts the mapping back together.
	args: list[ChatArg] = Field(default_factory=list[ChatArg], description='the command arguments')


def args_of(decision: ChatDecision) -> dict[str, str]:
	return {a.name: a.value for a in decision.args if a.name}


def describe_commands() -> str:
	lines: list[str] = []
	for cmd in agent_commands():
		# Param.help too. Without it `plan`'s ticket param reached the model as a bare
		# `ticket (string)` - nothing said it takes a Jira key, so it was never used.
		params = (
			', '.join(f'{p.name}{"*" if p.required else ""} ({p.type}: {p.help})' for p in cmd.params)
			or 'no parameters'
		)
		lines.append(f'- {cmd.name}: {cmd.help} | params: {params}')
	return '\n'.join(lines)


def describe_connectors(ctx: ShellContext) -> str:
	"""What the agent can reach beyond the browser. Absent, it assumed it could reach nothing."""
	servers = ctx.config.mcp_servers
	if not servers:
		return '(none configured - no Jira. Setting one up is theirs to do: /connect jira)'
	lines = [f'- {s.name}' for s in servers]
	if ctx.config.mcp_server('jira') is not None and not ctx.config.jira_project:
		lines.append('  jira has no default project key, so filing a bug needs one passed explicitly')
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


class Recorder(Channel):
	"""Passes everything through to the real channel and keeps a copy of the text.

	A tee, not a buffer: the human still sees the output live. It exists because `route()`
	otherwise tells the model only that a command ran and its exit code, so the agent could
	fetch a ticket and then be unable to say a word about what was in it.
	"""

	def __init__(self, inner: Channel):
		self.inner = inner
		self.lines: list[str] = []

	async def emit(self, event: Event) -> None:
		if event.kind != 'frame' and event.text.strip():
			self.lines.append(event.text)
		await self.inner.emit(event)

	async def ask(self, request: Ask) -> str:
		return await self.inner.ask(request)

	def text(self, limit: int = 8000) -> str:
		return '\n'.join(self.lines)[:limit]


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
	# The app map, not just the test artifacts. Without it the router knew which scenarios
	# existed but nothing whatsoever about the application they test, so it could not answer
	# the first question anyone actually asks.
	knowledge = appmap.context_for_chat(ctx.ws) or '(the app map is empty - try /crawl or /learn)'
	return (
		f'App: {ctx.config.app_name} ({ctx.config.base_url or "no base_url set"})\n\n'
		f'Scenarios:\n{listing}\n\nRecent runs:\n{runs}\n\n'
		f'Connectors:\n{describe_connectors(ctx)}\n\n'
		f'=== What you know about the app (the app map) ===\n{knowledge}'
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


async def think(ctx: ShellContext, llm: Any, messages: list[BaseMessage]) -> ChatDecision | None:
	"""One routing call, bounded and narrated. None means it failed and has been reported.

	Unbounded was the old behaviour, and it is the worst one: a human typing "hi" sat in front
	of `working…` with no model named, no elapsed time, and nothing to act on. Naming the model
	matters because the usual cause is that it is the wrong one for this job - a slow reasoning
	model wired to the `chat` role answers a greeting in minutes, if at all.
	"""
	using = model_name(ctx.config, 'chat') or 'the default model'
	try:
		response = await asyncio.wait_for(llm.ainvoke(messages, output_format=ChatDecision), CHAT_TIMEOUT)
		return response.completion  # pyright: ignore[reportAny]
	except TimeoutError:
		await ctx.ch.log(
			f'⏳ {using} did not answer within {CHAT_TIMEOUT:.0f}s.\n'
			f'   Chat is routing through the `chat` model role. If that is a large reasoning model, '
			f'point it at a small fast one:  /set-model --fast <model>'
		)
	except Exception as e:  # the provider's own error is the useful part; do not swallow it
		await ctx.ch.log(f'💥 {using} failed ({type(e).__name__}: {str(e)[:200]})')
	return None


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
		decision = await think(ctx, llm, messages)
		if decision is None:
			return 1
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
		chosen = args_of(decision)
		if cmd.human_only:
			# Name the command it actually asked for. This used to say "approve" whatever was
			# refused, so asking it to change autonomy told you to approve a scenario.
			spoken = ' '.join(v for v in chosen.values() if v)
			refusal = f'That one is yours to do: type  /{cmd.name} {spoken}'.rstrip()
			await ctx.ch.log(paint(refusal, 'yellow'))
			record(ctx, Turn(role='assistant', text=refusal))
			return 1

		# A capturing channel only for the commands whose output the agent has to reason about;
		# everything else keeps streaming straight to the user as it always did.
		capture = Recorder(ctx.channel) if cmd.feeds_context else None
		if capture is not None:
			ctx.channel = capture
		try:
			last_code = await cmd.handler(ctx, coerce(cmd, chosen))
		finally:
			if capture is not None:
				ctx.channel = capture.inner

		record(ctx, Turn(role='assistant', text=decision.reply, command=name, args=chosen, exit=last_code))
		messages.append(AssistantMessage(content=f'ran {name} -> exit {last_code}'))
		said = capture.text() if capture is not None else ''
		messages.append(
			UserMessage(
				content=f'`{name}` returned:\n{said}\n\nAnswer the human from this.'
				if said
				else f'`{name}` finished with exit code {last_code}. Anything else needed for the request?'
			)
		)
	return last_code
