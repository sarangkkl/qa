# Sidecar protocol

The contract between the Tauri shell and `nkqa-server`. Frozen — a change here means
every surface has to be updated, so read §7 before proposing one.

Implemented in `nkqa/server/` (D2). Verified by `tests/test_server.py`.

---

## 1. Shape

**HTTP is state you can fetch. The WebSocket is things that happen.**

That split is the whole design. Anything you could reasonably re-fetch after a reload is
a `GET`; anything that runs, streams, or asks the human a question is a socket frame. There
is deliberately no `POST /command` — a command needs the socket anyway to deliver its
events and its prompts, so having two ways to start one would just be two code paths that
drift.

One process per open workspace. One WebSocket connection is one session, with its own
`HumanInTheLoop`, so a credential typed in one window is reused for that window and dies
when it closes. **One job at a time per process** — a run drives a real browser, owns the
HITL prompts, and swaps `sys.stdout` while it streams.

## 2. Launch and handshake

Tauri spawns:

```
nkqa-server --workspace /path/to/qa-workspace [--port 0] [--exit-with-parent]
            [--init [--app-name NAME] [--base-url URL]]
```

`--init` creates the workspace layout first (via `workspace.create`, the only place that
knows the layout) and then serves it, so the desktop can open a folder that is not a
workspace yet without a second process start. It is idempotent and never overwrites an
existing `config.yaml`; it refuses `$HOME` and the filesystem root. **Never put a secret on
this command line** — argv is world-readable via `ps`, which is why the setup form collects
an app name and base URL but never an API key. A key belongs in a `secret` ask over the
authenticated socket.

`--exit-with-parent` shuts the server down when its stdin closes, i.e. when the process
that spawned it dies — including a crash or force-quit that runs no cleanup.

The server binds `127.0.0.1` on an ephemeral port and writes **exactly one line** of JSON
to stdout, then never writes to stdout again:

```json
{"ready": true, "port": 51734, "token": "…64 hex chars…", "workspace": "/path/to/qa-workspace"}
```

Failure is the same channel, and exit code 2:

```json
{"ready": false, "error": "no QA workspace at /path/to/thing"}
```

If no line arrives within ~15s, show the sidecar's stderr on a diagnostics screen rather
than failing silently. (Nothing in the product prints to stdout — that is what the Channel
is for — so a stray print would corrupt this handshake.)

## 3. Auth — both checks matter

A loopback port is reachable by every browser tab on the machine.

- **Token** on every request: `Authorization: Bearer <token>`, or `?token=` (the WebSocket
  uses the query parameter, since browsers can't set headers on an upgrade). Compared with
  `secrets.compare_digest`. Wrong or missing → `401`, or WS close code `4401`.
- **Origin** on anything a browser sends: must be one of `tauri://localhost`,
  `https://tauri.localhost`, `http://tauri.localhost`, `http://localhost:1420`,
  `http://127.0.0.1:1420`. Anything else → `403`. A request with *no* Origin is a
  non-browser caller (curl, tests) and is allowed.

## 4. HTTP

| Route | Returns |
|---|---|
| `GET /health` | `nkqa` and `browser_use` versions, `workspace`, `busy`, and `roles` — per model role: `model`, `provider`, `missing_keys`. `models_ok` is true when no role is missing a key. This is the "is this workspace ready to run" check. |
| `GET /workspace` | `app_name`, `base_url`, `headless`, `models` (role → tier or model id), `aliases` (the `smart`/`fast` tiers), `providers` (see below), `appmap` (relative .md paths), `scenarios` (see below), `runs` (newest first), `commands` (see §6). Everything the project shell needs on open. |
| `GET /scenarios/{id}` | One scenario plus `body`, the raw markdown. `404` if unknown. |
| `GET /runs/{name}` | `steps`, `result` (the parsed `results.json`, or null), `artifacts` (`report`/`gif`/`history` → artifact URLs), `videos` (artifact URLs). |
| `GET /chats` | `chats`: id, title, created, updated, turn count — newest first. |
| `GET /chats/{id}` | One conversation with all its turns. `404` if unknown. |
| `GET /artifacts/{path}` | A file from inside the workspace. |

A scenario looks like:

```json
{"id": "auth/login", "title": "Login works", "state": "draft",
 "ticket": "", "tags": [], "approved_by": "", "approved_at": "", "last_verdict": ""}
```

`state` is `draft` · `ok` · `stale` · `deprecated` — the same four the runner enforces.
`stale` means the file was edited after approval; render it as a warning, never as
approved.

`providers` is what the settings page offers:

```json
{"name": "openai", "label": "OpenAI",
 "models": [{"id": "gpt-5.1", "label": "GPT-5.1", "tier": "smart"}]}
```

**It is a catalogue, not a validator.** Any model id reaches the provider verbatim, so this
list going stale costs a dropdown entry, never a capability — offer a way to type an id that
is not in it. `tier` says which of the two slots a model is the natural pick for, which is
what makes "switch provider" fill both in one move.

**Changing the model is `set-model`, not a PUT.** It writes the `smart`/`fast` aliases in
`config.yaml` — the two tiers every role points at — so one change moves all five roles and
the split between planning and executing survives. A role someone has pointed straight at a
model is left alone and named in the output. Read the result back from `/workspace` and
`/health` rather than assuming it applied: `/health` is what knows whether the new provider's
key is actually set.

**No API key ever crosses this protocol.** Keys are read from the workspace's `.env` when the
sidecar launches, so a key sent here would not take effect until the next launch anyway.
`/health` names the missing ones; putting them there is a human editing a file.

**`/artifacts` is path-scoped twice.** The resolved path must stay inside the workspace
root (so `../../.ssh/id_rsa` is a 404, not a file), and the suffix must be one of
`.png .jpg .jpeg .gif .webp .mp4 .webm .json .md .txt` (so `config.yaml` and `.env` are a
403 even though they are inside the workspace).

## 5. WebSocket — `ws://127.0.0.1:<port>/session?token=<token>`

### Client → server

```json
{"type": "command", "id": "c1", "name": "run", "args": {"id": "checkout/coupon"}}
{"type": "command", "id": "c2", "line": "/run checkout/coupon --model smart", "chat": "<optional>"}
{"type": "say",     "id": "s1", "text": "test the coupon flow", "chat": "<chat id, or omit>"}
{"type": "answer",  "id": "a7", "value": "hunter2"}
{"type": "cancel",  "id": "c1"}
```

A `command` carries **either** `name`+`args` (what buttons send) **or** `line`, a raw slash
line parsed server-side by `shell.commands.parse_slash` — the terminal's own parser, so the
grammar is identical everywhere and there is no second implementation to drift: quoting,
`--flags`, free-text `rest` params, the "did you mean" hint and the missing-parameter error
all come out the same. A parse failure is a `result` with code `2` carrying the parser's own
message. Include `chat` when the line was typed in the chat box and the command and its exit
code are recorded as turns, so a transcript has no unexplained holes where work happened.

`say` is what the Chat view sends: plain English, routed through the same agent the
terminal shell uses. It runs as a job like any command, so it streams events and can be
cancelled. Omit `chat` and the server opens a new one and tells you its id (see the `chat`
frame below); pass one to continue it. Turns are appended to `chats/<id>.json` in the
workspace — what the agent *said* and what it *ran*, so the transcript is reviewable in a
PR. The router still cannot approve.

`args` values may be strings, numbers or booleans; the server coerces them to each
parameter's declared type and silently drops names the command doesn't have.

### Server → client

```json
{"type": "started",   "job": "c1", "name": "run"}
{"type": "event",     "job": "c1", "kind": "step", "text": "Applied coupon SAVE10",
                      "data": {"n": 3, "screenshot": "/…/step_3.png", "url": "https://…", "action": "click"}}
{"type": "ask",       "id": "a7", "job": "c1", "kind": "secret", "prompt": "🔑 QA agent needs \"password\": ",
                      "key": "password", "options": [], "body": "", "interrupt": true}
{"type": "result",    "job": "c1", "code": 0, "cancelled": false}
{"type": "cancelled", "job": "c1", "ok": true}
{"type": "chat",      "id": "20260904-141230", "title": ""}
{"type": "frame",     "job": "c1", "image": "<base64 jpeg>", "format": "jpeg", "width": 1280, "height": 800}
{"type": "error",     "message": "unknown frame type 'wat'"}
```

**`frame`** is the live browser view, and it is its own message type rather than an event:
at 20 fps the envelope and an empty `text` field per frame actually cost something. Render
it into an `<img>` via a data URL or an object URL. Frames are **shed, not queued** when
the UI cannot keep up — measured 20 fps to a fast consumer with nothing dropped, and 4.5
fps with 75 dropped to one taking 0.4s per frame, with the run itself never slowing down.
A `progress` event with `data.screencast` true/false brackets the stream and reports
`frames`/`dropped` at the end. If the browser will not screencast, that same event says
`unavailable` once and the run carries on with the step filmstrip.

**`event.kind`** — `log` · `step` · `verdict` · `artifact` · `progress` · `frame` · `done`.
(`frame` is emitted as its own `frame` message, never inside an `event` envelope.)
`text` is what the terminal would have printed; `data` is the same thing structured, so a
UI renders rows instead of re-parsing strings. Worth knowing what's in `data`:

| where | `data` carries |
|---|---|
| scenario listing | `scenario`, `state`, `title`, `last_verdict` |
| run listing | `run`, `when`, `steps` |
| model roles | `role`, `model`, `provider`, `missing` |
| a run's steps | `n`, `screenshot`, `url`, `action` |
| a verdict | `scenario`, `verdict`, `run` |
| appmap writes | `appmap` (relative path) |
| browser-use's own narration | `source: "browser-use"` |

**`ask.kind`** — `text` · `secret` · `confirm` · `choice`.
`body` is long content to show first (a scenario to approve, a bug preview). `options` is
populated for `choice` (permissions send `["y","s","a","n"]` = once / session / always /
deny). `interrupt: true` means the prompt came from inside a running job rather than a
command's own dialogue — render those as a modal.

Reply with `{"type": "answer", "id": <the ask id>, "value": "..."}`. `confirm` is true when
the value starts with `y`.

**Exit codes** are the CLI's, unchanged: `0` ok, `1` failure or declined, `2` usage or not
found. CI depends on them, so the UI should too.

## 6. Commands come from one registry

`GET /workspace` returns `commands`, generated from `shell.commands.REGISTRY` — the same
registry that generates the terminal's slash parser and the chat agent's tool list. Build
the UI from it rather than hard-coding command names, and a new command appears everywhere
at once.

```json
{"name": "run", "help": "execute an approved scenario in a real browser",
 "human_only": false, "shell_only": false, "instant": false,
 "params": [{"name": "id", "help": "scenario id", "type": "string", "flag": false, "required": false}]}
```

- **`instant: true`** (`mode`, `set-model`) — runs outside the one-job-at-a-time runner, so it still
  works while a run is in flight. That is the whole point: a session control you cannot use
  mid-run is a session control you cannot use. Only safe for a command that drives no
  browser, writes nothing a running job is also writing, and **never asks** — an instant
  command's channel is not in the pending-ask map, so a prompt from one could never be
  answered and would hang forever.
- **`human_only: true`** (`approve`, `mode`, `set-model`, the mutating vault commands) — served over the socket, because a human at a
  keyboard is what drives it, but it is absent from the chat agent's tool list and must
  stay that way. In the UI it needs the full scenario body visible and a deliberate second
  click. A one-click approve chip is a regression of the product's trust story.
- **`shell_only: true`** (`help`, `exit`, `forget`) — terminal meta commands. The server
  refuses them with code `2`.

**`crawl` and `correct` write the app map, so `/workspace` is stale after them.** A crawl now
writes one page per screen *while it runs*, each as its own git commit, rather than one
update at the end — so `appmap` in `GET /workspace` grows during the job, not only after it.
Every recorded page arrives as an `artifact` event carrying `{"appmap": "pages/<slug>.md"}`;
render those as progress. Re-read `/workspace` when the job finishes, and treat a stopped
crawl as a success: it kept everything it had already mapped, and its exit code says so.

## 7. Rules that are not negotiable

1. **Bind `127.0.0.1` only.** Never `0.0.0.0`.
2. **A secret's value never travels back.** It goes from the `answer` frame straight into
   `HumanInTheLoop.secrets` and appears in no event, no log, no chat transcript, no run
   history. In the frontend, treat a `secret` input as write-only: send it and clear the
   field, never put it in app state. `tests/test_ui.py` asserts the backend half.
3. **A dead socket denies.** Closing the window or cancelling resolves every pending ask
   with `""` — which every call site already treats as "no value / deny". A vanished
   window can never leave a run hanging, and can never accidentally grant a permission.
4. **Cancellation is tracked, not inferred.** The runners deliberately swallow
   `CancelledError` so partial evidence is saved, so a cancelled job still returns an exit
   code. Trust `result.cancelled`, not the code.

   **A cancel is two things, fired together.** Cancelling the task alone lands wherever the
   run happens to be awaiting and leaves browser-use to decide what that means; the stop
   signal reaches the `Agent` itself but is only polled at step boundaries. Neither is
   sufficient alone, so `jobs.cancel` does both.

   `cancelled.ok: false` means nothing was running under that id - and, importantly, that
   **no pending ask was denied**. Abandoning resolves every pending ask with `''`, which is
   a deny, so a stale id must never reach in and answer the live job's prompt.

   A cancelled run **skips reflection**: the appmap is not updated from half a run, and no
   LLM call is spent after you press Stop. It also skips the summary GIF. Everything else -
   `history.json`, the conversation transcript, the step screenshots, and for a scenario the
   verdict in `results.md` - is still written.
5. **Adding an `EventKind` or `AskKind` is a contract change** — every surface must render
   it. Propose it here first. (The `line` field, the `instant` flag and the autonomy modes
   were all added without one: a new field on an existing frame, and a `log` event carrying
   structured `data`. Prefer that shape.)
6. **Session autonomy never touches the two gates.** `mode` sets how `request_permission`
   answers itself for one session — `ask` (default) · `allow` · `refuse`. It is never
   persisted, resets to `ask` on every reconnect, and is `human_only` so the agent cannot
   widen its own autonomy. It does **not** touch `ask_credential`: a stored credential is
   still released only under an explicit human grant with its origin binding intact. And
   `approve` stays `human_only` in every mode. Note what it is not: it governs actions the
   agent *declares* risky, so it is not a sandbox.
