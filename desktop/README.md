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
- **Autonomy resets to Ask on every reconnect, and that is deliberate** — a new socket is a
  new session with a fresh `HumanInTheLoop`. Never show a mode the server is not in. It
  never approves, never releases a credential, and is never persisted.
