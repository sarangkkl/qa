# 01 — Project Tour: A Map of the Repository

Before reading any code, learn the layout. This repo is big (~4000-line files exist), but it follows a small set of conventions that make it predictable.

## Top level

| Path | What lives there |
|---|---|
| [browser_use/](../../browser_use/) | **The library itself.** The only Python package that ships to users. Everything else supports it. |
| [examples/](../../examples/) | ~100 runnable scripts grouped by topic. The best way to learn a feature is to find its example. Start with [examples/getting_started/](../../examples/getting_started/) (numbered 01–05). |
| [tests/](../../tests/) | `tests/ci/` is the real pytest suite (~150 files) run on every commit. Tests never hit real websites — they spin up a local `pytest-httpserver` with hand-written HTML. |
| [kt/](../../kt/00-INDEX.md) | "Knowledge transfer" docs — architecture notes for experienced engineers. Denser than this guide; good second read. |
| [skills/](../../skills/) | Markdown "skill" files consumed by AI coding assistants (not needed to understand the library). |
| [docker/](../../docker/), `Dockerfile` | Container builds. |
| [bin/](../../bin/) | Dev scripts: `setup.sh`, `test.sh`, `lint.sh`. |
| `README.md` | The project pitch + quickstart. `CLAUDE.md` / `AGENTS.md` are instructions for AI coding agents working on the repo. |
| [pyproject.toml](../../pyproject.toml) | Package metadata. Notable: the `browser-use` / `browseruse` / `bu` / `browser` console commands all run [browser_use/cli.py](../../browser_use/cli.py). |

There is no `docs/` site in the repo (hosted docs live at docs.browser-use.com); this guide and `kt/` are the in-repo documentation.

## The three-file convention

Almost every subpackage follows the same pattern. Learn it once and you can navigate anywhere:

| File | Contains |
|---|---|
| `service.py` | **The main logic.** The one class that does the work (`Agent`, `BrowserSession`, `DomService`, `Tools`). When in doubt, open the `service.py`. |
| `views.py` | **The data.** Pydantic models — settings, results, state objects. No behavior beyond validation. |
| `events.py` | **The messages.** Event class definitions for the event bus (only where events are used, mainly `browser/`). |

So "where is the code that does X?" is almost always answered by "`X`'s package → `service.py`", and "what shape is this data?" by "→ `views.py`".

## Inside `browser_use/` — the core four

These four packages are the machine. Chapters 02–05 cover them one each.

| Package | One-liner | Guide chapter |
|---|---|---|
| [agent/](../../browser_use/agent/) | The decision loop: build prompt → call LLM → execute actions → record history. Main class `Agent` in [service.py](../../browser_use/agent/service.py) (4100+ lines — don't read it top to bottom; chapter 02 gives you the map). Also holds the system prompts as markdown files in [agent/system_prompts/](../../browser_use/agent/system_prompts/). | 02 |
| [browser/](../../browser_use/browser/) | Everything Chrome: `BrowserSession` ([session.py](../../browser_use/browser/session.py)), launch config (`BrowserProfile` in [profile.py](../../browser_use/browser/profile.py)), the event definitions ([events.py](../../browser_use/browser/events.py)), and 14 watchdogs in [watchdogs/](../../browser_use/browser/watchdogs/). | 03 |
| [dom/](../../browser_use/dom/) | Turns the live page into the indexed text the LLM reads. `DomService` in [service.py](../../browser_use/dom/service.py) plus the serializer pipeline in [serializer/](../../browser_use/dom/serializer/). | 04 |
| [tools/](../../browser_use/tools/) | The action registry: what the LLM is allowed to do. `Tools` in [service.py](../../browser_use/tools/service.py), decorator machinery in [registry/](../../browser_use/tools/registry/). | 05 |

## Inside `browser_use/` — the supporting cast

You can ignore these on a first pass; come back when you need them.

| Package | One-liner |
|---|---|
| [llm/](../../browser_use/llm/) | Provider abstraction — one `chat.py` per provider (OpenAI, Anthropic, Google, Groq, Ollama, …). Chapter 06. |
| [filesystem/](../../browser_use/filesystem/) | A sandboxed scratch directory the agent can read/write during a run (`todo.md`, `results.csv`, …). |
| [mcp/](../../browser_use/mcp/) | Model Context Protocol, both directions: run browser-use *as* an MCP server for Claude Desktop, or connect the agent *to* external MCP servers as extra actions. |
| [tokens/](../../browser_use/tokens/) | Token counting and cost accounting per LLM call. |
| [screenshots/](../../browser_use/screenshots/) | Stores each step's screenshot on disk for the history/GIF. |
| [telemetry/](../../browser_use/telemetry/) | Anonymous usage analytics (PostHog). |
| [sync/](../../browser_use/sync/) | Streams run events to the Browser Use cloud dashboard (optional). |
| [skills/](../../browser_use/skills/) | Fetches reusable task "skills" from the Browser Use API and registers them as actions. |
| [actor/](../../browser_use/actor/) | A Playwright-style manual API (`Page`, `Element`, `Mouse`) over raw CDP — for when *you* want to drive the browser in code, no LLM involved. Has its own [README](../../browser_use/actor/README.md). |
| [sandbox/](../../browser_use/sandbox/) | Runs your agent function in a remote sandboxed environment. |
| [integrations/](../../browser_use/integrations/) | Third-party glue; currently Gmail (used for 2FA email codes). |
| [agent/cloud_events.py, beta/](../../browser_use/beta/) | Experimental next-gen agent. Ignore. |
| [controller/](../../browser_use/controller/) | **Deprecated** backwards-compat shim: `Controller` is just the old name for `Tools`. |

Top-level modules worth knowing: [config.py](../../browser_use/config.py) (env-var configuration, exports a `CONFIG` singleton), [cli.py](../../browser_use/cli.py) (the `browser-use` terminal command), [logging_config.py](../../browser_use/logging_config.py).

## The public API

What users import comes from [browser_use/__init__.py](../../browser_use/__init__.py):

```python
from browser_use import (
    Agent,             # the main entry point
    Browser,           # alias of BrowserSession
    BrowserSession,    # manages Chrome
    BrowserProfile,    # browser configuration
    Tools,             # action registry (old alias: Controller)
    ActionResult,      # what one action returns
    AgentHistoryList,  # what agent.run() returns
    ChatOpenAI, ChatAnthropic, ChatGoogle, ChatBrowserUse, ...  # 16 LLM wrappers
)
```

One trick to know: `__init__.py` doesn't actually import any of this up front. It keeps a `_LAZY_IMPORTS` table and a module-level `__getattr__`, so each symbol is only imported the first time you touch it. That's why `import browser_use` is fast even though the library is huge — and why jumping to a class from `__init__.py` in your IDE lands on a string table instead of the class. Use the table's paths to find the real home.

**Next:** [02 — The agent loop](02-the-agent-loop.md), where we open up `Agent.run()`.
