# 06 — The LLM Layer: One Interface, Many Providers

Package: [browser_use/llm/](../../browser_use/llm/) · Main files: [llm/base.py](../../browser_use/llm/base.py), [llm/messages.py](../../browser_use/llm/messages.py), one folder per provider

The agent doesn't care whether it's talking to GPT, Claude, Gemini, or a local Ollama model. It talks to one interface, and each provider folder adapts that interface to its vendor's API.

## The interface: `BaseChatModel`

[`BaseChatModel`](../../browser_use/llm/base.py#L18) is a Python **Protocol** — an interface defined by shape, not inheritance. Any object with the right attributes satisfies it; no base class required. The entire contract is essentially one method:

```python
async def ainvoke(
	messages: list[BaseMessage],
	output_format: type[T] | None = None,
) -> ChatInvokeCompletion[T] | ChatInvokeCompletion[str]
```

- Pass messages and no `output_format` → get text back.
- Pass a **pydantic class** as `output_format` → get back a *validated instance of that class*. This is how the agent gets `AgentOutput` (chapter 02): it hands over the class, the provider forces the model to comply, and `response.completion` is already parsed — the agent never touches provider-specific JSON.

The return type [`ChatInvokeCompletion`](../../browser_use/llm/views.py) carries `.completion` (the text or parsed object), `.thinking` (reasoning text, if the model produces it), and `.usage` (token counts, used by `browser_use/tokens/` for cost accounting).

## Provider-neutral messages

[llm/messages.py](../../browser_use/llm/messages.py) defines the library's own message types — `SystemMessage`, `UserMessage`, `AssistantMessage` — with content that can mix text parts and image parts (that's how screenshots reach vision models). Each message has a `cache: bool` flag hinting that providers should cache it (the big static system prompt is marked cacheable; combined with the stable-prefix prompt layout from chapter 02, this cuts cost significantly).

Each provider folder then contains:

- `chat.py` — the `BaseChatModel` implementation (`ChatOpenAI`, `ChatAnthropic`, …)
- `serializer.py` — translates the neutral messages into that vendor's wire format

Providers available (all exported from `browser_use`): `ChatOpenAI`, `ChatAnthropic`, `ChatGoogle`, `ChatAWSBedrock`, `ChatAnthropicBedrock`, `ChatAzureOpenAI`, `ChatGroq`, `ChatOllama`, `ChatDeepSeek`, `ChatMistral`, `ChatCerebras`, `ChatOpenRouter`, `ChatLiteLLM`, `ChatVercel`, `ChatOCIRaw`, and `ChatBrowserUse` (the hosted, browser-task-fine-tuned models, `bu-*`).

## How structured output actually works

"Force the model to return this schema" is implemented differently per vendor, and it's worth knowing both tricks:

**Strategy 1 — JSON-schema response format** (OpenAI-style, [openai/chat.py](../../browser_use/llm/openai/chat.py)): the API accepts a `response_format` parameter carrying a JSON schema with `strict: true`; the model is constrained to emit matching JSON.

**Strategy 2 — forced tool call** (Anthropic-style, [anthropic/chat.py](../../browser_use/llm/anthropic/chat.py)): the schema is presented as the parameters of a single "tool", and `tool_choice` forces the model to call it. The tool-call arguments *are* the structured output. There are also fallback text-parsers for when a model answers in prose anyway.

Before either strategy, [`SchemaOptimizer`](../../browser_use/llm/schema.py#L12) massages the pydantic-generated JSON schema: inlines `$ref`s, forbids extra properties, strips keywords a given vendor doesn't support (Gemini gets its own variant). Different vendors accept different schema subsets — this class is where those quirks are absorbed so the rest of the codebase doesn't care.

## Error handling the agent relies on

[llm/exceptions.py](../../browser_use/llm/exceptions.py) defines `ModelProviderError` and `ModelRateLimitError`. Chapter 02 mentioned the agent can switch to fallback LLMs — these exception types are the trigger: providers must raise them (not vendor-specific errors) so the agent's retry/fallback logic works uniformly.

## Picking a model as a beginner

```python
from browser_use import Agent, ChatOpenAI

agent = Agent(task='...', llm=ChatOpenAI(model='gpt-4o'))
```

- Every provider has a runnable script in [examples/models/](../../examples/models/) showing exact setup + env vars.
- `ChatBrowserUse` is the "it just works" default (fine-tuned for browser tasks, one API key).
- Provider quirks are documented in [browser_use/llm/README.md](../../browser_use/llm/README.md).
- Because `BaseChatModel` is a Protocol, you can wrap literally anything — an internal gateway, a mock for tests — by implementing `ainvoke` with the right shape.

**Next:** [07 — Follow a click](07-follow-a-click.md) — the capstone: one action traced through every layer you've now met.
