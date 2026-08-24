# Codebase Guide (for beginners)

A guided tour of the browser-use codebase for developers who are new to it — no prior knowledge of browser automation, CDP, event buses, or LLM tooling assumed. Every chapter links to the real source with line numbers so you can jump straight into the code.

Read in order — each chapter builds on the previous one:

| # | Chapter | What you'll learn |
|---|---|---|
| 00 | [Start here](00-start-here.md) | What browser-use is, the 30-second mental model, how to run it once, and a glossary of every term the codebase uses. |
| 01 | [Project tour](01-project-tour.md) | The repo map, the `service.py`/`views.py`/`events.py` convention, and what every subpackage is for. |
| 02 | [The agent loop](02-the-agent-loop.md) | What happens inside `agent.run()`: the five phases of a step, how the prompt is built, and the key data models. |
| 03 | [BrowserSession & events](03-browser-session-and-events.md) | The event bus pattern, `BrowserSession`, the 14 watchdogs, and `BrowserProfile`. |
| 04 | [DOM to text](04-dom-to-text.md) | How a live webpage becomes the indexed text the LLM reads, and where `[5]` comes from. |
| 05 | [Tools & actions](05-tools-and-actions.md) | The action registry, dependency injection, and how to write your own custom action. |
| 06 | [The LLM layer](06-llm-layer.md) | One interface over 16 providers, and how structured output is enforced. |
| 07 | [Follow a click](07-follow-a-click.md) | Capstone: `click(index=5)` traced through every layer down to raw CDP. |

When you're done, graduate to [kt/](../../kt/00-INDEX.md) (denser architecture notes for experienced engineers) and the runnable scripts in [examples/](../../examples/).
