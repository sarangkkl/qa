# The sidecar binary goes here

Tauri looks for `nkqa-server-<target-triple>` here. `rustc -vV | grep host` prints the
triple.

## Build it (verified working — read the notes, they cost a few hours)

    venv/bin/pip install pyinstaller
    venv/bin/pyinstaller --noconfirm --onefile --name nkqa-server \
      --collect-all browser_use --collect-all cdp_use --collect-all nkqa \
      --collect-all fastapi --collect-all uvicorn --collect-all keyring \
      --collect-all pydantic --collect-all starlette \
      --hidden-import nkqa.server.main --hidden-import uvicorn.loops.auto \
      --hidden-import uvicorn.protocols.http.auto \
      --hidden-import uvicorn.protocols.websockets.auto \
      --hidden-import uvicorn.lifespan.on \
      desktop/src-tauri/sidecar_entry.py

    cp dist/nkqa-server desktop/src-tauri/binaries/nkqa-server-$(rustc -vV | awk '/host:/{print $2}')

Measured on Linux: **99 MB, 2.4 s from launch to the handshake line.**
Measured on macOS (aarch64): **72 MB, 27 s cold and 17 s warm** — Gatekeeper scans the
unpacked archive on first run. The shell's `HANDSHAKE_TIMEOUT` is 120 s for this reason; it
used to be 20 s, which killed the sidecar mid-boot on every cold start and made "Open a
workspace" look like a dead button. If you shorten it, measure on a cold macOS first.

### Why `--onefile` and not `--onedir`

`--onedir` starts faster (~0.5 s) but produces `nkqa-server` *plus* a 204 MB `_internal/`
sibling it cannot run without. Tauri's `externalBin` copies a single file, so a onedir
build fails at launch with:

    Failed to load Python shared library '.../binaries/_internal/libpython3.13.so.1.0'

Shipping onedir would mean bundling `_internal/` as a Tauri `resource` and spawning the
resource path directly instead of using the sidecar API. Two seconds once per workspace
open is not worth that. If startup ever does matter, that is the escape hatch.

### Why all those `--collect-all` flags

PyInstaller's static analysis does not find them. `fastapi`, `starlette`, `pydantic`,
`uvicorn` and `keyring` all load pieces dynamically, and uvicorn picks its loop and
protocol implementations at runtime — hence the explicit `uvicorn.*.auto` imports. Without
these the binary builds fine and then dies on first launch with `ModuleNotFoundError`.

### Plain `cargo build` does not place the sidecar

`tauri build` and `tauri dev` copy `binaries/nkqa-server-<triple>` next to the compiled
executable. A bare `cargo build` does not, and the app then fails to spawn. Copy it
yourself when working that way:

    cp src-tauri/binaries/nkqa-server-<triple> src-tauri/target/debug/

### Killing it needs SIGTERM, not SIGKILL

The bootloader forks the real Python process. SIGKILL cannot be caught or forwarded, so it
kills only the bootloader and leaves that child running forever, reparented to init — a few
failed connects is all it takes to litter the machine with stray servers. SIGTERM *is*
forwarded, so `stop()` in `lib.rs` sends that first. Belt and braces, the shell also passes
`--exit-with-parent`, which makes the server shut down when its stdin hits EOF — i.e. when
the app dies, including a crash or force-quit that runs no cleanup at all. The flag is
opt-in so a sidecar started from a terminal with stdin closed does not exit immediately.

## You do not need any of this to develop the UI

Run the sidecar yourself and open the Vite dev server with its handshake:

    venv/bin/nkqa-server --workspace /path/to/workspace
    # → {"ready": true, "port": 51734, "token": "…"}
    open 'http://127.0.0.1:1420/?port=51734&token=…'
