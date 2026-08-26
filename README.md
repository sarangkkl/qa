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
qa run https://your-app.com "checkout flow" --name checkout
qa list                          # all recorded tests
qa replay checkout               # rerun WITHOUT the LLM - free and repeatable
qa replay --all                  # whole suite (CI mode; exit 0 = all passed)
qa replay checkout --var email=x@y.com
qa run --model smart             # override the executor model for one run
```

## Workspace

```
config.yaml     # app URL, model roles (planner/executor/reflector/fallback), aliases
appmap/         # what the agent knows about your app (grows over time)
scenarios/      # planned test scenarios (Phase 2: plan -> approve -> run)
runs/<name>/    # evidence per test: history.json, videos/, last_run.gif, conversation/
```

## Development

```bash
./check.sh       # ruff + pyright strict + pytest - the definition of "passing"
./check.sh fix   # auto-format first
```

Conventions live in [CLAUDE.md](CLAUDE.md).
