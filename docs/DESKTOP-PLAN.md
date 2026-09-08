# Desktop Plan — NKQA as a Tauri application

Status: agreed plan, not yet started · Written 2026-09-04
Read [ARCHITECTURE.md](ARCHITECTURE.md) first. Phases 1–5 are shipped; Phase 6 (suites,
run-over-run comparison, stuck-escalation) is untouched and **parallel** to this track.
Self-contained so a future session can execute it cold.

---

## 0. Why this exists

The CLI demo landed, and the people who saw it asked for a desktop app. That is a
distribution problem, not a product problem: the loop (learn → plan → approve → run →
reflect) is right; what stops a QA lead adopting it is `python -m venv`, `.env`, and a
terminal. A double-clickable app that opens a project, shows the app map, and streams a
live run is the same product with the install cost removed.

**Decisions taken by the user (2026-09-04):**

1. **Tauri shell + Python sidecar over HTTP/WebSocket.** Not PyO3, not stdio — a real
   local server we can also curl while debugging.
2. **CLI stays first-class.** CLI, interactive shell, and desktop become three thin
   surfaces over one core. No fork, no rewrite later.
3. **Bundle everything.** Python sidecar ships inside the app; Chromium downloads on
   first launch. The target user has neither installed.
4. **Live browser view in-app** is in scope (staged — see §8). The run should be
   watchable inside the window, not in a stray Chrome that pops to the front.
5. **A credential vault** (§6): store credentials securely once, and release them to a
   run only under a permission the human granted.

---

## 1. Invariants (carried forward, and what the GUI adds)

Everything Phases 1–5 rest on stays true, and the GUI makes two of them *harder*, so
they are called out explicitly:

- **The agent can never approve.** `approve` stays `human_only` and absent from the
  routing agent's tool list. In the desktop this means: the approve button lives in the
  scenario view, shows the full scenario body (and a diff when stale), and takes a
  deliberate second click. A one-click "Approve" chip in a toast is a regression of the
  product's trust story — do not ship one.
- **Secrets never reach the model, and never sit in plaintext.** Note this is a
  *deliberate revision* of the Phase 1–5 invariant, which was the stronger "secrets are
  never written to disk at all". The vault (§6) persists values — but in the OS
  keychain, never in the workspace, released only under an explicit human grant, and
  still handed to the browser exclusively through the `<secret>key</secret>` placeholder:
  the LLM sees the placeholder, never the value. That last clause is the part that must
  never move. The desktop also makes transport riskier (a credential crosses a socket and
  passes through a JS frontend), so §5.3 is the wire contract. A test must assert that no
  emitted event, log line, run history, or persisted chat transcript ever contains a
  credential value.
- **CLI surface and exit codes unchanged.** `qa <subcommand>` keeps argparse and 0/1/2.
  CI depends on it; tests assert it.
- **The workspace is still the database.** The desktop reads and writes the same plain
  files. Nothing about a project may become GUI-only state. If someone deletes the app
  and clones the repo, everything still works from the terminal.

---

## 2. The actual blocker: `print()` and `input()` are the API

Every module talks to the human directly. `actions.py` prints tables and calls
`input()` for the approve y/N and the file-bug confirmation. `hitl.py` calls
`getpass`/`input` through `ask_terminal`. `runner.greet` interviews the user with three
`input()` calls. `execution/*` prints progress. `browser-use` itself logs a wall of text
to stdout.

None of that reaches a window. **The first and largest piece of work is not Tauri — it
is extracting a UI channel.** Do this before writing a line of Rust; it is independently
valuable (it makes the CLI testable) and it is what keeps the two surfaces from forking.

### 2.1 `nkqa/ui.py` — the channel

```python
type EventKind = Literal['log', 'step', 'verdict', 'artifact', 'progress', 'table', 'done']

@dataclass
class Event:
	kind: EventKind
	text: str = ''
	data: dict[str, Any] = field(default_factory=dict)

@dataclass
class Ask:
	kind: Literal['text', 'secret', 'confirm', 'choice']
	prompt: str
	key: str = ''            # credential name, permission key, scenario id
	options: list[str] = field(default_factory=list)
	body: str = ''           # long content to show (scenario file, bug preview)

class Channel(Protocol):
	async def emit(self, event: Event) -> None: ...
	async def ask(self, request: Ask) -> str: ...
```

Two implementations:

- `TerminalChannel` — exactly today's behaviour. `emit` prints (the ANSI helpers in
  `shell/render.py` move behind it); `ask` is the existing `ask_terminal` with
  `getpass` for `kind='secret'`. **Zero user-visible change to the CLI.**
- `SocketChannel` — `emit` pushes a JSON frame down the WebSocket; `ask` allocates a
  correlation id, sends an `ask` frame, and awaits an `asyncio.Future` resolved when the
  matching `answer` frame arrives. Cancellation resolves it with `''` (which every
  existing call site already treats as "no value / denied").

### 2.2 Threading it through

`Channel` is passed, never global. Signature-wise this is the same shape `Config` and
`HumanInTheLoop` already have:

- `HumanInTheLoop.__init__(permissions_file, channel)` — `ask_human`,
  `ask_credential`, `request_permission` become `channel.ask(...)`. Permission choice
  becomes `kind='choice'` with `['once', 'session', 'always', 'deny']` instead of
  parsing `y/s/a/n`.
- `actions.*` take `ch: Channel`; every `print` becomes `ch.emit`, every `input`
  becomes `await ch.ask`. This makes `approve` and `file_bug` async — update `cli.py`
  dispatch accordingly.
- `execution/`, `planner`, `reflector`, `crawler`, `ingest`, `revise`, `jira`: same
  substitution. `runner.greet` becomes three `ch.ask` calls (or is skipped entirely
  when the desktop already collected url/focus/name in a form).
- `shell/render.py` becomes the terminal *formatter* for events, not a printer.

### 2.3 browser-use's own output

browser-use logs through `logging` and prints agent thinking to stdout. In the terminal
that is the show; in the desktop it vanishes. Install a `logging.Handler` per run that
forwards records to the channel as `Event(kind='log')`, and set
`BROWSER_USE_LOGGING_LEVEL` appropriately. Anything browser-use writes with bare `print`
gets captured with `contextlib.redirect_stdout` into a line-splitting shim for the
duration of the run.

**Do not skip this** — without it the desktop shows a spinner for two minutes while the
terminal would have shown twenty steps of reasoning. It is the single biggest difference
between a demo that lands and one that doesn't.

### 2.4 Cancellation

Ctrl+C is gone; a Stop button sends `{type:'cancel', id}`, which cancels the
`asyncio.Task` running the command. The existing `except (KeyboardInterrupt,
asyncio.CancelledError)` / `finally` blocks in `execution/runner.py` already save
partial evidence — verify `scenario_runner.py` and `crawler.py` do the same, and add
tests.

---

## 3. Target architecture

```
┌─────────────────────────────── Tauri window (Rust + WebView) ───────────────────────┐
│  React/TS UI                                                                        │
│  ├── Workspace picker (recent projects, New / Open)                                 │
│  └── Project shell                                                                  │
│        sidebar: Chat · Scenarios · App Map · Flows · Runs · Settings                │
│        main:    view for the selected section                                       │
│        right:   Live pane (browser frames + step stream)  ◀── the thing they asked  │
└───────────────┬─────────────────────────────────────────────────────────────────────┘
                │  http://127.0.0.1:<ephemeral>  +  ws://…/session/<id>   (token-auth)
┌───────────────▼─────────────────────────────────────────────────────────────────────┐
│  nkqa-server  (Python sidecar — ONE PROCESS PER OPEN WORKSPACE, see §4.3)            │
│  server/app.py     FastAPI: sessions, commands, artifacts, health                   │
│  server/socket.py  WS frame loop -> SocketChannel                                    │
│  server/jobs.py    one command task at a time per workspace + cancellation           │
│  ─────────────── everything below is today's code, unchanged in shape ───────────── │
│  actions · shell/agent · planner · execution · reflector · hitl · mcp · workspace    │
└─────────────────────────────────────────────────────────────────────────────────────┘
                │ Playwright / CDP
        ┌───────▼────────┐
        │ Chromium       │  headless when the Live pane is on; frames streamed back
        └────────────────┘
```

`qa` the CLI keeps calling the same `actions.*` with a `TerminalChannel`. Nothing about
the sidecar exists in its import path.

---

## 4. Project & workspace model

### 4.1 What a project is

Unchanged: a directory with `config.yaml` + `appmap/`. `workspace.find()` already walks
up like git. The desktop adds a **registry** of them so the app can show recents:

```
<app-data>/nkqa/workspaces.json     # [{path, name, last_opened}]
<app-data>/nkqa/settings.json       # theme, default models, telemetry off
```

(`~/Library/Application Support/nkqa` on macOS, `%APPDATA%\nkqa` on Windows —
Tauri's `app_data_dir` gives the right path per OS.)

### 4.2 Workspace layout after this track

```
config.yaml            # unchanged
.env                   # unchanged; written by the setup wizard
vault.yaml             # NEW — declares which credentials this project needs (§6.2).
                       #   Committed. Names, descriptions, origins. Never values.
appmap/
  overview.md          # "App Map" tab
  pages/*.md           # "App Map" tab
  flows/*.md           # "App Flows" tab   ← promote to a first-class view
  learned.json
scenarios/<area>/*.md  # "Scenarios" tab: status chips, approve, revise
runs/<name>/           # "Runs" tab: results.md, gif, video, per-step evidence
chats/<id>.json        # NEW — persisted conversations (§6)
.nkqa/                 # NEW — desktop-only, gitignored: ui state, last open chat
qa_permissions.json    # unchanged
```

Add `chats/` and `.nkqa/` to `workspace.create()` and `.nkqa/` to the `GITIGNORE`
constant. `chats/` **is** committed — the reasoning behind a scenario is worth reviewing
in a PR.

### 4.3 One sidecar process per workspace — and why

Tempting to run one server for everything. Don't, for one concrete reason: **API keys
come from `.env` via `load_dotenv()`, which mutates the global `os.environ`.** Two
workspaces with different keys in one process silently cross-contaminate, and
`resolve_llm` reads the environment. Same story for MCP server subprocesses and
`~/.mcp-auth`.

One process per open workspace gives us env isolation, crash isolation (a browser-use
explosion kills one project's server, not the app), and a trivially correct
"one run at a time" rule. Tauri spawns it on workspace open and kills it on close.

Cost: ~80MB RSS per open project. Acceptable; cap the app at, say, 4 open projects.

---

## 5. The bridge protocol

### 5.1 Handshake

Tauri spawns `nkqa-server --workspace <path> --port 0`. The server binds an **ephemeral
port on 127.0.0.1 only**, then writes one line of JSON to stdout:

```json
{"ready": true, "port": 51734, "token": "<32 random bytes, hex>"}
```

Tauri parses that line and uses it for every subsequent call. If no ready line arrives
in 15s, show a diagnostics screen with the sidecar's stderr — do not fail silently.

### 5.2 Surface

```
GET  /health                     -> {version, browser_use_version, workspace, models_ok}
GET  /workspace                  -> config, appmap file tree, scenarios, runs (the banner data)
GET  /scenarios/{id}             -> frontmatter + body + runnable state + last verdict
GET  /runs/{name}                -> results.json/md, step list, artifact URLs
GET  /artifacts/{path}           -> video/gif/screenshot, path-scoped to the workspace root
POST /command                    -> {name, args} -> {job_id}   (mirrors shell/commands REGISTRY)
POST /cancel/{job_id}
WS   /session/{session_id}       -> the live channel
```

Reuse `shell/commands.REGISTRY` verbatim for `/command`: one registry already generates
the slash parser and the agent's tool list; the HTTP surface becomes the third generated
consumer, and a new command shows up in all three at once. `human_only` commands are
served but flagged so the frontend renders them as explicit human actions.

### 5.3 WebSocket frames

Client → server:

```json
{"type": "command", "id": "c1", "name": "run", "args": {"id": "checkout/coupon"}}
{"type": "answer",  "id": "a7", "value": "hunter2"}
{"type": "cancel",  "id": "c1"}
```

Server → client:

```json
{"type": "event",  "job": "c1", "kind": "step", "text": "Clicked 'Apply coupon'", "data": {"n": 3, "screenshot": "/artifacts/..."}}
{"type": "ask",    "id": "a7", "kind": "secret", "key": "password", "prompt": "QA agent needs \"password\" to continue"}
{"type": "result", "job": "c1", "code": 0}
```

**Security rules, non-negotiable:**

- Bind `127.0.0.1` only. Never `0.0.0.0`. A local port is reachable by every browser tab
  on the machine; the token is what stops a malicious page talking to it.
- Require the token on the WS upgrade and every HTTP call; reject on mismatch.
- Check `Origin` on upgrade — reject anything that isn't the Tauri app origin.
- `kind: 'secret'` answers go straight into `hitl.secrets` and are **never** echoed in
  an event, never written to the chat transcript, never logged. The frontend must treat
  the input as write-only: send it and clear the field, never put it in app state.
- `/artifacts` resolves the requested path and rejects anything that escapes the
  workspace root (`Path.resolve().is_relative_to(ws.root)`).

Test that a secret typed into `ask_credential` appears in no persisted file and no
emitted frame. That test is the security invariant made executable.

---

## 6. Credential vault

Today `ask_credential` asks the human every single run. Fine for a demo, fatal for a
suite: `qa replay --all` in CI cannot answer a password prompt, and a QA lead re-typing
the same login twelve times a day stops using the product by Thursday. The vault stores
a credential once and releases it to a run only under a permission a human granted.

### 6.1 Where values live: the OS keychain

macOS Keychain, Windows Credential Manager, libsecret on Linux — reached through the
`keyring` package. Service `nkqa:<workspace-id>`, account = credential name.

Why the keychain and not an encrypted file in the workspace: no master password to
invent and re-type, no cryptography we own and have to get right, OS-level protection at
rest, and the OS handles the lock/unlock prompt. And the workspace is git-tracked and
meant to be shared — an encrypted blob sitting in it is a permanent invitation to commit
a passphrase next to it.

Workspace identity is a uuid in `.nkqa/id` (gitignored), so two clones of the same repo
on one machine don't collide and renaming the folder doesn't orphan the entries.

**Headless fallback** (CI, a Linux box with no Secret Service): read
`NKQA_SECRET_<NAME>` from the environment; if that's absent too, fall back to today's
interactive prompt. Deliberately no home-rolled encrypted-file backend — that is a
master-password UX and a crypto surface, in exchange for a case the environment already
solves.

### 6.2 What the workspace stores (and commits)

Metadata and grants only, never values:

```yaml
# vault.yaml
credentials:
  qa_user:
    description: Sustain QA account email
    origin: https://dev.sustain.slrconsulting.com
  qa_password:
    description: password for qa_user
    origin: https://dev.sustain.slrconsulting.com
```

This makes a workspace self-describing: clone it, run `qa vault status`, and it tells
you exactly which values you must supply before anything can run. That is also the
answer to the team case in `OPEN-QUESTIONS` §3.1 — the *shape* of the credentials is
shared and reviewable in a PR, the values never leave each engineer's machine.

Grants go in the existing `qa_permissions.json` under a `credentials` key, next to the
`request_permission` grants already stored there:

```json
{"credentials": {"qa_password": {"scope": "always", "scenarios": ["auth/login"]}}}
```

### 6.3 How a run gets a value

`ask_credential(name)` becomes a resolution chain; the vault is a new first step, and
everything after it is what already exists:

1. **Session memory** (`hitl.secrets`) — already collected this session, use it.
2. **Vault, if a grant covers this credential for this scenario** — read from the
   keychain into `hitl.secrets`, emit `Event('log', 'using stored credential
   "qa_password"')`. The *name*, never the value.
3. **Vault, no grant** — ask the human: allow once / this session / always / deny. That
   is the exact four-way choice `request_permission` already implements, so this is
   wiring, not new machinery. "Always" writes a grant, never a value.
4. **Not in the vault** — today's prompt, plus an offer to save the answer to the vault.

Step 3 is the "as per the agent's permission" part: the agent asks for a *named*
credential, a human decides whether that name is released for that scenario, and the
agent receives a placeholder either way.

**The agent cannot enumerate the vault.** There is no `list_credentials` tool and there
will not be one. It can only request a name it already knows from the scenario or the
appmap — so a confused or manipulated agent cannot go fishing for what else is in there.

### 6.4 Origin binding — the control that matters most

This agent drives a browser and acts on instructions from pages it reads. Every entry
carries an `origin`, and the runtime refuses to release the value when the browser's
current origin doesn't match. A run that gets redirected — by a bug, a stale link, or a
prompt-injected page — cannot type production credentials into somebody else's form.
Subdomain wildcards (`*.slrconsulting.com`) are allowed; a bare `*` is not.

It is perhaps thirty lines of code, and it is the difference between "we store
passwords" and "we store passwords responsibly". Do not defer it to a later milestone.

### 6.5 Surfaces

```
qa vault status                 # what this project needs · what's set · what's granted
qa vault set <name>             # hidden prompt, then store
qa vault rm <name>
qa vault grant <name> [--scenario ID]
qa vault revoke <name>
```

There is deliberately **no `qa vault get`**, and `set` never accepts the value as an
argv (shell history). A value leaves the keychain only into a running browser.

Desktop: Settings → Credentials, listing name, description, origin, grants and
last-used, with Set / Rotate / Revoke. **Recommendation: no "reveal" in v1.** Nobody
actually needs it, and it is the single feature that converts a leak into a catastrophe.
If it is ever added, it requires OS re-auth (Touch ID / Windows Hello) and is logged.

### 6.6 Invariants (write a test for each)

- A value never appears in `history.json`, `conversation/`, `results.md`, chat
  transcripts, event frames, logs, screenshots, or a filed Jira bug. browser-use already
  redacts `sensitive_data` from history — **assert it, don't assume it.**
- The LLM receives `<secret>name</secret>`. Unchanged contract, and the reason the rest
  of this section is safe.
- No grant, no release. Revoking takes effect on the next `ask_credential`, not the next
  app restart.
- Origin mismatch denies, and the denial is recorded in the run report rather than
  swallowed.

### 6.7 When it lands

It needs the channel (D1) for its prompts and the desktop (D4) for management, but the
resolution chain and the CLI can ship the moment D1 is done — and the day they do,
`qa replay --all` becomes viable unattended, which Phase 6 wants anyway. Scheduled runs
are impossible without this, so it is a prerequisite for that whole track, not a
convenience.

---

## 7. Chat persistence

Today the routing agent builds its message list fresh on every free-text line
(`shell/agent.route`) and throws it away. The desktop needs conversations that survive
app restarts and sit next to the project.

```
chats/2026-09-04-checkout-work.json
{
  "id": "...", "title": "checkout work", "created": "...",
  "turns": [
    {"role": "user", "text": "test the coupon flow"},
    {"role": "assistant", "text": "Drafting 2 scenarios…", "command": "plan", "args": {...}, "exit": 0},
    {"role": "event", "kind": "artifact", "data": {"run": "checkout-coupon--20260904-1130"}}
  ]
}
```

- New `nkqa/chats.py`: create / list / load / append / rename / delete. Title auto-set
  from the first user message.
- `shell/agent.route` grows an optional `history: list[Turn]` parameter and appends to
  it. The terminal shell can start using it too (Phase 5 explicitly deferred
  persistence; this is where it lands).
- Cap the context sent to the model — last N turns plus the state summary
  `describe_state()` already builds. Don't replay a 200-turn chat into a haiku call.
- Commands run from the chat are recorded as turns, so the transcript shows *what the
  agent did*, not just what it said. That transcript is the thing a QA lead will screenshot.

---

## 8. Live browser view (staged)

Ship it in three stages, each independently useful. Stage 1 goes in the first release.

**Stage 1 — step filmstrip (free).** browser-use already captures a screenshot per step
and `generate_gif` builds the summary. Emit each step's screenshot path as an
`Event(kind='step', data={'screenshot': ...})`; the Live pane shows the current frame
big with a scrubbing filmstrip beneath it and the agent's reasoning beside it. Roughly
1 frame per action — choppy, but it already reads as "watching the agent work", and it
costs nothing beyond wiring.

**Stage 2 — CDP screencast (the real thing).** Get the CDP session behind browser-use's
Playwright page, call `Page.startScreencast` (jpeg, quality ~60, maxWidth 1280), forward
each `Page.screencastFrame` as a binary WS frame, and ack it. Render into an `<img>`
with an object URL, ~10 fps. Force `run.headless: true` while the pane is on, so nothing
pops over the app.

> **Spike first (half a day):** confirm browser-use exposes the Playwright page / CDP
> session on a public-enough surface for the installed version, and that a screencast
> coexists with its own screenshot capture. If it doesn't, Stage 1 remains the shipping
> answer and Stage 2 becomes an upstream ask. Do this spike **before** committing the
> Live pane to a release scope.

**Stage 3 — takeover (stretch).** Forward clicks/keys from the pane back via
`Input.dispatchMouseEvent` / `dispatchKeyEvent` so a human can grab the wheel mid-run.
This pairs exactly with `ask_human`: "I'm stuck on the 2FA screen" → the user does it by
hand in the pane → the agent continues. Compelling, and strictly optional.

---

## 9. Packaging & distribution

- **Sidecar build:** PyInstaller **onedir** (not onefile — Playwright needs a real
  filesystem layout and onefile costs seconds of startup on every launch). Output named
  for Tauri's convention, `nkqa-server-<target-triple>`, declared under
  `bundle.externalBin` in `tauri.conf.json`.
- **Chromium is not in the installer.** ~150MB, and it changes on its own schedule.
  First launch runs a setup screen that shells `playwright install chromium` with a
  progress bar. Cache it in app-data so a reinstall doesn't re-download. Offline first
  launch must produce a clear message, not a stack trace.
- **Node is not bundled.** `npx mcp-remote` is how Jira works. Detect Node at startup;
  if it's missing, keep the app fully functional and grey out the Jira features with a
  one-line explanation and an install link. Bundling a Node runtime to support one
  connector isn't worth ~40MB and a second supply chain.
- **macOS:** codesign + notarize. PyInstaller binaries typically need hardened-runtime
  entitlements (`allow-unsigned-executable-memory`, `allow-dyld-environment-variables`);
  budget a day for the first successful notarization, it is never the happy path.
- **Windows:** ship an MSI; without a signing cert expect SmartScreen warnings —
  decide early whether to buy one.
- **Updates:** Tauri's updater with signed release artifacts, built by a GitHub Action
  matrix (macos-latest arm64 + x64, windows-latest). Version the sidecar and the shell
  together; `/health` reports both so a mismatched pair fails loudly.
- **Size:** ~120–180MB installer, ~400MB installed after Chromium. Say this out loud in
  the README before someone is surprised by it.

---

## 10. New dependencies — needs your explicit approval

`CLAUDE.md` forbids adding dependencies without a yes. This track needs:

| Where | Package | Why |
|---|---|---|
| runtime | `fastapi` | HTTP + WS surface. `starlette` alone would do; FastAPI buys typed request models that pyright strict likes. |
| runtime | `uvicorn` | ASGI server. Standard extras only if we need websockets' C speedups — plain is fine. |
| runtime | `keyring` | Credential vault (§6). Pure-Python, wraps the OS keychain on all three platforms, no compiled crypto of our own. |
| dev | `pyinstaller` | sidecar build |
| frontend | Node 20 + Rust stable | Tauri v2, React, TypeScript, Vite |

That is deliberately the smallest set. No SQLite, no ORM, no state library — the
workspace is still the database, and the frontend holds server state in memory plus
`.nkqa/` for view preferences. Also update `pyproject.toml`'s ruff `extend-exclude` and
pyright `include` for the new `nkqa/server/` and the `desktop/` directory.

---

## 11. Milestones

Each ends green on `./check.sh` and is committable. Sizes assume the current pace.

| # | Milestone | What lands | Size |
|---|---|---|---|
| **D0** | Spikes | CDP screencast reachable through browser-use? PyInstaller + Playwright onedir launches on a clean Mac? Two throwaway scripts, two answers. | 1 day |
| **D1** | UI channel | `nkqa/ui.py`, `TerminalChannel`, every module threaded through, browser-use logging forwarded. CLI behaviour byte-identical (tests). **No Tauri yet.** | 3–4 days |
| **D2** | Sidecar | `nkqa/server/`, handshake, `/health`, `/workspace`, `/command`, WS + `SocketChannel`, job cancellation, token + origin auth, artifact path scoping. Drivable with `websocat` before any UI exists. | 3 days |
| **D2b** | Vault | `nkqa/vault.py` (keychain backend + env fallback), `vault.yaml` schema, grants in `qa_permissions.json`, the resolution chain inside `ask_credential`, origin binding, `qa vault` CLI, redaction tests. Unattended `qa replay --all` becomes possible here. | 2–3 days |
| **D3** | Tauri shell | Workspace picker + registry, sidecar spawn/kill lifecycle, project shell, Chat view wired to the WS, HITL modals (question / credential / permission). First end-to-end run from the GUI. | 4–5 days |
| **D4** | The views | Scenarios (status chips, body, approve with confirmation, revise), App Map + Flows (markdown render, edit), Runs (results, gif, video, per-step evidence), Settings → Credentials. Setup wizard writing `config.yaml`/`.env`/`vault.yaml`. | 4–5 days |
| **D5** | Live pane | Stage 1 filmstrip, then Stage 2 screencast if D0 said yes. | 2–3 days |
| **D6** | Ship | Chat persistence (`chats/`), PyInstaller sidecar, Chromium first-launch flow, codesign + notarize, updater, GH Action, README. | 4–5 days |

≈ 5–6 weeks of focused work. D1 is the load-bearing one — if it is done properly the
rest is assembly, and if it is fudged (globals, a hidden singleton channel) everything
after it fights the design.

**Suggested order of proof:** D0 → D1 → D2 → a `websocat` demo → D3. Resist starting the
Rust until D2 is drivable from a terminal; a debuggable backend is the entire reason
this bridge was chosen over PyO3.

---

## 12. Definition of done

- Double-click the app on a machine with neither Python nor Chrome installed →
  first-launch setup → create a workspace → wizard writes `config.yaml` + `.env`.
  **Done, with one deliberate change: the form writes `config.yaml` only, never `.env`.**
  Creation happens in the sidecar (`nkqa-server --init`, see PROTOCOL §2) so the layout
  stays in `workspace.py`, and its arguments cross Rust as argv — world-readable via `ps` —
  so an API key must never be collected there. `/health` already reports `missing_keys`,
  and Settings is where a key belongs.
- Chat in the window plans scenarios; the scenario view shows them; approve requires
  seeing the body and a deliberate confirm; the agent still cannot approve (test).
- A run streams steps and browser-use reasoning live, shows the browser (filmstrip at
  minimum), and asks for credentials/permissions in a modal.
- Stop mid-run leaves complete partial evidence on disk (test).
- Runs view plays the video and the gif, and links steps to evidence.
- The same workspace opened in a terminal with `qa` behaves exactly as it does today —
  same commands, same exit codes, same files (test).
- A credential is stored once in the OS keychain, released to a run only under a human
  grant, and refused when the browser's origin doesn't match its binding; revoking takes
  effect on the next request (test).
- No credential value appears in any event frame, run history, conversation transcript,
  chat transcript, log, screenshot, or filed bug (test).
- `qa vault status` on a fresh clone lists exactly what a new engineer must supply.
- `./check.sh` green: ruff, pyright strict, pytest.

---

## 13. Risks & mitigations

| Risk | Mitigation |
|---|---|
| D1 touches every module at once | Do it as pure substitution with the CLI's behaviour pinned by tests first. Add no features in D1. |
| browser-use internals shift (CDP access, logging) | D0 spike; keep Stage 1 filmstrip as the always-works fallback. Pin the browser-use version. |
| Notarization/signing eats a week | Start a signing dry-run during D2, not at D6. |
| The GUI erodes the approval gate | Written into §1 as an invariant; enforced by the `human_only` flag and a test. |
| A local HTTP port is an attack surface | 127.0.0.1 + token + Origin check + path scoping, all in D2, not retrofitted. |
| Storing credentials raises the blast radius of any bug | OS keychain rather than our own crypto or a file in the repo; origin binding; no `vault get`, no reveal in v1; the agent cannot enumerate entries; grants are revocable and scoped per scenario. |
| `keyring` behaves differently on three platforms (and headless CI has none) | Explicit env-var fallback, and a `qa vault status` that says which backend is in use. Test the fallback path in CI, since that is the one CI will actually take. |
| Two surfaces drift apart | Everything generated from `shell/commands.REGISTRY`; a test asserts CLI, shell, and HTTP expose the same command set. |
| Scope creep into a hosted product | Non-goals in ARCHITECTURE §10 still hold: no SaaS, no cloud browser farm, no team sync in this track. |

---

## 14. Out of scope for this track

Team sync / shared remote workspaces (still git + PRs — `OPEN-QUESTIONS` §3.1) ·
record-by-demonstration · scheduled/background suite runs and failure notifications
(that's Phase 6 territory, and easier once the sidecar exists) · Linux builds ·
mobile · any hosted component · replacing markdown-in-git with a database.

---

## 15. Decide before D1 starts

1. **The name.** `OPEN-QUESTIONS` §3.3 is still open and a desktop app has an icon, a
   bundle id, a window title, and an installer. Renaming after people install is a
   different kind of cost than a find-replace today.
2. **Bundle id + signing identity** — Apple Developer account, and whether a Windows
   cert gets bought.
3. **Where evidence lives long-term** (`OPEN-QUESTIONS` §3.2). The Runs view design
   changes if videos are meant to be pushed somewhere shared rather than kept local.
4. **Whether the vault must cover CI from day one.** If `qa replay --all` is meant to run
   on a build agent soon, the env-var fallback (§6.1) is the primary path rather than the
   fallback, and it should be tested first.
