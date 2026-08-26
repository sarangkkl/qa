# Phase 1 Plan — Product Skeleton

Status: agreed plan, not yet started · Written 2026-08-26
Read [ARCHITECTURE.md](ARCHITECTURE.md) first — it holds every design decision.
This document is deliberately self-contained so a future session can execute it cold.

---

## 0. Context recap (for future sessions)

**The product:** NKQA (working name, see §1) — a CLI-first AI QA teammate,
"Claude Code for QA". Plans test scenarios as reviewable git files, executes only
human-approved scenarios, records evidence per scenario, learns the app over time
into a plain-file `appmap/` (no vector DB), extends via MCP. Full design: ARCHITECTURE.md.

**What exists today (repo state before Phase 1):**
- `qa_test.py` at repo root — the validated prototype. It can: record an AI-driven
  browser test (browser-use Agent) with human-in-the-loop tools (`ask_human`,
  `ask_credential` with `<secret>` placeholders, `request_permission` with
  once/session/always/deny grants persisted to `qa_output/qa_permissions.json`);
  save evidence (video, gif, per-step LLM transcript, `history.json` checkpointed
  every step via `on_step_end`); list tests; deterministically **replay** a recording
  without the LLM (`Agent.load_and_rerun`, `--var key=value` overrides,
  `detect_variables_in_history`); replay-all as CI mode. It works — do not break its
  behavior, relocate it.
- `qa_output/tests/<name>/` — existing recordings (evidence format the product keeps).
- `venv/` — Python 3.13.2, **browser-use 0.13.8**. Key APIs the prototype uses:
  `browser_use.Agent`, `Tools` (+ `tools.registry.action` decorator), `ActionResult`
  (`extracted_content`, `long_term_memory`), `BrowserProfile(headless,
  record_video_dir)`, `Agent(task, tools, extend_system_message, sensitive_data,
  fallback_llm, generate_gif, save_conversation_path, calculate_cost,
  file_system_path)`, `agent.run(max_steps, on_step_end)`, `agent.save_history()`,
  `agent.load_and_rerun(history_file, variables, skip_failures)`,
  `browser_use.llm.models.get_llm_by_name`, `browser_use.agent.gif.create_history_gif`,
  `browser_use.agent.variable_detector.detect_variables_in_history`.
- `kt/` — knowledge-transfer notes (not product code). `.env` — API keys (gitignored).
- The repo folder is named `browser-use` but it is the **product repo**, not the
  library; the library comes from pip.

**Phase sequence (from ARCHITECTURE.md §9):** 1 skeleton → 2 plan/approve/run loop →
3 MCP + Jira read → 4 app map → 5 suite/CI polish. This doc covers Phase 1 only.

## 1. Open decision: the name

Working name **`nkqa`** (package) with CLI command **`qa`**. The user has NOT yet
confirmed the final product name — ask before publishing anything under it; renaming
the package later is a find-replace, so do not block Phase 1 on it.

## 2. Goal of Phase 1

Turn the prototype into an installable package with a CLI chassis, a workspace, and
role-based model config — **with zero behavior regression**. Phase 1 ends when the
prototype's every capability is reachable through the new CLI and `qa_test.py` is
deleted.

Not a rewrite: `qa_test.py` code moves into modules nearly verbatim. New code is only
the chassis around it (CLI, config, workspace paths, model resolution).

## 3. Deliverables

### 3.1 Package layout

```
pyproject.toml               # package nkqa; console script: qa = nkqa.cli:main
nkqa/
  __init__.py                # __version__
  cli.py                     # argparse subcommands → thin dispatch, no logic
  workspace.py               # find/create workspace; all path constants live HERE
  config.py                  # load config.yaml; defaults; model alias resolution
  models.py                  # role ("planner"/"executor"/"reflector"/"fallback")
                             #   → LLM instance via get_llm_by_name; override chain
  hitl.py                    # ask_human, ask_credential, request_permission,
                             #   _ask_terminal, secrets dict, grants (from qa_test.py)
  execution/
    __init__.py
    runner.py                # record/run flow (qa_test.py record() + greet())
    replay.py                # replay, replay_all, _collect_replay_secrets,
                             #   _resolve_history_file (from qa_test.py)
    evidence.py              # test-dir layout, save_history checkpoint, gif fallback,
                             #   results listing (list_tests, recorded_tests)
  prompts.py                 # QA_RULES system-message extension
docs/                        # this folder
tests/                       # pytest: config parsing, workspace, slugify, CLI dispatch
```

### 3.2 Workspace (`qa init`)

`qa init` creates, in the current directory:

```
config.yaml                  # from template below
appmap/overview.md           # stub with headings (filled in Phase 4)
scenarios/.gitkeep
runs/.gitkeep
.gitignore                   # ignores .env, runs/**/videos (large files)
```

Workspace discovery: walk up from cwd looking for `config.yaml` +
`appmap/` (like git finds `.git`). All commands except `init` require a workspace.
**Migration:** if `qa_output/tests/` exists and `runs/` is empty, offer to move
recordings into `runs/` (keep the permissions file: `qa_permissions.json` moves to
workspace root). Do not silently touch existing data.

### 3.3 `config.yaml` v1 schema

```yaml
app:
  name: My App
  base_url: https://staging.example.com
models:                # roles; only executor+fallback are USED in Phase 1,
  planner:   smart     #   but the full schema ships now so later phases add no
  executor:  fast      #   config migration
  reflector: fast
  fallback:  smart
aliases:
  smart: anthropic_claude_sonnet_5        # names accepted by get_llm_by_name
  fast:  anthropic_claude_haiku_4_5
run:
  max_steps: 30
  headless: false
# mcp:                 # schema reserved; parsed-and-ignored until Phase 3
```

`config.py` must tolerate missing file/keys with sane defaults (prototype behavior).
Alias resolution: role → alias → `get_llm_by_name(model_id)`. Precedence:
config default < `--model <alias|id>` CLI flag < (Phase 5: in-session `/model`).
Verify exact model-name strings accepted by `get_llm_by_name` in browser-use 0.13.8
at implementation time — do not trust the examples above blindly.

### 3.4 CLI commands in Phase 1

| Command | Behavior |
|---|---|
| `qa init` | create workspace (§3.2), offer prototype-data migration |
| `qa run [url] [focus] [--name N] [--model M]` | today's *record* flow: interactive greet, AI-driven test, evidence saved under `runs/<name>/` |
| `qa replay <name> [--var k=v]...` | deterministic re-run, no LLM (prototype `--replay`) |
| `qa replay --all` | suite mode (prototype `--replay-all`) |
| `qa list` | recorded runs table (prototype `--list`) |
| `qa version` | package + browser-use versions |

Notes: `qa run` in Phase 1 is the prototype's freeform record mode. In Phase 2 it
becomes "execute approved scenario" and freeform moves to `qa explore` — name the
internal function accordingly (`run_freeform`) to make that split cheap.
Exit codes preserved: 0 pass, 1 fail, 2 usage/not-found (CI depends on this).

### 3.5 Code migration map (qa_test.py → package)

| qa_test.py | Destination |
|---|---|
| `slugify`, `OUTPUT_DIR`/`TESTS_DIR` constants | `workspace.py` (constants become workspace-relative: `runs/`) |
| `secrets`, `session_grants`, `_load_always_grants`, `_save_always_grant`, `_ask_terminal`, `ask_human`, `ask_credential`, `request_permission`, `tools` | `hitl.py` (wrap in a class or factory so state isn't module-global — two runs in one process must not share secrets) |
| `QA_RULES` | `prompts.py` |
| `recorded_tests`, `list_tests` | `execution/evidence.py` |
| `greet`, `record` (incl. checkpoint/on_step_end, crash-save `finally`, variable-detection hint) | `execution/runner.py` |
| `replay`, `replay_all`, `_collect_replay_secrets`, `_resolve_history_file` | `execution/replay.py` |
| `parse_args`, `main` | `cli.py` (argparse subcommands replace positional/flag soup) |
| hardcoded `fallback_llm=get_llm_by_name('anthropic_claude_haiku_4_5')` | `models.py` via config roles |

Preserve exactly: secret hiding heuristic (`pass/token/secret/otp` → hidden input),
`<secret>key</secret>` placeholder contract, checkpoint-every-step crash safety,
"video finalized only after exit" warning, overwrite-confirmation on duplicate names.

### 3.6 Out of scope for Phase 1 (do not build)

Planner agent, scenario files/schema/approval gate (Phase 2) · MCP client, Jira
(Phase 3) · appmap content, crawl, reflector (Phase 4 — only the stub folder ships) ·
suites/tags, run-comparison, stuck-escalation, `/model` in-session switch (Phase 5) ·
any web UI.

## 4. Definition of done

1. `pip install -e .` in the venv; `qa version` works.
2. In a fresh empty dir: `qa init` → workspace created; `qa run <url> "focus"`
   reproduces the prototype experience end-to-end (greet, HITL tools, evidence in
   `runs/<name>/`: history.json, video, gif, conversation, results).
3. `qa replay <name>` and `qa replay --all` pass on a recording made in (2), and on a
   migrated prototype recording.
4. `--model` flag switches the executor model; config aliases resolve; missing
   config.yaml still works with defaults.
5. `pytest tests/` green (config, workspace discovery, slugify, CLI dispatch,
   migration logic — no live-browser tests required).
6. `qa_test.py` deleted; README updated with the new commands.
7. Committed in reviewable chunks (chassis → migration → CLI → cleanup).

## 5. Implementation order

1. `pyproject.toml` + package + `qa version` (proves install/entry point).
2. `workspace.py` + `qa init` + tests.
3. `config.py` + `models.py` + tests.
4. Move `hitl.py` + `prompts.py` (pure relocation).
5. `execution/` migration (runner, replay, evidence) + `qa run/replay/list`.
6. Prototype-data migration in `qa init`, README, delete `qa_test.py`.

Estimated size: ~600 lines moved, ~400 lines new. No new dependencies beyond
`pyyaml` (config) — keep argparse (no typer) to stay dependency-light, matching the
prototype's style: tabs for indentation, single quotes.
