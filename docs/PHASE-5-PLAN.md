# Phase 5 Plan — Interactive Shell (`qa` chat)

Status: agreed plan, not yet started · Written 2026-08-28
Read [ARCHITECTURE.md](ARCHITECTURE.md) first; Phases 1–4 are shipped (see their plan docs).
Self-contained so a future session can execute it cold.

---

## 0. Context recap (for future sessions)

Shipped: the full loop — `qa learn` (annotated-doc → appmap) → `qa plan` (knowledge →
draft scenarios) → `qa approve` (hash-bound) → `qa run <id>` (per-step verdicts +
evidence) → automatic reflection back into the appmap; plus `qa explore`, `qa replay`,
`qa crawl`, `qa list`, `qa scenarios`, `qa models`, MCP connectors and Jira
(`qa plan --ticket`, `qa file-bug`). 55 tests, ruff + pyright strict, `./check.sh`.

Two live verifications still pending, unrelated to this phase: Jira OAuth pass
(Phase 3) and bootstrapping the user's real appmap from their annotated doc (Phase 4).

**Why this phase exists:** a new user currently has to learn eight subcommands before
they ever feel the loop. The product's face should be a conversation, like Claude Code.

Decisions made by the user (2026-08-28):
1. The interactive shell **is Phase 5**; the old Phase 5 (suites, `run --all`,
   run-over-run comparison, stuck-escalation, selector auto-healing) becomes **Phase 6**.
2. **Hybrid input**: slash commands execute instantly with no LLM call; free text goes
   to a routing agent that picks commands and can chain them.
3. **No new dependencies** — stdlib `readline` only (history, arrow keys, completion).

## 1. Non-negotiable invariants

- **The chat agent can never approve.** `approve` is marked human-only: it is absent
  from the agent's tool list. If free text asks for approval, the agent answers "type
  `/approve <id>`". Even the human path prints the scenario and asks y/N. Everything
  built in Phases 2–4 rests on this gate.
- **The headless CLI is untouched.** `qa <subcommand>` keeps its argparse surface and
  exit codes (0 pass / 1 fail / 2 usage) — CI depends on them. The shell is a second
  front-end over the same functions, not a replacement (tests assert this).
- **Secrets stay in memory.** One `HumanInTheLoop` per session, so a credential typed
  once is reused for the session; never written to disk; cleared by `/forget` and on
  exit. Same contract as the `<secret>` placeholder rule.

## 2. New modules

```
nkqa/shell/
  __init__.py
  commands.py   # ONE registry: name -> (callable, params, help, human_only).
                #   Slash parser and the agent's tool list are both generated from it,
                #   so a new command appears in both automatically.
  session.py    # the REPL: banner, prompt loop, readline history + completion,
                #   slash dispatch, Ctrl+C/Ctrl+D semantics, session state
  agent.py      # free text -> chat-role LLM with command tools -> execute + narrate
  render.py     # status banner, scenario/run tables, ANSI colour helpers (no deps)
nkqa/revise.py  # `qa revise <id> "<instruction>"`: planner-role rewrite of one
                #   scenario file (see §4)
```

`cli.py` keeps its argparse (the CI contract); the registry wraps the same underlying
functions in `nkqa/*`. Slight duplication is deliberate — it avoids destabilising the
shipped CLI surface.

## 3. Session behaviour

- `qa` with no arguments opens the session (`qa chat` as an explicit alias).
- **Banner on entry**: app name + base URL, appmap file count, scenarios
  (N approved / M draft / K stale), last verdicts, model roles — the state of your QA
  world at a glance.
- **Slash commands** (instant, no LLM): `/plan`, `/scenarios`, `/approve`, `/run`,
  `/explore`, `/learn`, `/reflect`, `/crawl`, `/replay`, `/list`, `/file-bug`,
  `/revise`, `/models`, `/forget`, `/help`, `/exit`. Tab-completes command names and
  scenario ids via `readline.set_completer`.
- **Free text** → routing agent (§5): "test the login flow" plans; "run the first one"
  resolves the reference and runs; "make step 3 stricter" revises.
- **HITL inline**: `ask_human` / `ask_credential` / `request_permission` prompts appear
  in the conversation instead of as raw terminal interrupts.
- **Interrupts**: Ctrl+C cancels the running command and returns to the prompt (runs
  already save partial evidence on KeyboardInterrupt); Ctrl+D or `/exit` leaves.
- **Session memory**: last plan output, last run, last listing — so pronouns
  ("run it", "approve the second one") resolve without re-asking.

Async: one event loop per session (`asyncio.run(session.run())`); input is read with
`asyncio.to_thread(input)`, the pattern `hitl.py` and `greet` already use, so
interactive prompts inside running commands keep working.

## 4. `qa revise <id> "<instruction>"`

The killer chat interaction ("make step 3 stricter", "add a step checking the total")
needs a scenario editor, and there isn't one. `revise` sends the current scenario plus
the instruction to the **planner-role** model and rewrites the file. Because the
content changes, the stored approval hash stops matching — the scenario shows as STALE
and the runner refuses it until re-approved. The gate holds with no extra code; state
this explicitly in the output ("approval invalidated — re-approve to run").
Available as both `/revise` in the shell and `qa revise` on the CLI.

## 5. The routing agent

- New model role **`chat`**, default `fast` (routing and narration are cheap; the heavy
  thinking stays in the commands: planner drafts, executor drives, reflector learns).
  Add to `ROLES`, `DEFAULT_MODELS`, the config template, and `qa models`.
- Tools = the registry minus `human_only` entries. Loop: interpret → call a command →
  narrate the result → offer the obvious next step ("2 drafts written; review then
  `/approve auth/login`").
- Ambiguity is asked, never guessed — same rule the planner follows.

## 6. Implementation order (green on ./check.sh each step)

1. `chat` model role (config, ROLES, `qa models`) + tests.
2. `commands.py` registry + `/help` + tests (registry covers every CLI command;
   `approve` flagged human-only).
3. `session.py`: banner, loop, readline history (`<workspace>/.qa_history`),
   completion, slash dispatch, Ctrl+C/Ctrl+D, session HITL + `/forget` + tests
   (scripted input lines, monkeypatched `input`).
4. `revise.py` + `/revise` + `qa revise` + tests (stubbed LLM; assert approval goes stale).
5. `agent.py` free-text routing + tests (stubbed LLM tool calls; **assert `approve` is
   absent from the tool list**).
6. `render.py` polish (banner, tables, colour), README + ARCHITECTURE updates, live
   session walkthrough. Commit per step.

## 7. Definition of done

- `qa` opens a session showing real workspace status; `/help` lists everything.
- Slash commands behave identically to their CLI equivalents (tests compare paths).
- Free text plans, runs, and revises; asking the agent to approve produces a refusal
  plus the `/approve` hint (test).
- `/approve` inside the shell prints the scenario and requires y/N.
- Ctrl+C during a run returns to the prompt with evidence saved; Ctrl+D exits cleanly.
- A credential typed once is reused for the session, never written to disk, cleared by
  `/forget` (test).
- Every existing CLI command and exit code unchanged (tests). No new dependencies.

## 8. Out of scope for Phase 5

Suites/tags, `run --all`, run-over-run comparison, stuck-escalation, selector
auto-healing (**Phase 6**) · full TUI panels, mouse, multiline editing (needs
prompt_toolkit — deliberately declined) · parallel/background runs · web UI ·
conversation persistence across sessions (the appmap is the durable memory).
