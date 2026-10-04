# The sidecar goes here

`nkqa-server/` - a PyInstaller **onedir** folder (`nkqa-server` plus `_internal/`). Tauri copies
it into the app as the resource `server/` (`tauri.conf.json` → `bundle.resources`), and `lib.rs`
(`server_command`) spawns `<resources>/server/nkqa-server` by path.

## Build it

    venv/bin/pyinstaller --noconfirm nkqa-server.spec      # from the repo root -> dist/nkqa-server/
    rm -rf desktop/src-tauri/binaries/nkqa-server
    cp -RL dist/nkqa-server desktop/src-tauri/binaries/nkqa-server

`-L` matters: the build has symlinks (Pillow's dylibs), and real files are simpler to bundle.

### Why onedir and not onefile

Measured on macOS (aarch64), launch to the handshake line:

| build | every launch | first launch after install |
|---|---|---|
| onefile (what we shipped first) | **23-26 s** | ~27 s |
| onedir (now) | **~2.5 s** | ~28 s |

A onefile binary unpacks its ~200 MB archive into a temp dir on *every* launch, and macOS checks
the freshly written libraries each time. Onedir is already unpacked. macOS still checks each
library the first time it ever sees it (keyed by content, so a copied app is not "new" - a new
install on another Mac is), which is the slow first launch.

### The standby server

The shell hides both costs: when the app opens it starts `nkqa-server --standby --exit-with-parent`
(`start_standby` in `lib.rs`). That loads everything, prints `{"standby": true}` and waits for one
JSON line on stdin - `{"workspace": "...", "init": {"app_name", "base_url"} | null}` - then
continues exactly like a normal start and prints the usual handshake. `sidecar_connect` hands the
standby the workspace (falling back to a cold spawn if there is none or it died) and immediately
starts a replacement, so the next window is just as fast. Opening a workspace is then about the
time it takes to bind a port, and the first-install check runs while the human is on the picker.
The cost is one idle loaded server (~150 MB of memory) while the app is open.

Tauri's `externalBin` (the sidecar API) copies a single file, which is why the folder travels as a
resource instead. `HANDSHAKE_TIMEOUT` in `lib.rs` stays at 120 s for the cold first launch.

### Why all those `--collect-all` flags

PyInstaller's static analysis does not find them. `fastapi`, `starlette`, `pydantic`,
`uvicorn` and `keyring` all load pieces dynamically, and uvicorn picks its loop and
protocol implementations at runtime — hence the explicit `uvicorn.*.auto` imports. Without
these the binary builds fine and then dies on first launch with `ModuleNotFoundError`.

### Plain `cargo build` places it too

The Tauri build script copies resources next to the compiled executable (`target/debug/server/`)
for `cargo build`, `tauri dev` and `tauri build` alike. A stale *file* named
`target/debug/nkqa-server` from the old onefile setup is harmless now (the folder is `server/`).

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
