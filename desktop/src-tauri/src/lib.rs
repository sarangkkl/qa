//! The desktop shell. Its whole job is the sidecar's lifecycle and the handshake.
//!
//! One `nkqa-server` process per open workspace: the sidecar loads that workspace's .env
//! into its own process, and `load_dotenv` mutates the global environment, so sharing one
//! process across workspaces would silently cross-contaminate API keys.
//!
//! Everything else — every read, every command, every prompt — goes straight from the
//! webview to that process over HTTP and a WebSocket. Nothing is proxied through Rust,
//! so there is exactly one implementation of the protocol.

mod menu;

use std::collections::HashMap;
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::Mutex;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tauri::{Manager, State};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

// A --onefile PyInstaller build unpacks ~72 MB to a temp dir and imports browser_use before
// it can say anything, and macOS adds a Gatekeeper scan on top. Measured here: 27 s cold,
// 17 s warm. The old 20 s budget killed the sidecar mid-boot on every cold start, so this is
// deliberately generous - the cost of waiting too long is a slow open, the cost of waiting
// too little is an app that never opens at all.
const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(120);
const RECENTS_FILE: &str = "workspaces.json";
const MAX_RECENTS: usize = 12;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Connection {
	pub port: u16,
	pub token: String,
	pub workspace: String,
}

/// What the sidecar writes on stdout as its first and only line.
#[derive(Debug, Deserialize)]
struct Handshake {
	ready: bool,
	#[serde(default)]
	port: u16,
	#[serde(default)]
	token: String,
	#[serde(default)]
	workspace: String,
	#[serde(default)]
	error: String,
}

struct Sidecar {
	connection: Connection,
	child: CommandChild,
}

#[derive(Default)]
pub struct Sidecars {
	running: Mutex<HashMap<String, Sidecar>>,
	// Which window is showing which workspace. Closing one window must stop only what no
	// other window still holds - two workspaces open at once is the point, and Cmd+W on one
	// of them used to kill both.
	claims: Mutex<HashMap<String /* window label */, String /* workspace */>>,
	// Labels are never reused, so a closed window's label can never collide with a live one.
	next_window: AtomicU32,
	// One connect at a time. Without this, React's StrictMode double-invoke - or an
	// impatient second click during the ~20 s boot - spawns a second sidecar for the same
	// workspace, because nothing lands in `running` until the handshake arrives.
	gate: tokio::sync::Mutex<()>,
}

/// Stop a sidecar and everything it forked.
///
/// `CommandChild::kill` sends SIGKILL, which the PyInstaller bootloader cannot catch or
/// forward - its real Python child survives, reparented to init, and every failed connect
/// leaves one behind forever (this is how a handful of retries became 25 stray processes).
/// SIGTERM *is* forwarded, so ask politely first and keep SIGKILL as the fallback.
fn stop(child: CommandChild) {
	#[cfg(unix)]
	{
		let pid = child.pid();
		let terminated = std::process::Command::new("kill")
			.args(["-TERM", &pid.to_string()])
			.status()
			.map(|s| s.success())
			.unwrap_or(false);
		if terminated {
			// Give the bootloader a moment to pass the signal on before the hard kill.
			std::thread::sleep(Duration::from_millis(500));
			return;
		}
	}
	let _ = child.kill();
}

/// The escape hatch: stop asking, and end the process.
///
/// SIGTERM first so uvicorn's shutdown hook can close the browser cleanly, then SIGKILL if it
/// misses the window. The 500ms `stop()` above is fine for closing the app; a live run needs
/// longer, because the hook is closing Chromium in that time.
fn stop_hard(child: CommandChild) {
	#[cfg(unix)]
	{
		let pid = child.pid();
		let _ = std::process::Command::new("kill")
			.args(["-TERM", &pid.to_string()])
			.status();
		std::thread::sleep(Duration::from_millis(1500));
	}
	let _ = child.kill();
}

/// Kill the sidecar for one workspace. The frontend reconnects, which respawns it.
///
/// This is the only stop that cannot fail, and the only one that costs something: the
/// session's in-memory credentials, any pending prompt, and the run's video die with the
/// process. Everything already checkpointed to disk survives.
#[tauri::command]
async fn sidecar_stop(state: State<'_, Sidecars>, workspace: String) -> Result<(), String> {
	// Held so this cannot race a connect that is mid-handshake for the same workspace.
	let _gate = state.gate.lock().await;
	// The claim survives: the window still owns this workspace and reconnects immediately.
	// Releasing it here would let another window take it mid-restart.
	let sidecar = state
		.running
		.lock()
		.map_err(|e| e.to_string())?
		.remove(&workspace);
	if let Some(sidecar) = sidecar {
		stop_hard(sidecar.child);
	}
	Ok(())
}

/// Which window owns which workspace.
///
/// Lock order is always `claims` then `running`, and never both at once.
impl Sidecars {
	fn claim(&self, label: &str, workspace: &str) {
		if let Ok(mut claims) = self.claims.lock() {
			claims.insert(label.to_string(), workspace.to_string());
		}
	}

	/// The window already showing this workspace, if any.
	fn claimant(&self, workspace: &str) -> Option<String> {
		let claims = self.claims.lock().ok()?;
		claims
			.iter()
			.find(|(_, held)| held.as_str() == workspace)
			.map(|(label, _)| label.clone())
	}

	/// Forget a window, and stop its sidecar only if it was the last one showing it.
	fn release(&self, label: &str) {
		let workspace = match self.claims.lock() {
			Ok(mut claims) => match claims.remove(label) {
				Some(held) if !claims.values().any(|other| other == &held) => held,
				_ => return,
			},
			Err(_) => return,
		};
		let sidecar = self.running.lock().ok().and_then(|mut r| r.remove(&workspace));
		if let Some(sidecar) = sidecar {
			stop(sidecar.child);
		}
	}

	fn stop_all(&self) {
		if let Ok(mut running) = self.running.lock() {
			for (_, sidecar) in running.drain() {
				stop(sidecar.child);
			}
		}
	}
}

impl Drop for Sidecars {
	fn drop(&mut self) {
		self.stop_all();
	}
}

fn recents_path(app: &tauri::AppHandle) -> Option<std::path::PathBuf> {
	let dir = app.path().app_data_dir().ok()?;
	std::fs::create_dir_all(&dir).ok()?;
	Some(dir.join(RECENTS_FILE))
}

fn read_recents(app: &tauri::AppHandle) -> Vec<String> {
	recents_path(app)
		.and_then(|p| std::fs::read_to_string(p).ok())
		.and_then(|text| serde_json::from_str::<Vec<String>>(&text).ok())
		.unwrap_or_default()
}

fn remember(app: &tauri::AppHandle, workspace: &str) {
	let mut recents = read_recents(app);
	recents.retain(|p| p != workspace);
	recents.insert(0, workspace.to_string());
	recents.truncate(MAX_RECENTS);
	if let Some(path) = recents_path(app) {
		let _ = std::fs::write(path, serde_json::to_string_pretty(&recents).unwrap_or_default());
	}
}

/// The one place the shell knows anything about the workspace layout.
///
/// `nkqa/workspace.py` is the source of truth (`at()` checks config.yaml *and* appmap/);
/// this is only a cheap "should we offer to create one?" so the picker can decide without
/// paying a ~30s sidecar start to be told no. The sidecar re-checks properly either way.
fn looks_like_workspace(path: &str) -> bool {
	std::path::Path::new(path).join("config.yaml").is_file()
}

/// What to call a workspace in a window title or a menu: its folder name.
pub(crate) fn folder_name(workspace: &str) -> String {
	std::path::Path::new(workspace)
		.file_name()
		.map(|name| name.to_string_lossy().into_owned())
		.unwrap_or_else(|| workspace.to_string())
}

/// A folder someone deleted should not haunt the picker - or the File menu.
pub(crate) fn valid_recents(app: &tauri::AppHandle) -> Vec<String> {
	read_recents(app)
		.into_iter()
		.filter(|p| looks_like_workspace(p))
		.collect()
}

#[tauri::command]
fn recent_workspaces(app: tauri::AppHandle) -> Vec<String> {
	valid_recents(&app)
}

#[derive(Debug, Clone, Serialize)]
pub struct PickedFolder {
	pub path: String,
	pub is_workspace: bool,
}

#[tauri::command]
async fn pick_workspace(app: tauri::AppHandle) -> Option<PickedFolder> {
	use tauri_plugin_dialog::DialogExt;
	let (tx, rx) = std::sync::mpsc::channel();
	app.dialog()
		.file()
		.set_title("Open or create a QA workspace")
		.pick_folder(move |picked| {
			let _ = tx.send(picked.map(|p| p.to_string()));
		});
	rx.recv().ok().flatten().map(|path| PickedFolder {
		is_workspace: looks_like_workspace(&path),
		path,
	})
}

/// Percent-encode a query value. A workspace path can hold a space, a `&` or a `#`, any of
/// which would split or truncate the intent silently.
fn encode(value: &str) -> String {
	value
		.bytes()
		.map(|b| match b {
			b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => (b as char).to_string(),
			_ => format!("%{b:02X}"),
		})
		.collect()
}

/// Open another native window: one window per workspace is the whole multi-workspace story.
///
/// `workspace` opens straight into it, `pick` lands on the picker with the folder chooser
/// already up, neither is just the picker. The intent rides in the window's own URL rather
/// than an event because a window that has not loaded yet has nothing listening.
///
/// Size, title and the minimums come from `tauri.conf.json` via `from_config`, so there is
/// no second copy of them here to drift.
pub(crate) fn open_window(app: &tauri::AppHandle, workspace: Option<String>, pick: bool) -> Result<(), String> {
	if let Some(path) = &workspace {
		if let Some(existing) = app
			.state::<Sidecars>()
			.claimant(path)
			.and_then(|label| app.get_webview_window(&label))
		{
			return existing.set_focus().map_err(|e| e.to_string());
		}
	}

	let mut config = app
		.config()
		.app
		.windows
		.first()
		.cloned()
		.ok_or_else(|| "no window is configured".to_string())?;
	let n = app.state::<Sidecars>().next_window.fetch_add(1, Ordering::Relaxed);
	config.label = format!("ws-{n}");
	config.url = tauri::WebviewUrl::App(
		match (&workspace, pick) {
			(Some(path), _) => format!("index.html?open={}", encode(path)),
			(None, true) => "index.html?intent=pick".to_string(),
			(None, false) => "index.html".to_string(),
		}
		.into(),
	);

	tauri::WebviewWindowBuilder::from_config(app, &config)
		.map_err(|e| e.to_string())?
		.build()
		.map(|_| ())
		.map_err(|e| e.to_string())
}

/// Async on purpose: building a window from inside a synchronous command deadlocks on Windows.
#[tauri::command]
async fn new_window(app: tauri::AppHandle, workspace: Option<String>, pick: bool) -> Result<(), String> {
	open_window(&app, workspace, pick)
}

/// What the setup form collected. Never a secret: argv is world-readable via `ps`, so an
/// API key must go over the authenticated socket as a `secret` ask, never through here.
#[derive(Debug, Clone, Deserialize)]
pub struct InitOptions {
	pub app_name: String,
	pub base_url: String,
}

/// Start (or reuse) the sidecar for a workspace and return where to reach it.
///
/// The workspace is required, deliberately: there is no "reopen the last one". A workspace
/// opens because someone chose it, never because it was open last time - reopening it on
/// launch spawns a 30s sidecar and a real browser for a project nobody asked about.
#[tauri::command]
async fn sidecar_connect(
	app: tauri::AppHandle,
	window: tauri::WebviewWindow,
	state: State<'_, Sidecars>,
	workspace: String,
	init: Option<InitOptions>,
) -> Result<Connection, String> {
	// With `init` the folder is *expected* not to be one yet - that is the whole point.
	if init.is_none() && !looks_like_workspace(&workspace) {
		return Err(format!(
			"{workspace} is not a QA workspace - it has no config.yaml. Open a folder created by `qa init`."
		));
	}

	// Held for the whole spawn: a caller that arrives mid-boot waits here and then finds the
	// finished sidecar in the map below, instead of starting a second one.
	let _gate = state.gate.lock().await;

	// One window per workspace. Two views on one sidecar are two sessions driving one
	// browser, and neither window can tell whose run is on the screen - so send the human to
	// the window that already has it instead of quietly opening a second view.
	if let Some(owner) = state.claimant(&workspace) {
		if owner != window.label() {
			if let Some(existing) = app.get_webview_window(&owner) {
				let _ = existing.set_focus();
			}
			return Err(format!("{workspace} is already open in another window."));
		}
	}

	let existing = state
		.running
		.lock()
		.map_err(|e| e.to_string())?
		.get(&workspace)
		.map(|sidecar| sidecar.connection.clone());
	if let Some(connection) = existing {
		state.claim(window.label(), &workspace);
		return Ok(connection);
	}

	let mut args: Vec<String> = vec![
		"--workspace".into(),
		workspace.clone(),
		"--exit-with-parent".into(),
	];
	if let Some(options) = &init {
		args.push("--init".into());
		args.push("--app-name".into());
		args.push(options.app_name.clone());
		args.push("--base-url".into());
		args.push(options.base_url.clone());
	}

	let (mut rx, child) = app
		.shell()
		.sidecar("nkqa-server")
		.map_err(|e| format!("cannot find the bundled nkqa-server: {e}"))?
		.args(args)
		.spawn()
		.map_err(|e| format!("could not start nkqa-server: {e}"))?;

	// Read exactly one line of stdout. Anything on stderr is kept for the error message,
	// because a sidecar that dies silently is the worst thing this window can do.
	let mut stderr = String::new();
	let deadline = std::time::Instant::now() + HANDSHAKE_TIMEOUT;
	let handshake = loop {
		if std::time::Instant::now() > deadline {
			stop(child);
			return Err(format!(
				"nkqa-server did not report ready within {}s.\n{stderr}",
				HANDSHAKE_TIMEOUT.as_secs()
			));
		}
		match tokio::time::timeout(Duration::from_secs(1), rx.recv()).await {
			Ok(Some(CommandEvent::Stdout(bytes))) => {
				let line = String::from_utf8_lossy(&bytes);
				let line = line.trim();
				if line.is_empty() {
					continue;
				}
				match serde_json::from_str::<Handshake>(line) {
					Ok(parsed) => break parsed,
					Err(e) => {
						stop(child);
						return Err(format!("could not read the handshake ({e}): {line}"));
					}
				}
			}
			Ok(Some(CommandEvent::Stderr(bytes))) => {
				stderr.push_str(&String::from_utf8_lossy(&bytes));
			}
			Ok(Some(CommandEvent::Terminated(status))) => {
				return Err(format!("nkqa-server exited ({:?}).\n{stderr}", status.code));
			}
			Ok(Some(_)) => continue,
			Ok(None) => return Err(format!("nkqa-server closed its output.\n{stderr}")),
			Err(_) => continue, // one second with nothing said; keep waiting until the deadline
		}
	};

	if !handshake.ready {
		stop(child);
		return Err(if handshake.error.is_empty() {
			"nkqa-server refused to start".into()
		} else {
			handshake.error
		});
	}

	let connection = Connection {
		port: handshake.port,
		token: handshake.token,
		workspace: if handshake.workspace.is_empty() {
			workspace.clone()
		} else {
			handshake.workspace
		},
	};

	// The window can be closed during the ~30 s boot. A claim for a label that no longer
	// exists is never released, so the sidecar would outlive anyone's interest in it.
	if app.get_webview_window(window.label()).is_none() {
		stop(child);
		return Err("the window was closed while the workspace was opening".into());
	}

	// Drain the rest of the sidecar's output so its pipe never fills and blocks it.
	tauri::async_runtime::spawn(async move { while rx.recv().await.is_some() {} });

	state
		.running
		.lock()
		.map_err(|e| e.to_string())?
		.insert(workspace.clone(), Sidecar { connection: connection.clone(), child });
	state.claim(window.label(), &workspace);
	remember(&app, &workspace);
	// Open Recent is a snapshot of what `remember` just wrote.
	menu::refresh(&app);
	// The Window menu lists windows by title, and "nkqa" three times is useless. Set it here
	// rather than from TS: no new ACL permission, and it lands with the handshake.
	let _ = window.set_title(&format!("{} — nkqa", folder_name(&workspace)));
	Ok(connection)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
	tauri::Builder::default()
		.plugin(tauri_plugin_shell::init())
		.plugin(tauri_plugin_dialog::init())
		.manage(Sidecars::default())
		// In `setup`, not `.menu()`: that closure runs before Tauri manages its path resolver,
		// and Open Recent is read from a file under the app data dir.
		.setup(|app| {
			let handle = app.handle();
			handle.set_menu(menu::build(handle)?)?;
			Ok(())
		})
		.on_menu_event(menu::on_event)
		.invoke_handler(tauri::generate_handler![
			sidecar_connect,
			sidecar_stop,
			recent_workspaces,
			pick_workspace,
			new_window
		])
		.on_window_event(|window, event| {
			// Only this window's sidecar, and only if no other window is still showing it -
			// closing one workspace must leave the other windows working.
			if let tauri::WindowEvent::Destroyed = event {
				if let Some(state) = window.app_handle().try_state::<Sidecars>() {
					state.release(window.label());
				}
			}
		})
		.build(tauri::generate_context!())
		.expect("error while building nkqa")
		.run(|app, event| {
			// The backstop: quitting can outrun the per-window Destroyed events, and a sidecar
			// left running is holding a real browser.
			if let tauri::RunEvent::Exit = event {
				if let Some(state) = app.try_state::<Sidecars>() {
					state.stop_all();
				}
			}
		});
}
