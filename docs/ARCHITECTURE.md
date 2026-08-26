# NKQA — Architecture

**One sentence:** A CLI-first AI QA teammate ("Claude Code for QA") that lives in a git
workspace: it plans test scenarios as reviewable files, executes only what a human has
approved, records evidence per scenario, learns the app over time, and connects to Jira
and anything else through MCP.

Status: agreed design, 2026-08-26. The prototype (`qa_test.py`) validated the execution
engine (browser-use agent, human-in-the-loop tools, evidence capture, deterministic
replay). This document specifies the product built around it.

---

## 1. Product shape

- **CLI/desktop tool first.** Runs on the QA engineer's machine, like Claude Code.
  No server, no hosted dashboard, no cloud browser farm in v1.
- **The workspace is the database.** All state — knowledge, scenarios, evidence,
  config — is plain files in a git repo. Git gives us review (PRs), versioning,
  diffing, and audit history for free. No DB, no vector store in v1 (see §5).
- **Approval is a hard gate, not a suggestion.** The planner writes `draft` scenarios;
  the runner *refuses* anything not `approved`. A human flips the status (directly or
  by merging a PR). This is the product's trust story.

## 2. Workspace layout

```
qa-workspace/
  config.yaml            # app URL, models, MCP servers, credential refs (never secrets)
  appmap/                # knowledge base — what the agent knows before it starts
    overview.md          # always-loaded index: app purpose, roles, environments, areas
    pages/*.md           # per-page: purpose, key elements, flows in/out, quirks
    flows/*.md           # cross-page user journeys
    learned.json         # machine-facing: selectors, timings, flakiness counters
  scenarios/
    <area>/<scenario>.md # one file = one scenario (schema in §3)
  runs/
    <date>-<slug>/       # per-run evidence: video/, screenshots/, steps.json,
                         # results.md, conversation/
```

## 3. Scenario lifecycle & file schema

Markdown with YAML frontmatter — readable by QA humans, structured enough for agents.

```markdown
---
id: checkout/purchase-with-coupon
title: Purchase with a percentage coupon
status: draft            # draft | approved | deprecated  — runner enforces this
ticket: PROJ-123         # optional Jira link
tags: [checkout, regression]
preconditions:
  - a test user with a saved address exists
  - coupon SAVE10 is active in the test environment
---

## Steps
1. Log in as the test user.
2. Add "Blue Widget" to the cart.
3. Open the cart and apply coupon `SAVE10`.
   - **Expect:** total drops by 10%; coupon chip is visible.
4. Complete checkout with the saved address and test card.
   - **Expect:** order confirmation page with an order number.

## Out of scope
- Real payment capture (permission-gated; not granted for this scenario).
```

Lifecycle: `qa plan` writes drafts → human reviews/edits (PR is the intended review
surface) → `qa approve` records approval **bound to a content hash** of the scenario
(any later edit invalidates it; the runner refuses stale approvals until re-approved) →
`qa run` executes → evidence folder created under `runs/`, with `results.md` linking
video/screenshots to specific step numbers → post-run reflection updates `appmap/` (§5).

## 4. Agents and their roles

Two (later three) separate agents, instantiated separately, with separate models:

| Agent         | Job                                                                | Browser | Default model tier |
|---------------|--------------------------------------------------------------------|---------|--------------------|
| **Planner**   | Read appmap + Jira story / free-text ask → write draft scenarios   | rarely  | smart              |
| **Executor**  | Drive the browser through an approved scenario; record evidence    | yes     | fast               |
| **Reflector** | Post-run: diff observations vs appmap, commit knowledge updates    | no      | fast               |

The split works *because* of the approval gate: the smart model (plus a human) did the
thinking at plan time; execution is following an explicit script, which a cheap model
does reliably. Runs vastly outnumber planning sessions, so this is the cost model that
makes the product economical.

The executor keeps the prototype's human-in-the-loop tools unchanged:
- `ask_human` — never guess when ambiguous or blocked
- `ask_credential` — secrets collected at runtime, hidden from the LLM via
  `<secret>` placeholders, never written to disk
- `request_permission` — dangerous/irreversible actions gated by
  allow-once / session / always / deny (persisted grants in the workspace)

## 5. Knowledge & learning ("the QA notebook")

**No AI/vector database.** Learned knowledge is plain files in git — auditable,
diffable, revertible, human-editable. The mental model sold to customers: the agent
keeps a QA notebook about your app, in your repo — not a black-box brain.

Two kinds of memory, never conflated:
- **Distilled knowledge** (`appmap/`): small, curated, load-bearing. This is the moat.
- **Raw evidence** (`runs/`): large, append-only, archive. Reflection distills it into
  the appmap; nothing reads it wholesale afterwards.

**Learning loop:** after every run, the Reflector gets the run history + current appmap
and writes edits as git commits: new pages discovered, changed selectors
(→ `learned.json`), quirks ("coupon field only appears once the cart has items"),
flaky spots. `git log appmap/` shows what the agent learned; humans correct it and the
human edit wins.

**Retrieval is navigation, not embedding search.** `overview.md` is always loaded as
the index; agents pull specific `pages/*.md` / `flows/*.md` by name and grep, the way
Claude Code navigates a codebase. Deterministic, debuggable, free. An embedding index
may eventually appear *inside* the docs-ingestion pipeline (Phase 4, e.g. searching 500
raw Confluence pages), but its output is distilled markdown; the appmap stays the
system of record.

Appmap writers (all funnel into the same files): manual authoring (final authority) →
learn-from-runs (Reflector) → autonomous onboarding crawl (`qa init --crawl`) → docs
ingestion (Confluence/PRDs, last).

## 6. Model configuration

Models are assigned to *roles*, with alias indirection so configs survive model churn:

```yaml
# config.yaml
models:
  planner:   smart
  executor:  fast
  reflector: fast
  fallback:  smart          # cross-provider failover (browser-use fallback_llm)
aliases:
  smart: anthropic/claude-sonnet-5
  fast:  anthropic/claude-haiku-4-5
```

Override precedence (Claude Code feel): config default → CLI flag
(`qa run checkout --model smart`) → in-session `/model` switch.
`calculate_cost=True` reports cost per role in `results.md`.

Planned enhancement (not v1): **stuck-escalation** — when the fast executor hits a
step that no longer matches reality, escalate that one step to the smart model, then
drop back down. Needs a good "am I stuck?" signal; design it after observing real runs.

## 7. Extensibility & integrations: MCP

The testing agent is an **MCP client**. "Give the agent a new tool for a new
environment" = add an MCP server to `config.yaml`; its tools register into the
browser-use tool registry (browser-use ships the MCP client). We build no bespoke
plugin system. Jira is simply the first bundled connector (Atlassian's official MCP
server), not custom integration code.

```yaml
mcp:
  jira:
    server: atlassian          # bundled preset
  test-data:
    command: ["node", "./tools/seed-server.js"]   # customer's own tool server
```

**Jira scope in v1 (deliberately narrow):**
- **Read stories to plan from:** `qa plan --ticket PROJ-123` pulls the story +
  acceptance criteria and drafts scenarios against them.
- **Instructed bug filing only:** `qa file-bug <finding-id>` files a bug with evidence
  attached — only when the human says so. No autonomous filing, no Xray/Zephyr
  test-cycle management.

## 8. CLI surface (v1)

```
qa init [--crawl]          # create workspace; optionally onboard via autonomous crawl
qa plan "<ask>" | --ticket PROJ-123     # draft scenarios (status: draft)
qa approve <scenario>      # flip to approved (or merge the PR)
qa run <scenario|--tag t|--all>         # execute approved scenarios, record evidence
qa replay <scenario>       # deterministic re-run from history, no LLM (from prototype)
qa list                    # scenarios + last verdicts
qa file-bug <finding-id>   # push a finding to Jira with evidence
qa config / /model         # settings, model switching
```

## 9. Build phases

1. **Product skeleton** — package + CLI chassis, workspace layout, config loading
   (incl. `models:`), prototype code moved into an executor module ~verbatim.
2. **Plan → approve → run loop** — scenario schema, planner agent, approval gate,
   per-scenario evidence with step-linked `results.md`. *The differentiator; sellable
   as a demo on its own.*
3. **MCP + Jira read** — MCP client wiring, `qa plan --ticket`, instructed
   `qa file-bug`.
4. **App map** — cheapest sources first: manual + learn-from-runs, then
   `qa init --crawl`, docs ingestion last. *The compounding moat.*
5. **Suite & CI polish** — tag suites, `run --all` regression mode on the replay
   machinery, run-over-run comparison, stuck-escalation.

## 10. Non-goals for v1

- Web dashboard / hosted SaaS (validate the workflow first)
- Cloud browser farm / parallel remote execution
- Xray/Zephyr-style test-cycle management in Jira
- Vector database as system of record
- Autonomous bug filing without human instruction
