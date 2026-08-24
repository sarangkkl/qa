# 05 — Tools & Actions: The Hands

Package: [browser_use/tools/](../../browser_use/tools/) · Main files: [tools/service.py](../../browser_use/tools/service.py), [tools/registry/service.py](../../browser_use/tools/registry/service.py)

The tools layer answers two questions:

1. **What is the LLM allowed to do?** (the action registry)
2. **When it picks an action, how does that become real code running?** (dispatch)

## Two classes, one job

- [`Registry`](../../browser_use/tools/registry/service.py#L33) — generic machinery: a dict of registered actions plus the decorator that fills it and the dispatcher that calls them.
- [`Tools`](../../browser_use/tools/service.py#L441) — the concrete product: on construction it registers ~25 built-in browser actions into its registry. (`Controller` is the deprecated old name for `Tools`.)

The built-ins, roughly grouped:

| Group | Actions |
|---|---|
| Navigation | `navigate`, `search`, `go_back`, `wait` |
| Interaction | `click`, `input` (type text), `scroll`, `send_keys`, `upload_file`, `dropdown_options`, `select_dropdown` |
| Tabs | `switch`, `close` |
| Reading | `extract` (LLM-powered page-to-data), `search_page`, `find_elements`, `find_text`, `screenshot` |
| Files | `write_file`, `replace_file`, `read_file`, `save_as_pdf` (the agent's sandboxed scratch dir — `browser_use/filesystem/`) |
| Escape hatches | `evaluate` (run JavaScript) |
| Terminal | `done` — ends the run, carries the final answer |

Each is a small async function registered inline in `Tools.__init__` with a decorator:

```python
@self.registry.action('Click an element by its index')
async def click(index: int, browser_session: BrowserSession): ...
```

## How the LLM knows what it can do

This is the elegant part. The registry doesn't just *describe* actions to the LLM — it **generates the LLM's output schema** from them.

[`create_action_model()`](../../browser_use/tools/registry/service.py#L517) builds a pydantic model where each registered action is a field with its own parameter model, unioned together. That model becomes the `action` field of `AgentOutput` (chapter 02), which is enforced via structured output (chapter 06). So the LLM literally *cannot* return an action that isn't registered, or wrong parameter types — validation happens before any code runs.

Because some actions are registered with a `domains=[...]` filter, the available set can change per page — which is why the agent rebuilds the action model at the start of every step (`_update_action_models_for_page`). Site-specific actions appear in the prompt only when you're on a matching URL.

## Dispatch: from JSON to a function call

When the agent executes `{"click": {"index": 5}}`:

1. [`Tools.act()`](../../browser_use/tools/service.py#L2168) — unpacks the action name + params, applies a wall-clock timeout (default 180s, env `BROWSER_USE_ACTION_TIMEOUT_S`). Any exception becomes `ActionResult(error=...)` — remember, failure is data the LLM reads next step, not a crash.
2. [`Registry.execute_action()`](../../browser_use/tools/registry/service.py#L331) — looks up the action, validates params into its pydantic model, substitutes sensitive-data placeholders (see below), **injects special parameters**, and calls the function.
3. The function's return value is coerced: an `ActionResult` passes through; a plain string becomes `ActionResult(extracted_content=...)`.

### Dependency injection by parameter name

An action function can declare parameters that the LLM never sees — the framework fills them in based purely on the parameter's **name** ([the list](../../browser_use/tools/registry/service.py#L57)):

`browser_session`, `cdp_client`, `page_url`, `file_system`, `page_extraction_llm`, `available_file_paths`, `has_sensitive_data`, `extraction_schema`, `context`

So in `async def click(index: int, browser_session: BrowserSession)`, the LLM only sees `{"index": int}`; `browser_session` arrives by magic. If you've used pytest fixtures or FastAPI dependencies, it's the same trick.

### Sensitive data

You can pass `Agent(sensitive_data={'password': '...'})`. The LLM only ever sees the placeholder `<secret>password</secret>`; the registry substitutes the real value *after* the LLM's output is parsed, just before execution ([`_replace_sensitive_data`](../../browser_use/tools/registry/service.py#L427)). Your secrets never enter the model's context.

## Writing your own action (the practical bit)

This is the main extension point of the whole library, and it's three lines of ceremony:

```python
from browser_use import Agent, ActionResult, Tools
from browser_use.browser import BrowserSession

tools = Tools()

@tools.action('Ask a human for help with a question')
async def ask_human(question: str) -> ActionResult:
	answer = input(f'{question} > ')
	return ActionResult(extracted_content=f'The human said: {answer}')

@tools.action('Get current page cookies', domains=['*.example.com'])
async def get_cookies(browser_session: BrowserSession) -> ActionResult:
	...

agent = Agent(task='...', llm=llm, tools=tools)
```

Rules and gotchas:

- The **function name becomes the action name**; the decorator string is the description the LLM reads. Make both descriptive — they're prompt engineering.
- Parameters are inferred from the signature (a pydantic model is auto-generated). Alternatively pass an explicit `param_model=MyParams` as the first style. No `**kwargs` allowed.
- Params whose names match the injection list above are provided by the framework and hidden from the LLM.
- `domains=['*.example.com']` — offer the action only on matching pages.
- `terminates_sequence=True` — mark actions that navigate, so queued follow-up actions get abandoned (chapter 02's stale-page guard).
- Return an `ActionResult`. Fields that matter:
  - `extracted_content` — feedback shown to the LLM in the next step
  - `long_term_memory` — one short line that stays in `<agent_history>` for the rest of the run
  - `error` — mark the action failed (the text is shown to the LLM)
  - `is_done=True, success=True/False` — end the whole run (how `done` works)
- Sync functions work too (run in a thread), but prefer async.

Live examples: [examples/custom-functions/](../../examples/custom-functions/) — file uploads, human-in-the-loop, notifications, and more.

**Next:** [06 — The LLM layer](06-llm-layer.md) — how one interface hides 16 providers.
