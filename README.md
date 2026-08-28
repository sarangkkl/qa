# NKQA

AI QA teammate, CLI-first. It records AI-driven browser tests with a human in the loop
(asks when unsure, collects credentials without seeing them, requests permission before
anything dangerous), saves full evidence (video, gif, transcript, structured history),
and replays recordings deterministically without an LLM.

Design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · Current phase: [docs/PHASE-1-PLAN.md](docs/PHASE-1-PLAN.md)

## Install (dev)

```bash
python -m venv venv && venv/bin/pip install -e .
```

Put API keys in `.env` (e.g. `ANTHROPIC_API_KEY=...`).

## Quickstart

```bash
qa                               # interactive session (the main way to use it)
qa init                          # create a QA workspace in the current directory
qa learn appdoc.md               # build the appmap from an annotated doc (screenshots + text)
qa plan "the checkout flow"      # planner drafts scenarios from app knowledge (no browser)
qa plan --ticket PROJ-123        # plan from a Jira story's acceptance criteria (via MCP)
qa scenarios                     # list scenarios: status + last verdict
qa approve checkout/coupon       # review + approve (hash-bound: edits invalidate it)
qa run checkout/coupon           # execute in a real browser -> per-step verdicts + results.md
qa explore https://your-app.com "checkout flow"   # freeform AI-driven testing, no scenario
qa list                          # all recorded runs
qa replay <run-name>             # rerun WITHOUT the LLM - free and repeatable
qa replay --all                  # whole suite (CI mode; exit 0 = all passed)
qa file-bug <run> [--step N]     # file a Jira bug from a failed run - always human-confirmed
qa reflect <run>                 # update the appmap from a past run (automatic after runs)
qa crawl                         # optional: explore the live app read-only to enrich the map
qa revise <id> "<how>"           # rewrite a scenario (invalidates its approval)
qa models                        # roles -> models -> providers -> are the API keys set?
```

### The interactive session

`qa` with no arguments opens a Claude-Code-style session: it greets you with the state
of your QA world (appmap size, scenarios by status, recent runs, active models), then
takes either slash commands (`/plan`, `/run`, `/scenarios`, `/revise`, `/help` - instant,
no LLM cost) or plain English ("which scenarios are still drafts?", "make step 3
stricter"). Credentials and permission prompts appear inline; a credential typed once is
reused for the session, never written to disk, and `/forget` clears it. Ctrl+C cancels
whatever is running and returns to the prompt; Ctrl+D or `/exit` leaves.

**The chat agent cannot approve scenarios.** Ask it to and it hands the keystroke back to
you (`/approve <id>`) - approval is the gate everything else rests on, so it is enforced
structurally, not by asking the model nicely.

The appmap (`appmap/`) is the product's memory: plain markdown in git, no vector DB.
It's seeded by `qa learn` from a document you write - screenshots with a line or two
about each screen, roles, and flows - then grows automatically after every run
(git-committed as `appmap: learned from <run>`; disable with `appmap.auto_reflect: false`).
The planner reads all of it, so everything the map knows shows up in better scenarios.

Extra tools for the agent come from MCP servers listed in `config.yaml` (`mcp:` section):
any server's tools can be exposed to the executor (test-data seeders, OTP readers, ...).
Jira is just the first connector - and it is *not* exposed to the executor, so bugs are
only filed when you run `qa file-bug`. Jira needs Node.js (`npx mcp-remote` bridges
Atlassian's remote MCP; first use opens an OAuth login in your browser).

The gate: `qa run` refuses drafts, deprecated scenarios, and scenarios edited after
approval (stale hash). A human approval is always in the loop before a browser moves.

## Workspace

```
config.yaml     # app URL, model roles (planner/executor/reflector/fallback), aliases
appmap/         # what the agent knows about your app (grows over time)
scenarios/      # one markdown file per scenario; approval bound to a content hash
runs/<name>/    # evidence per run: results.md, history.json, videos/, gif, conversation/
```

## Development

```bash
./check.sh       # ruff + pyright strict + pytest - the definition of "passing"
./check.sh fix   # auto-format first
```

Conventions live in [CLAUDE.md](CLAUDE.md).
