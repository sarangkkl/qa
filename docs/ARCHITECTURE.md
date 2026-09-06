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

**Session autonomy** (`/mode ask | allow | refuse`) sets how that gate answers itself for
one session: stop and ask every time (the default), grant automatically, or refuse
automatically. It is session-scoped and never persisted — there is no config.yaml key, so a
workspace can never be born permissive — and it is `human_only`, so the agent cannot widen
its own autonomy. `refuse` outranks even a stored always-grant; the conservative answer
wins. It does not touch credentials, which keep their own human grant and origin binding,
and `approve` stays human-only in every mode. **It is not a sandbox**: it governs the
actions the agent itself declares risky, not what a browser is physically able to do.

### Stopping a run

`nkqa` owns SIGINT and SIGTERM in every surface: every `Agent` is constructed with
`enable_signal_handler=False`, because browser-use's own handler *pauses* the agent rather
than stopping it, and nothing ever resumes that pause. `nkqa/stop.py` is the whole mechanism:

- `StopSignal` is per session, lives on `ShellContext` (not on `HumanInTheLoop` - that object
  is the security surface and stays about secrets), and is handed to the runner, which
  `attach`es its `Agent`. It is passed to browser-use as `register_should_stop_callback`.
- Stopping fires both halves: tell the agent, then cancel the task. It also switches the
  summary GIF off, because browser-use encodes it synchronously on the way out.
- A stopped run keeps its evidence and its verdict but does **not** reflect into the appmap.
- `interruptible()` defines Ctrl+C for the CLI and the shell: first stops the run and keeps
  the evidence, second leaves immediately.
- The desktop adds one tier above this: killing the sidecar outright. That always works, and
  costs the session's credentials, any pending prompt, and the run's video.

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

### A crawl writes as it goes

The crawl used to hand back one structured result when it finished, and that was its only
write. Miss it — stop the run, crash it, or exhaust the step budget before the model calls
`done` — and the whole session was lost. A real 17-minute crawl over five screens wrote
nothing at all: the last step read `is_done: false`, so there was no structured output and
`appmap/` was never touched.

So the browser agent now has `record_page`, and calls it the moment it finishes with a
screen. Each page is its own file and its own git commit, written before the agent navigates
away. Stopping a crawl costs you the screen in progress, not the run. `record_flow` does the
same for journeys. `overview.md` stays an end-of-run merge, because merging the index is a
whole-map operation, not a per-page one.

**Route identity.** A page is keyed by its route with id-like segments collapsed to `:id`,
query and fragment dropped: `/pm-hub/501.D70162.00001` → `/pm-hub/:id`. Without it a dev
environment holding 442 projects looks like 442 screens, and the single-use OAuth redirect
URLs a login goes through look like pages of the app. Off-origin URLs are refused outright —
an identity provider is not part of the product.

**The skip list is the map itself.** `known_routes()` reads the `**Route:**` line back out of
`appmap/pages/*.md`, so it cannot drift from what is actually written, and a page a human
wrote by hand counts as known. A documented route is skipped rather than overwritten — this
is what makes "the human edit wins" structural instead of advisory. `qa crawl --refresh`
re-maps everything when the app has genuinely changed. Human-written template routes
(`/projects/{projectNumber}/{tab}`) cover their instances, so a hand-documented screen is not
re-mapped the moment the crawler sees a real id in the URL.

### Asking, and correcting

The chat router now gets the app map in its context (`appmap.context_for_chat`), so "what
does the clients page do?" is answered from what is written rather than guessed. Over the
budget, files are named but not loaded, so the agent can say a page doc exists that it has
not read — better than implying the app has nothing else.

`qa correct "<what is actually true>"` is the write half. Every other appmap writer takes a
path, a run name or a page budget; none of them takes a sentence, so correcting the agent
meant opening the file yourself. It shows a diff, then writes through the same
`appmap.apply()` as everything else — sandboxed, and committed as `appmap: corrected by you`,
so `git revert` undoes a correction that came out wrong.

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

### Changing it without editing YAML

`qa set-model --provider anthropic|openai [--smart ID] [--fast ID]`, the same command the
desktop Settings page runs, **edits the two aliases and not the five roles.** That is the
whole reason the indirection exists: one edit moves every role and the smart/fast split
survives. Collapsing all five onto one model makes every run either slow or dim. A role
someone has since pointed straight at a model no longer goes through an alias, so it does
not move — it is named in the output rather than silently rewritten.

Two names for the same model are accepted (`anthropic_claude_sonnet_5`, browser-use's own
naming, and `anthropic:claude-sonnet-5`, passed through verbatim). Anything written by
`set-model` uses the second form: model ids contain dots and dates that the first form's
underscore mangling would mangle.

`config.yaml` is edited **surgically, by line**, never round-tripped through a YAML dumper —
the file is full of comments explaining what each role is for, and a human edits it too.
`CATALOGUE` in `models.py` is what the UI offers, not what is accepted; any id is passed to
the provider as typed, so a model released after a build is still reachable.

**Keys are not part of this.** `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` are read from the
workspace `.env` at launch, so switching provider needs a reopen if the new key was not
already there. `qa models` (and `/health`) is what reports a missing one; `set-model`
succeeds regardless, because "the change did not apply" and "the key is not set yet" are
different failures and must not be reported as the same one.

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
  jira:                        # official Atlassian remote MCP, bridged to stdio
    command: npx
    args: ['-y', 'mcp-remote', 'https://mcp.atlassian.com/v1/sse']
    # expose_to_executor defaults to false for jira: the testing agent
    # must never file bugs on its own
  test-data:                   # customer's own tool server
    command: node
    args: ['./tools/seed-server.js']
    env: {SEED_KEY: 'env:SEED_KEY'}   # env: indirection - no secrets in config
```

**Jira scope in v1 (deliberately narrow):**
- **Read stories to plan from:** `qa plan --ticket PROJ-123` pulls the story +
  acceptance criteria and drafts scenarios against them.
- **Instructed bug filing only:** `qa file-bug <finding-id>` files a bug with evidence
  attached — only when the human says so. No autonomous filing, no Xray/Zephyr
  test-cycle management.

## 8. CLI surface (v1)

Shipped (Phases 1–4), plus the shell that fronts them (Phase 5):

```
qa                         # interactive session: slash commands + natural language
qa init                    # create the workspace (migrates old prototype recordings)
qa learn <doc.md|folder>   # build the appmap from an annotated doc (text + screenshots)
qa plan "<ask>" [--ticket PROJ-123]     # draft scenarios (status: draft)
qa scenarios               # ids, status (draft/approved/STALE), last verdict
qa approve <id>            # hash-bound approval; human keystroke, never the agent
qa revise <id> "<how>"     # rewrite a scenario (invalidates its approval)
qa run <id>                # execute an approved scenario -> per-step verdicts + evidence
qa explore [url] [focus]   # freeform AI-driven testing, no scenario
qa replay <run> [--all]    # deterministic re-run from history, no LLM
qa reflect <run>           # appmap learns from a run (automatic after every run)
qa crawl [--pages N] [--refresh]   # read-only exploration; skips pages already mapped
qa correct "<what is true>"        # fix what the app map gets wrong, in your own words
qa suite [--tag T] [--strict]           # run every approved scenario -> one CI report
qa compare [a] [b]                      # what changed between two suite runs
qa vault [status|set|rm|grant|revoke]   # stored credentials and their grants
qa list / qa models        # recorded runs · model roles, providers, key presence
qa set-model --provider anthropic|openai [--smart ID] [--fast ID]   # point the tiers at a provider
qa file-bug <run> [--step N]            # push a finding to Jira with repro + evidence
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
5. **Interactive shell** — `qa` with no arguments opens a Claude-Code-style session:
   slash commands + natural language over the commands above. *The product's face.*
   The chat agent can never approve — that stays a human keystroke.
6. **Suite & CI polish** — tag suites, run-over-run comparison, stuck-escalation,
   selector auto-healing. *Shipped: `qa suite`, `qa compare`, and the vault that makes an
   unattended run possible. Stuck-escalation and selector auto-healing remain open, and
   deliberately so — both need a signal only real runs can provide (§6).*

## 10. The UI channel (added for the desktop track)

Nothing below the front-ends prints or reads stdin. Every verb is handed a
`Channel` (`nkqa/ui.py`) and talks through it:

```python
class Channel(ABC):
	async def emit(self, event: Event) -> None: ...   # log | step | verdict | artifact | progress | done
	async def ask(self, request: Ask) -> str: ...     # text | secret | confirm | choice
```

`TerminalChannel` is the CLI: events print, asks read stdin, and the shipped output is
unchanged byte for byte. The desktop sidecar passes a channel that puts the same events
on a WebSocket and resolves `ask` from the UI. `cli.py` and `shell/session.py` are the
only modules that may still call `print` - they *are* the terminal surface.

Two consequences worth knowing:
- `Ask.interrupt` marks a prompt raised from inside a running job (the HITL tools). The
  terminal frames it with rules so it is visible in a wall of browser-use logs; a GUI
  renders it as a modal.
- A collected secret goes straight into `HumanInTheLoop.secrets` and is never put back
  into an Event. The channel carries the prompt, never the answer - `tests/test_ui.py`
  asserts it.

`execution/stream.py` forwards browser-use's own `logging` output and bare prints onto
the channel during a run, and turns each finished step into a step Event carrying the
screenshot path. Both are no-ops for `TerminalChannel`, where browser-use is already
writing to the same terminal.

## 11. Non-goals for v1

- Web dashboard / hosted SaaS (validate the workflow first)
- Cloud browser farm / parallel remote execution
- Xray/Zephyr-style test-cycle management in Jira
- Vector database as system of record
- Autonomous bug filing without human instruction
