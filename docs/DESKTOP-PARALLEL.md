# Desktop track — parallel work breakdown

Companion to [DESKTOP-PLAN.md](DESKTOP-PLAN.md), which is approved. That document says
*what* to build; this one says how to split it across agents working at the same time
without them destroying each other's work.

Written 2026-09-04.

---

## 0. The one rule that makes this work

**Agents are assigned files, not tasks.** Two agents given "make the runner emit events"
and "make actions emit events" will both open `actions.py` and one of them loses an
afternoon. Every track below owns an explicit, disjoint set of paths. An agent that
needs a change outside its ownership *requests* it (§9) rather than making it.

The corollary: the seams between those files must exist **before** anyone starts. That's
Wave 0.5, it is genuinely blocking, and it is worth doing slowly.

## 1. Why D1 can't just be split six ways

The channel refactor (`print`/`input` → `Channel`) touches `actions.py`, `hitl.py`, all
of `execution/`, `planner`, `reflector`, `ingest`, `revise`, `crawler`, `jira`, `cli.py`
and `shell/`. It is one conceptual change smeared across the whole codebase — the classic
worst case for parallel work, because every agent needs the same new type and every
agent's tests fail until everyone is done.

Three things make it tractable:

1. **Freeze `nkqa/ui.py` first**, types only, merged to `main` before anyone branches.
   Then the shared thing everyone depends on stops moving.
2. **Ship a `FakeChannel` in `tests/conftest.py` at the same time.** Every agent can then
   test its own modules in isolation, green, without the rest of the refactor existing.
3. **Split `shell/commands.py`'s registry** so each module contributes its own command
   list (§3). Today it is one 260-line array that six agents would all need to edit.

With those three in place the refactor parallelizes across four agents cleanly, and —
the real prize — **the server and the entire frontend can be built simultaneously
against the frozen protocol, before the refactor is finished at all.**

## 2. Dependency graph

```
  WAVE 0 — spikes (day 1, fully parallel, throwaway code)
  S1 CDP screencast    S2 PyInstaller+Playwright    S3 keyring x-platform
        │                        │                        │
        └────────────────────────┴────────────────────────┘
                                 ▼
  WAVE 0.5 — CONTRACT LOCK (one agent, ~half a day, BLOCKING, merged to main)
  nkqa/ui.py types · nkqa/vault.py types · docs/PROTOCOL.md · FakeChannel · registry split
                                 │
        ┌───────────┬────────────┼────────────┬───────────┬───────────┐
        ▼           ▼            ▼            ▼           ▼           ▼
  WAVE 1 (parallel, ~3–4 days)
  A1 channel   A2 verbs    A3 execution   A4 hitl+vault   A5 server   A6 tauri shell
  + terminal   + knowledge  + browser-use                  (FakeChannel) (mock server)
        └───────────┴────────────┴────────────┴───────────┘           │
                                 ▼                                     │
                        INTEGRATION GATE #1  ◀──────────────────────────┘
                    (one agent, ~1 day: real end-to-end run from the GUI)
                                 │
        ┌───────────┬────────────┼────────────┬───────────┬───────────┐
        ▼           ▼            ▼            ▼           ▼           ▼
  WAVE 2 (parallel, ~4–5 days)
  B1 scenarios  B2 appmap   B3 runs view   B4 credentials  B5 chat    B6 live pane
     view        + flows     + evidence      view          persistence  (needs S1)
        └───────────┴────────────┴────────────┴───────────┘           │
                                 ▼                                     │
  WAVE 3 — ship (~4–5 days)  ◀────────────────────────────────────────┘
  C1 packaging + signing + updater   C2 e2e tests + docs   (C1 can start in Wave 1)
```

## 3. What the contract lock must produce

One agent, on `main`, before anyone else branches. Types and schemas only — **no
implementations**, so there is nothing to disagree with later.

| Artifact | Contents |
|---|---|
| `nkqa/ui.py` | `Event`, `Ask`, `Channel` protocol exactly as DESKTOP-PLAN §2.1. Plus the full `EventKind` literal set and the `Ask.kind` set — adding a kind later means touching every agent. |
| `nkqa/vault.py` | `VaultBackend` protocol (`get`/`set`/`delete`/`list_names`), `CredentialSpec`, `Grant`. No keychain code. |
| `docs/PROTOCOL.md` | Every HTTP route with request/response shapes, and every WS frame from DESKTOP-PLAN §5.2–5.3, with a worked example of each. **This is what lets the frontend start on day two.** |
| `tests/conftest.py` | `FakeChannel`: records emitted events, answers `ask()` from a scripted queue, raises on an unexpected ask. Every Wave 1 agent tests against it. |
| `nkqa/shell/commands.py` | Registry split: `COMMANDS` becomes the concatenation of module-local lists (`ACTION_COMMANDS`, `VAULT_COMMANDS`, `SHELL_COMMANDS`…). The file stops being a merge hotspot. |
| `nkqa/server/__init__.py` | Empty package + route stubs returning `501`, so A5 and A6 agree on paths from the start. |

Definition of done: `./check.sh` green, merged to `main`, and **announced as frozen**.
Changing anything here afterwards is a §9 protocol event, not a commit.

## 4. Wave 0 — spikes (day 1)

Throwaway code, one question each, answered in prose. Run all three at once.

- **S1 — CDP screencast.** Can we reach the Playwright page / CDP session through the
  installed browser-use and run `Page.startScreencast` alongside its own screenshotting?
  Deliverable: a script that saves 30 frames, plus a yes/no. Gates B6 and D5 scope.
- **S2 — PyInstaller + Playwright.** Does a `onedir` build of a trivial browser-use
  script launch Chromium on a clean machine? Deliverable: a working `.spec` and the
  measured cold-start time. Gates C1.
- **S3 — keyring across platforms.** Does `keyring` behave on macOS, Windows and a
  headless Linux container, and what exactly does it raise when there's no backend?
  Deliverable: the exception types the env fallback must catch. Gates A4.

## 5. Wave 1 — six agents, ~3–4 days

Exclusive ownership. Nothing outside the list.

### A1 — Channel core & terminal surface
**Owns:** `nkqa/ui.py` (implementations), `nkqa/shell/render.py`, `nkqa/shell/session.py`, `nkqa/cli.py`
**Builds:** `TerminalChannel` — `emit` renders each `EventKind` (the ANSI/table helpers move behind it), `ask` wraps the existing `ask_terminal` with `getpass` for `kind='secret'`. Updates the session loop and argparse dispatch for the now-async `approve`/`file_bug`.
**Done when:** golden-output tests prove `qa` prints byte-identically to today; exit codes unchanged.
**Why first among equals:** everyone else's work is invisible until this renders it. If one agent is ahead of the pack, make it this one.

### A2 — Verbs & knowledge modules
**Owns:** `nkqa/actions.py`, `planner.py`, `reflector.py`, `ingest.py`, `revise.py`, `crawler.py`, `jira.py`, `scenarios.py`
**Builds:** `ch: Channel` threaded through every function; `print` → `ch.emit`, `input` → `await ch.ask`. `approve` becomes an `Ask(kind='confirm', body=<scenario text>)`; `file_bug`'s preview likewise.
**Done when:** every module is channel-driven and tested against `FakeChannel` with no terminal in the loop.

### A3 — Execution & browser-use plumbing
**Owns:** `nkqa/execution/**`
**Builds:** channel threading; **per-step events carrying the screenshot path** (B6 and B3 both consume these, so publish the exact `data` shape early); the `logging.Handler` that forwards browser-use records; the `redirect_stdout` shim for its bare prints; cancellation via task-cancel with partial evidence preserved.
**Done when:** a cancelled run leaves complete evidence (test), and a run against `FakeChannel` emits a step event per action.
**Highest-risk track** — it is the one touching a third-party library's internals. Staff it accordingly.

### A4 — HITL & vault
**Owns:** `nkqa/hitl.py`, `nkqa/vault.py` (implementations), `nkqa/vault_commands.py` (new)
**Builds:** HITL tools over `ch.ask`; keychain backend + env fallback per S3; `vault.yaml` parsing; grants in `qa_permissions.json`; the four-step resolution chain in `ask_credential`; origin binding; the `qa vault` CLI commands as `VAULT_COMMANDS`.
**Done when:** the redaction tests from DESKTOP-PLAN §6.6 all pass — no value in history, transcripts, events, logs or bugs; origin mismatch denies and is recorded.

### A5 — Sidecar server
**Owns:** `nkqa/server/**`
**Builds:** handshake line on stdout, ephemeral 127.0.0.1 bind, token + Origin checks, `/health`, `/workspace`, `/scenarios`, `/runs`, `/artifacts` (with path scoping), `/command`, `/cancel`, the WS loop and `SocketChannel`, one-job-at-a-time queue.
**Does not wait for A2/A3.** Develops against `FakeChannel` and a stubbed action layer.
**Done when:** a full command lifecycle — including an `ask` round-trip and a cancel — is drivable from `websocat` with no UI and no real browser.

### A6 — Tauri shell & frontend skeleton
**Owns:** `desktop/**`
**Builds:** Tauri v2 + React + TS scaffold, workspace picker and registry, sidecar spawn/kill lifecycle, the WS client, the project shell (sidebar + routing), and the three HITL modals (question / credential / permission).
**Does not wait for anything but `docs/PROTOCOL.md`.** Ships a `desktop/mock-server/` that replays canned frames — build the whole UI against it, including a fake run that streams fake steps.
**Done when:** the mock drives every screen, and the HITL modals round-trip answers.

> Also startable now: **C1 packaging** (§7). It needs only S2 and the current CLI, and
> the signing/notarization dead ends are much cheaper to hit in week one than week five.

## 6. Integration gate #1 (~1 day, one agent)

Not a merge — a milestone with a demo. Wave 1 branches land on `integration`, then:
swap A6's mock for A5's real server, swap A5's stub actions for A2/A3, run a real
scenario end-to-end from the GUI with a real credential prompt. Fix the seams that
inevitably don't line up. **Nobody starts Wave 2 until a run completes in the window.**

Expect this to surface two or three protocol gaps. That is the gate doing its job.

## 7. Wave 2 — six agents, ~4–5 days

Frontend tracks own a component directory each; backend counterparts are small.

| Agent | Owns | Builds |
|---|---|---|
| **B1** scenarios | `desktop/src/views/scenarios/**` | List with status chips, scenario body, revise, and approve — full body visible plus a deliberate second confirm, per the invariant. A stale scenario shows a diff against the approved hash. |
| **B2** app map | `desktop/src/views/appmap/**` | Markdown render + edit for `overview.md`, `pages/`, `flows/`; Flows promoted to its own view; save writes back through `/command`. |
| **B3** runs | `desktop/src/views/runs/**` | Run list, `results.md`, per-step evidence, gif and video playback via `/artifacts`. |
| **B4** credentials | `desktop/src/views/settings/**` | Vault UI: names, descriptions, origins, grants, last-used; Set / Rotate / Revoke. **No reveal.** |
| **B5** chat | `nkqa/chats.py`, `desktop/src/views/chat/**` | `chats/` persistence, history threaded into `shell/agent.route`, context capping, the transcript view showing commands as turns. |
| **B6** live pane | `desktop/src/views/live/**`, `nkqa/execution/screencast.py` | Stage 1 filmstrip from A3's step events; Stage 2 screencast if S1 said yes. |

B5 is the only Wave 2 track with meaningful backend work; if you are short an agent,
merge it into B1's scope rather than dropping the filmstrip.

## 8. Wave 3 — ship (~4–5 days)

- **C1 packaging** — `scripts/**`, `.github/workflows/**`, PyInstaller spec, Chromium
  first-launch flow, codesign + notarize, Windows MSI, Tauri updater. *Started in Wave 1;
  finished here.* Owns `tauri.conf.json` bundle keys by request to A6 (§9).
- **C2 hardening** — end-to-end tests, the redaction test suite run against the real
  stack, README + ARCHITECTURE updates, a clean-machine install rehearsal.

## 9. Coordination mechanics

- **One git worktree per agent**, branch `desktop/<agent-id>`: `git worktree add
  ../nkqa-a3 -b desktop/a3`. Separate checkouts mean separate venvs and no half-written
  file from another agent breaking your test run.
- **Rebase onto `integration` daily.** Not weekly. A three-day-old branch in a refactor
  this wide is a bad afternoon.
- **`./check.sh` green before every push.** Non-negotiable — ruff, pyright strict,
  pytest. A red push blocks five other agents.
- **Contract changes are a protocol event.** An agent that needs a new `EventKind` or a
  route shape change opens a one-paragraph note in `docs/PROTOCOL.md` under
  `## Proposed`, and the coordinator merges it to `main` and tells everyone to rebase.
  No agent silently widens a shared type.
- **Cross-boundary edits are requests, not commits.** Need `actions.py` changed and
  you're A3? Ask A2. If that's blocking, stub it locally and mark it `# XXX contract`.
- **A human reviews every merge to `integration`.** Six agents can write faster than one
  person can read; the approval gate is the product, so its code in particular gets read
  by you, not by another agent.
- **Commits carry no AI attribution** — `CLAUDE.md` rule, and it applies to every agent
  in this fleet.

## 10. Critical path & what to actually expect

```
S1–S3 spikes        1d   ────
contract lock       0.5d     ──
Wave 1              3–4d       ────────────
integration gate    1d                     ───
Wave 2              4–5d                      ─────────────
Wave 3              4–5d                                   ─────────────
                                                    ≈ 2.5–3 weeks wall clock
```

Serial, DESKTOP-PLAN estimated 5–6 weeks. Six agents does **not** buy 6× — it buys a bit
over 2×, because the contract lock is serial, the integration gate is serial, and your
review time is a fixed cost that doesn't parallelize at all. Anyone promising more than
that is not counting the merges.

The two places the schedule actually breaks: **A3** (browser-use internals may not
cooperate) and **C1** (first notarization is never the happy path). Both have a spike or
an early start above for exactly that reason.

## 11. Do not parallelize these

- **The contract lock.** One agent, one voice. A committee designs a worse `Event`.
- **The approval-gate code path.** One agent, and you read the diff yourself. It is the
  product's trust story; it does not get built by whoever had capacity.
- **The vault's release logic** (§6.3–6.4). One agent owns the chain end to end — a
  credential check split across two agents' assumptions is exactly how a bypass appears.
- **Integration gates.** Serial by definition; making them parallel just moves the
  breakage later.
