# nkqa desktop

Tauri v2 shell + React/TypeScript frontend over the sidecar protocol in
[../docs/PROTOCOL.md](../docs/PROTOCOL.md).

## Two ways to run it

**In a browser (fastest loop, and how the UI is tested).** Start a sidecar yourself and
open the dev server with its handshake in the query string — no Rust toolchain needed:

    ../venv/bin/nkqa-server --workspace /path/to/qa-workspace
    # → {"ready": true, "port": 51734, "token": "…"}

    npm install && npm run dev
    open 'http://127.0.0.1:1420/?port=51734&token=…'

**As the desktop app.** Needs the Rust toolchain and the sidecar binary in
`src-tauri/binaries/` (see the README there):

    npm run dev:tauri      # spawns the sidecar itself
    npm run build:tauri    # .app / .dmg / .msi

## How it fits together

`src-tauri` owns exactly one thing: the sidecar's lifecycle. It spawns
`nkqa-server --workspace <path>`, reads the one-line handshake, hands `{port, token}` to
the webview, and kills the process on close. **Nothing is proxied through Rust** — every
read, command and prompt goes straight from the frontend to the sidecar, so there is one
implementation of the protocol, and the same frontend runs in a plain browser.

One sidecar per open workspace, because the sidecar loads that workspace's `.env` into its
own process environment.

    src/api/    types (mirrors PROTOCOL.md) · connection · HTTP reads · the session socket
    src/views/  Chat · Scenarios · AppMap+Flows · Runs · Credentials · LivePane · picker
    src/components/  AskModal (the HITL prompt) · a small markdown renderer

## Things not to undo

- **Approve stays a two-step.** The button runs the real `approve` command; the server
  asks back with the whole scenario in the ask body, and the modal makes you read it and
  confirm. No one-click chip — that gate is the product.
- **A secret input is write-only.** The value is sent and the field cleared. It is never
  put in component state that survives, never logged, never rendered back.
- **Dismissing an ask is denying.** A closed dialog resolves the ask with `''` server-side.
- **Frames are dropped when the UI falls behind**, by design. Gaps in the live view are
  expected and not a bug to paper over.
- **The terminal feed shows every event kind, browser-use's narration included.** Filtering it
  down to "important" lines defeats the point: it exists so you can tell a slow job from a
  stuck one, and that judgement needs the boring lines. Collapsed, its header must keep
  showing the elapsed time and the latest line — that row is the feature, not the expanded view.
- **The elapsed timer is client-side and must stay that way.** A chat turn is one blocking LLM
  call that emits nothing until it returns, so a timer driven by incoming events would sit
  frozen during exactly the wait it exists to explain.
- **A step screenshot is an artifact path, fetched through `artifactUrl`.** Never put
  `step.data.screenshot` straight into an `<img src>` — it used to be a raw temp-directory
  path, which is why the live stage was a black box for so long.
- **The command list comes from `GET /workspace`**, not from hard-coded names — that is
  what keeps CLI, shell and desktop in step.
- **The slash grammar lives server-side** in `shell.commands.parse_slash`. The chat box
  sends the raw line; it must not be re-parsed in TypeScript. (The served param metadata
  does not even carry `rest`, so a TS parser would mis-handle `/plan <free text>` on day
  one.) The client suggests; the server decides.
- **The approve gate is the ask, not the surface.** `/approve x` typed in chat still opens
  the modal with the whole scenario body, because the server raises that ask wherever the
  command came from.
- **A job id is opaque; the view that started a job owns it.** Chat tracks the ids it
  started rather than reading meaning into the first letter.
- **Stop is reachable from every surface that can cover the screen.** The ask modal's
  backdrop sits over the Live pane, so a run parked on a permission prompt had no reachable
  Stop at all - that is why the modal carries one. Escape denies the question; Cmd+. stops
  the run. They must not swap meanings.
- **Force stop is two clicks and never automatic.** It kills the sidecar, which discards the
  session's credentials and any pending ask. Escalating that on a timer is not recoverable.
- **A cancelled job must look different from a finished one.** The server sends
  `result.cancelled` and a `cancelled` ack with `ok`; render both, or a failed Stop is
  indistinguishable from a successful one.
- **`connect` writes a connector; `auth` signs in. Two commands, deliberately.** Sign-in is up
  to 300s of interactive OAuth, so it runs as a normal job; `connect` writes one config file and
  is `instant`, which is what lets Settings work mid-run.
- **Connect only knows a fixed list of connectors, and Jira's name must be `jira`.**
  `jira_server()` looks the server up by that exact string, so a connector called `atlassian`
  would render fine and then break file-bug and plan-from-ticket. Anything not on the list stays
  a hand edit — this path writes a command that config.yaml will later execute.
- **Never offer `expose_to_executor` as a toggle.** It defaults to false for Jira so the testing
  agent cannot file bugs on its own; that is a safety property, not a setting.
- **The model catalogue is a suggestion, not a whitelist.** Settings offers the list from
  `GET /workspace`, but "Other" takes any id and it reaches the provider verbatim. A model
  released after this build must stay reachable without shipping a new build.
- **Settings edits the two tiers, never the five roles.** `smart` plans, `fast` drives the
  browser; every role ships pointing at one of them, so one edit moves everything and the
  cost/quality split survives. A role a human has pointed straight at a model is left alone
  and named — silently rewriting a hand-edited line is worse than not moving it.
- **The settings page never collects an API key.** Keys are read from `.env` when the sidecar
  launches, so one typed here would do nothing until the next launch. Name the missing key
  and the file; do not build a field that looks like it works.
- **Autonomy resets to Ask on every reconnect, and that is deliberate** — a new socket is a
  new session with a fresh `HumanInTheLoop`. Never show a mode the server is not in. It
  never approves, never releases a credential, and is never persisted.
