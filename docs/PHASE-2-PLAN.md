# Phase 2 Plan — The Plan → Approve → Run Loop

Status: agreed plan, not yet started · Written 2026-08-26
Read [ARCHITECTURE.md](ARCHITECTURE.md) first; [PHASE-1-PLAN.md](PHASE-1-PLAN.md) describes what already exists.
Self-contained so a future session can execute it cold.

---

## 0. Context recap (for future sessions)

Phase 1 shipped: installable `nkqa` package, `qa` CLI (`init/run/replay/list/version`),
workspace layout (config.yaml, appmap/, scenarios/, runs/), role-based model config
(`nkqa/models.py`: planner/executor/reflector/fallback → aliases → `get_llm_by_name`),
HITL tools as a class (`nkqa/hitl.py`), execution modules ported from the prototype
(`nkqa/execution/runner.py` freeform record, `replay.py`, `evidence.py`). All green under
ruff + pyright strict + pytest via `./check.sh`; pre-commit enforces it.

Phase 2 builds the product's identity: scenarios are drafted by a planner agent as
reviewable files, a human approves them (hash-bound), and only then does the executor
drive the browser — with structured per-step verdicts. This is the Claude Code
plan-mode analogy: **plan from knowledge, never by browsing**.

Decisions made by the user (2026-08-26):
1. **Planner input = appmap + ask/ticket, no browser.** Exactly like Claude Code plans
   from the repo. The appmap is thin in Phase 2 (stub + hand-written notes + past runs)
   and grows in Phases 3–4; the planner interface does not change.
2. **Approval is hash-bound.** Editing an approved scenario invalidates the approval;
   the runner refuses stale approvals until re-approved.
3. **Execution returns structured per-step verdicts** (pass/fail/blocked + note),
   rendered into results.md with evidence links.

## 1. Scenario file spec

One file = one scenario: `scenarios/<area>/<slug>.md`. Scenario id = path relative to
scenarios/ without extension (e.g. `checkout/purchase-with-coupon`). YAML frontmatter +
markdown body:

```markdown
---
title: Purchase with a percentage coupon
status: draft            # draft | approved | deprecated
ticket: PROJ-123         # optional, free text until Phase 3
tags: [checkout, regression]
preconditions:
  - a test user with a saved address exists
approved_hash: 3f2a…     # written by `qa approve`; absent on drafts
approved_by: Gaurav Sah <sarangkkl2@gmail.com>   # from git config user.name/email
approved_at: 2026-08-26T14:30:00
---

## Steps
1. Log in as the test user.
2. Open the cart and apply coupon `SAVE10`.
   - **Expect:** total drops by 10%; coupon chip is visible.

## Out of scope
- Real payment capture.
```

**Content hash**: sha256 of the file with the volatile frontmatter keys removed
(`status`, `approved_hash`, `approved_by`, `approved_at`) and whitespace-normalized
(strip trailing spaces, collapse blank runs). Everything else — title, ticket, tags,
preconditions, steps, out-of-scope — is approval-relevant and included. `qa approve`
computes and stores it; the runner recomputes and compares. Mismatch ⇒ "approval is
stale (file edited since approval)" ⇒ exit 2.

## 2. New modules

```
nkqa/
  scenarios.py             # parse/serialize scenario files; content_hash(); Scenario dataclass;
                           #   lifecycle checks (is_runnable -> ok | draft | stale | deprecated)
  planner.py               # planner agent: gather context (appmap/*.md, existing scenario
                           #   titles, recent run summaries) + ask -> structured drafts -> files
  execution/
    scenario_runner.py     # execute one approved scenario with per-step verdicts
    report.py              # results.md from verdicts + evidence links
```

**scenarios.py** parses frontmatter with pyyaml and the body with a light split on
`## Steps` / `## Out of scope` — no markdown library (dependency policy). Steps are the
numbered items; an indented `**Expect:**` bullet attaches to the preceding step.
Serialization must round-trip cleanly since the planner writes and humans edit.

**planner.py** is a *direct LLM call* (role `planner`, via `nkqa.models.resolve_llm`),
NOT a browser Agent. Context assembled into one prompt: full appmap/*.md (it is small —
revisit if it outgrows context in Phase 4), list of existing scenario ids+titles (avoid
duplicates), last verdict per scenario from runs/ (evidence.py), and the user's ask.
Output: structured list of scenarios (area, slug, title, tags, preconditions,
steps[{action, expect}], out_of_scope) → serialized to draft files via scenarios.py.
Never overwrites an existing file unless `--force`; prints what it wrote.
⚠ Verify at implementation: browser-use `BaseChatModel.ainvoke` signature and its
structured-output mechanism (`output_format=`? else prompt-for-JSON + pydantic parse).

**scenario_runner.py** builds the executor task from the scenario (preconditions,
numbered steps with expectations, out-of-scope as prohibitions), runs a browser-use
Agent (role `executor`, HITL tools, same evidence params as freeform) with
`output_model_schema=ScenarioResult`:

```python
class StepVerdict(BaseModel):
	step: int
	verdict: Literal['pass', 'fail', 'blocked']
	note: str  # for fail: expected vs actual + repro detail
class ScenarioResult(BaseModel):
	steps: list[StepVerdict]
	summary: str
```

⚠ Verify at implementation: the accessor for the structured final result on
AgentHistoryList (likely `history.structured_output`); fall back to parsing
`final_result()` as JSON if needed. A missing/unparseable result is itself a `blocked`
outcome, never a silent pass. Exit code: 0 all pass, 1 any fail/blocked.

**report.py** writes `results.md` into the run dir: scenario id + hash at time of run,
per-step table (verdict, note), links to `videos/`, `last_run.gif`, `conversation/`,
and the run's history.json. Also appends one summary line to nothing else — run-over-run
comparison is Phase 5.

## 3. CLI changes

| Command | Behavior |
|---|---|
| `qa plan "<ask>" [--area X] [--force]` | draft scenarios from appmap + ask (planner role) |
| `qa scenarios` | list all scenarios: id, title, status (draft/approved/STALE/deprecated), last verdict |
| `qa approve <id>` | show the file (and mark staleness), confirm y/N, write status+hash+approver+timestamp |
| `qa run <id>` | **new meaning**: execute an approved scenario (refuses draft/stale). Evidence → `runs/<slug>--YYYYMMDD-HHMM/` (timestamped: scenarios run many times) |
| `qa explore [url] [focus] [--name]` | the old freeform record flow, moved verbatim from `qa run` |
| `qa replay`, `qa list` | unchanged; they operate on run dirs and keep working for scenario runs |

Compat: `qa run <something-that-looks-like-a-url>` prints "did you mean qa explore?"
and exits 2. cli.py stays dispatch-only (CLAUDE.md boundary).

## 4. Implementation order (each step green on ./check.sh)

1. `scenarios.py` + tests (parse/serialize round-trip, hash stability, hash ignores
   volatile keys, is_runnable matrix: draft/approved/edited-after-approval/deprecated).
2. `qa scenarios` + `qa approve` + tests (approve writes hash+approver; edit → STALE;
   re-approve clears).
3. `qa explore` rename + `qa run <id>` gate + tests (refusal paths need no browser).
4. `scenario_runner.py` + `report.py` + tests (task-prompt building, verdict→results.md
   rendering, exit codes — agent mocked; verify structured-output accessor live).
5. `planner.py` + `qa plan` + tests (context assembly and file-writing tested with a
   stubbed LLM; one live smoke).
6. Docs: README quickstart gains the plan→approve→run flow; ARCHITECTURE.md §3 gains
   the hash-bound detail. Commit in chunks matching steps 1–6.

Estimated: ~700 lines new code + tests. No new dependencies.

## 5. Definition of done

- `qa plan "test the login flow"` writes ≥1 draft under scenarios/; `qa scenarios`
  shows it as draft; `qa run <id>` refuses with a clear message and exit 2.
- `qa approve <id>` then `qa run <id>` executes in a real browser and produces
  `runs/<slug>--<ts>/results.md` with per-step verdicts and working evidence links.
- Editing one step after approval flips `qa scenarios` to STALE and `qa run` refuses;
  re-approving runs again. (Live check by the user — drives a real browser.)
- `qa explore` behaves exactly like Phase 1 `qa run` (freeform). `qa replay` still
  replays both freeform and scenario runs.
- `./check.sh` green; pytest covers every non-browser path above.

## 6. Out of scope for Phase 2

Jira ticket fetching (Phase 3 — `ticket:` stays free text) · appmap enrichment and
reflection after runs (Phase 4 — but scenario runs already leave history.json for it) ·
suites/tags execution, run-over-run comparison, stuck-escalation (Phase 5) ·
PR-based approval tooling (git-native already: files diff fine; `qa approve` is the
only gate the runner trusts).
