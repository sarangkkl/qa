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
qa init                          # create a QA workspace in the current directory
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
```

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
