# Desktop track — progress

Companion to [DESKTOP-PLAN.md](DESKTOP-PLAN.md). One line per milestone; update as they land.

| Milestone | Status | Notes |
|---|---|---|
| D0 spikes | **S1 done · S2 done · S3 pending** | S1: screencast reachable, 20 fps. S2: PyInstaller onedir runs a real browser. S3 (keyring) needs a machine with a keychain — not this container. |
| Contract lock | **done** | `nkqa/ui.py`, `tests/conftest.py` (FakeChannel), registry split in `shell/commands.py`. Frozen — see §9 of DESKTOP-PARALLEL.md before changing. |
| D1 UI channel | **done** | Every module channel-driven; `execution/stream.py` forwards browser-use. CLI output and exit codes verified unchanged. |
| D2 sidecar | **done** | `nkqa/server/` — handshake, token+Origin auth, WS `SocketChannel`, one-job runner, artifact scoping. Protocol frozen in [PROTOCOL.md](PROTOCOL.md). 16 tests. |
| D2b vault | **done** | `nkqa/vault.py` + `vault_commands.py`. Two origin gates, grants in `qa_permissions.json`, env fallback. 20 tests. S3 answered by building it. |
| D3 Tauri shell | **done, and run** | `desktop/` — Tauri v2 + React/TS. Compiled and launched under Xvfb: spawns the bundled sidecar, reads the handshake, connects. |
| D4 views | **done** | Chat · Scenarios (approve gate) · App map · Flows · Runs · Credentials. Driven in a real browser, zero console errors. |
| D4b setup | **done** | Create a workspace from the picker (sidecar `--init`), slash commands + palette in chat, session autonomy selector. |
| D5 live pane | **built, unproven** | Filmstrip + screencast rendering is written; never seen with a real run (needs an API key). |
| D6 ship | **sidecar solved** | PyInstaller onefile verified: 99 MB, 2.4 s to handshake. Signing/notarization still untouched. |

**Phase 6 (parallel track):** `qa suite` and `qa compare` shipped — see below. Stuck-escalation
and selector auto-healing remain open by choice; both need a signal only real runs give.

---

## D0 spike results

### S1 — CDP screencast (`spike_screencast.py`) · **YES, Stage 2 is real**

`Page.startScreencast` is reachable through the CDP session browser-use already holds —
no fork, no private API:

```python
cdp = await session.get_or_create_cdp_session()
cdp.cdp_client.register.Page.screencastFrame(on_frame)      # (event, session_id)
await cdp.cdp_client.send.Page.startScreencast(
    params={'format': 'jpeg', 'quality': 60, 'maxWidth': 1280, 'maxHeight': 800, 'everyNthFrame': 1},
    session_id=cdp.session_id,
)
# ack every frame or Chrome stops sending: Page.screencastFrameAck(sessionId=event['sessionId'])
```

Measured on a page repainting every 50 ms (a deliberate worst case; real apps repaint far
less):

| | |
|---|---|
| frame rate | **20.2 fps** (120 frames in 5.9 s) |
| frame size | 17 KB avg at quality 60 / 1280×800 |
| bandwidth | **~336 KB/s** over the socket — trivial for localhost |
| coexistence | browser-use's own `take_screenshot()` still works during and after ✅ |

**Consequences for the plan:** D5 Stage 2 is no longer conditional — build it. The
estimate of ~10 fps was pessimistic. `everyNthFrame`, `quality` and `maxWidth` are the
three knobs if bandwidth ever matters; frames must be acked or the stream stalls after a
few. Stage 1 (the step filmstrip) is still worth shipping first, because it needs no CDP
plumbing and it is the fallback if a future browser-use release moves this API.

### S2 — PyInstaller + browser-use (`spike_sidecar_entry.py`) · **YES**

`--onedir` with `--collect-all browser_use --collect-all cdp_use --collect-all nkqa`
builds clean, and the frozen binary launches a real browser and drives it over CDP:

```
SPIKE_RESULT {"frozen": true, "nkqa_import": true, "cdp_ok": true, "screenshot_chars": 37685}
```

| | |
|---|---|
| bundle size | **226 MB** (excludes Chromium — that still downloads on first launch) |
| cold start + browser launch + screenshot | 3.2 s total, of which the browser is ~2.3 s |

**Caveat: this was Linux.** It proves the structural question — the heavy dependency tree
survives freezing and a browser still launches from inside the bundle. It says nothing
about the macOS-specific work: arm64, codesigning, hardened-runtime entitlements and
notarization. Those remain the C1 risk, and the signing dry-run should still start early.

### S3 — keyring · **answered by building D2b, and it found a bug**

The container has no Secret Service, which turned out to be the useful case. `keyring`
does **not** raise when there is no usable backend: `keyring.get_keyring()` happily
returns a *fail* backend that raises `NoKeyringError` on the first actual read. So the
obvious availability check passes and the crash lands mid-run, on a CI box, halfway
through a suite.

`KeyringBackend.__init__` therefore probes with a real `get_password` call. Where that
raises, `open_keychain()` returns None and the vault reports
`backend: none (environment variables only)` and falls back to `NKQA_SECRET_*`.

Still worth running on the Mac: confirm the macOS Keychain path stores and reads back, and
see whether the first read after a reboot prompts for the login keychain.

---

## What D1 changed

`nkqa/ui.py` is new and is the contract: `Event` / `Ask` / `Channel`, plus
`TerminalChannel`. Every verb now takes a channel; `print` became `ch.emit`, `input`
became `await ch.ask`. Architecture note in [ARCHITECTURE.md](ARCHITECTURE.md) §10.

Signature changes worth knowing when reading the diff:

- `actions.approve`, `actions.init`, `actions.models`, `actions.list_scenarios`,
  `actions.list_runs` are now **async** (they prompt or emit). `cli.py` wraps them in
  `run_sync`; exit codes are unchanged.
- `ingest.collect_inputs` and `ingest.build_messages` are async — they warn about
  skipped images through the channel.
- `execution.replay.resolve_history_file` is async for the same reason.
- `reflector.reflect` / `auto_reflect`, `planner.plan`, `revise.revise`,
  `crawler.crawl`, `execution.evidence.list_runs` and both runners take `ch`.
- `HumanInTheLoop(permissions_file, channel)` — the channel is the second argument and
  defaults to a `TerminalChannel`, so existing call sites keep working.

New behaviour that only the GUI will see:

- `execution/stream.py` forwards browser-use's `logging` records and bare prints onto
  the channel, and emits a step Event per finished agent step with `screenshot`, `url`
  and `action` in `data`. No-op for `TerminalChannel` (browser-use already writes there,
  and forwarding would double every line).
- Structured `data` payloads on the events that a UI will want as rows rather than text:
  scenario listings, run listings, model roles, verdicts, appmap writes.

## Verified

- `./check.sh` green: ruff, pyright strict, pytest — **81 tests** (74 before, 7 new in
  `tests/test_ui.py`).
- `qa init`, `qa scenarios`, `qa list`, `qa approve` (decline → 1, accept → 0, unknown
  → 2) produce identical output and exit codes to before the refactor.
- Secrets: `test_credential_value_never_reaches_the_channel` asserts a collected value
  lands in `hitl.secrets` and appears in no event, no ask record, and no file on disk.

## What D2 added

`nkqa/server/` — four small modules, and a `nkqa-server` console script:

| module | job |
|---|---|
| `main.py` | binds `127.0.0.1:0`, prints the one-line handshake, hands the socket to uvicorn. Loads the workspace's `.env` **in this process only** — the reason for one process per workspace. |
| `auth.py` | token (`compare_digest`) + Origin allowlist. |
| `channel.py` | `SocketChannel`: `emit` puts a frame on the wire, `ask` sends one and awaits the answer. `abandon()` resolves every pending ask with `''` so a closed window denies rather than hangs. |
| `jobs.py` | one job at a time; cancellation tracked explicitly, because the runners swallow `CancelledError` to save evidence. |
| `app.py` | the routes and the frame loop. Commands are generated from `shell.commands.REGISTRY`. |

Design decision worth knowing: **no `POST /command`.** The plan sketched one, but a
command needs the socket anyway for its events and prompts, so HTTP is reads only and the
socket carries everything that happens. One code path, not two that drift.

Verified beyond the unit tests — the real binary, driven the way Tauri will:

```
handshake: {'ready': True, 'port': 42187, 'token': 'a4dbbd5ed144...', 'workspace': '/tmp/demo'}
health: 0.1.0 browser-use 0.13.8 busy False
workspace: My App | scenarios: [('auth/login', 'ok')] | commands exposed: 17
no-token request rejected: 401
```

Tests (`tests/test_server.py`, 16): token and Origin rejection, WS upgrade refusal, the
read routes, `../` and `.env`-suffix artifact refusals, a full command lifecycle
(`started` → events → `result`), the approve ask round-trip in both directions (approving
flips the file to `ok`; declining leaves it `draft` and exits 1), unknown/shell-only
command refusal, and three job-runner cases including the evidence-saving cancel.

## What D2b added

`nkqa/vault.py` and `nkqa/vault_commands.py`. `vault.yaml` (committed) declares what the
project needs; values live in the OS keychain or `NKQA_SECRET_*`, never in the workspace.

**Two independent origin gates**, which is more than the plan promised — the second one
turned out to be free:

1. **Release** (ours): the value is not even read out of the keychain when the browser's
   current origin does not match. The HITL tool takes `page_url`, which browser-use injects
   into any action that declares it.
2. **Substitution** (browser-use's own): an origin-bound value is held as
   `secrets[origin][name]`, and browser-use only substitutes `<secret>name</secret>` on a
   matching page. So a value already in memory still cannot be typed elsewhere. Origin
   patterns use browser-use's `match_url_with_domain_pattern`, so one syntax across the
   product — and it refuses a scheme downgrade (`https://*.x.com` will not match `http://`).

The release chain in `ask_credential`: session memory → vault under an existing grant →
vault after a four-way ask (once / session / always / deny) → otherwise ask the human, and
offer to save when `vault.yaml` declares it. The agent **cannot enumerate** the vault: there
is no list tool, and `vault-set/rm/grant/revoke` are `human_only`, like `approve`.

Grants live beside the existing permission grants in `qa_permissions.json`, which changed
shape from a bare list to `{"permissions": [...], "credentials": {...}}`. The loader still
reads the old list, and a test pins that.

Verified on the CLI:

```
$ qa vault                    # a fresh clone is told exactly what to supply
NAME            STORED       GRANT        ORIGIN
qa_password     — not set    —            https://*.slrconsulting.com
qa_user         — not set    auth/login   https://dev.sustain.slrconsulting.com
2 credential(s) not set yet:  qa vault set <name>          # exit 1 — usable in CI

$ qa vault get qa_user
qa vault: error: argument action: invalid choice: 'get'    # deliberately does not exist
```

`qa_permissions.json` after granting holds names and scenarios and no values at all.

## Backend work after D2b

Two gaps that would otherwise have blocked the UI. Both are testable without a window, so
they are done and verified.

### Chat persistence — `nkqa/chats.py`

Phase 5 threw the router's messages away every turn. Chats are now JSON in the workspace,
**committed**: the reasoning behind a scenario is worth reviewing in a PR. A turn records
what the agent *did* (command + exit code), not only what it said.

The sidecar gained the frame the Chat view actually needs: `{"type": "say", "text": "..."}`.
Before this the socket could only run *commands* — there was no way to send plain English
at all, which would have blocked D3 on day one. `say` routes through the same agent the
terminal uses, streams events like any job, and can be cancelled. Omit `chat` and the server
opens one and replies with a `chat` frame naming it. Context is capped at 20 turns: the
appmap is the durable memory, not the transcript.

Also `GET /chats` and `GET /chats/{id}`.

### Live view backend — `nkqa/execution/screencast.py`

Wired into both runners, so a run streams its browser to any non-terminal channel. The two
things that make or break a screencast:

- **Every frame is acked, including dropped ones.** Chrome stops sending after a couple of
  unacked frames — the classic "it worked for a second then froze".
- **Frames are shed, not queued.** A slow UI must never slow down the browser it is
  watching.

Measured against a real browser through the real code path:

| consumer | frames | dropped | rate |
|---|---|---|---|
| fast | 100 | 0 | 20.0 fps |
| 0.4s per frame | 24 | 75 | 4.5 fps |

The run itself never slowed down in either case. A browser that will not screencast emits
one `unavailable` progress event and the run carries on with the step filmstrip.

`frame` is a new `EventKind` — a contract change, so PROTOCOL.md is updated. `SocketChannel`
sends it as its own lean message rather than an `event` envelope (at 20 fps the wrapper
costs something); `TerminalChannel` drops it, since base64 JPEG in a terminal is nonsense.

## Phase 6: suites and CI

Unblocked by the vault — without stored credentials an unattended suite stops at the first
login prompt.

`nkqa/suite.py`, plus `qa suite` and `qa compare` on all three surfaces (they come from the
same registry, so the sidecar exposes them for free — the desktop Runs view gets suites with
no extra backend work).

**The suite is the approved set.** A draft was never approved, so it is reported as "not in
the suite" rather than failed on; `--strict` fails on it, which is what a build server wants.
A scenario edited after approval goes STALE and leaves the suite until re-approved. That
keeps the approval gate meaningful instead of turning CI permanently red.

Exit codes: `0` all passed · `1` something failed (or, with `--strict`, something was
excluded) · `2` nothing ran at all — a usage problem, not a passing suite.

Each suite writes `runs/suite--<ts>/suite.{md,json}` and is compared against the previous
one, so the report leads with **newly failing**. `suite.json`'s shape is a contract (a test
pins it) because CI reads it.

`.github/workflows/qa-suite.yml.example` ties it together: the vault's `NKQA_SECRET_*`
fallback, a readiness check (`qa models`, `qa vault`) before anything runs, `--strict`, and
evidence uploaded as an artifact.

**Two bugs found while testing, both with tests now:**

1. Two suites started in the same second produced the same directory name, so the second
   silently overwrote the first's report — and the comparison then had nothing to read.
   `suite_name()` disambiguates.
2. **The sidecar's handshake lied.** `bind()` alone does not queue connections, so between
   printing `{"ready": true}` and uvicorn calling `listen()` there was a window where a
   client got ECONNREFUSED. Tauri would have hit this on every launch that was fast enough,
   and it would have looked like a flaky sidecar. `bind_port()` now listens before the
   handshake is printed, so "ready" means ready.

Integration-checked against the running sidecar: `suite` and `compare` appear in the
registry-generated command list with no extra wiring, run over the WebSocket, and return
the right exit codes — connecting immediately after the handshake with no sleep.

Deliberately *not* built: stuck-escalation and selector auto-healing. ARCHITECTURE §6 says
to design the "am I stuck?" signal after observing real runs, and that is still true — I
would be guessing at a threshold I cannot validate here, and a bad one silently doubles
model cost.

## D3/D4: the desktop app

`desktop/` — Tauri v2 shell + React/TypeScript frontend. **Compiled and launched**
(`cargo build`, run under Xvfb): it read the recent workspace, spawned the bundled
PyInstaller sidecar, parsed the handshake, connected over HTTP and the WebSocket, and
rendered the workspace. The frontend was separately driven in headless Chromium against a
live sidecar — every view, and the approve gate in both directions — with zero console
errors.

`src-tauri` owns only the sidecar's lifecycle. **Nothing is proxied through Rust**: every
read, command and prompt goes straight from the webview to the sidecar, so there is one
implementation of the protocol and the same frontend runs in a plain browser. That is what
makes the UI testable without a Rust toolchain — and it is how the bugs below were found.

### Four bugs that only appear when you actually run it

1. **CORS.** The sidecar never sent `Access-Control-Allow-Origin`, so every fetch from the
   UI was blocked before the token was even looked at. This would have hit Tauri
   identically — the webview origin is cross-origin to `127.0.0.1:<port>`. The D2 tests
   passed because `TestClient` does not enforce CORS. Fixed, with tests for an allowed and
   an unknown origin.
2. **PyInstaller `--onedir` cannot be a Tauri sidecar.** It produces the executable plus a
   204 MB `_internal/` sibling it cannot run without, and `externalBin` copies one file.
   Spike S2 "proved" onedir worked — it did, standalone; it does not as a sidecar.
   `--onefile` it is: 99 MB, 2.4 s to handshake, measured.
3. **PyInstaller does not collect fastapi/uvicorn/keyring/pydantic/starlette.** S2 predated
   the server module, so its build command was silently out of date: the binary builds fine
   and dies on first launch. uvicorn also needs its `*.auto` implementations named
   explicitly. The full command is in `desktop/src-tauri/binaries/README.md`.
4. **A failed auto-connect was swallowed in silence** — the app just sat on the picker with
   no reason given. Now it surfaces the error.

Also worth knowing: a plain `cargo build` does not place the sidecar next to the executable
(only `tauri build`/`tauri dev` do), and `frontendDist` is embedded at compile time, so a
frontend change needs a Rust rebuild when running the binary directly.

### Not yet proven

The live pane has never rendered a real run — that needs an API key and a real browser
session. The filmstrip and screencast paths are written and the backend is tested, but
nobody has watched it work.

## Carried forward

1. **Build the UI from `GET /workspace`'s `commands`**, not from hard-coded names — that
   is what keeps the three surfaces in step.
2. **`approve` needs the body visible and a second click.** It arrives as an `ask` with
   `kind: confirm` and the whole scenario in `body`. Do not reduce it to a chip.
3. **Treat a `secret` ask as write-only in the frontend**: send the value, clear the
   field, never put it in app state.
4. **Trust `result.cancelled`, not the exit code** — a cancelled run still returns a code
   because it saved its evidence first.
5. **The `data` payload on events is there so the UI renders rows, not parsed strings.**
   The table in PROTOCOL.md §5 lists what each carries.
6. **The Chat view sends `say`, not `command`.** It gets a `chat` frame back naming the
   conversation; pass that id on subsequent messages to continue it.
7. **The Live pane renders `frame` messages**, and should expect gaps — frames are dropped
   when it falls behind, by design. The bracketing `progress` events tell it when the
   stream is on, off, or unavailable.
