//! The native menu.
//!
//! It exists for one reason: this app is one window per workspace, and once a window has a
//! workspace there is no other way to open a second one.
//!
//! Everything else here is the standard menu, rebuilt by hand because setting a menu at all
//! replaces Tauri's default wholesale - and a menu without an Edit submenu has no Cmd+C,
//! which would break every chat box and every secret field in the app.

use tauri::menu::{
	AboutMetadata, Menu, MenuBuilder, MenuEvent, MenuItemBuilder, SubmenuBuilder, HELP_SUBMENU_ID,
	WINDOW_SUBMENU_ID,
};
use tauri::{AppHandle, Emitter, Manager, Wry};

const NEW_WINDOW: &str = "file:new-window";
const OPEN_WORKSPACE: &str = "file:open-workspace";
const RECENT: &str = "file:recent:";

pub fn build(app: &AppHandle) -> tauri::Result<Menu<Wry>> {
	let info = app.package_info();
	let about = AboutMetadata {
		name: Some(info.name.clone()),
		version: Some(info.version.to_string()),
		copyright: Some(app.config().bundle.copyright.clone().unwrap_or_default()),
		..Default::default()
	};

	let recents = crate::valid_recents(app);
	let mut recent = SubmenuBuilder::new(app, "Open Recent");
	for path in &recents {
		// The path is the id: no side table to keep in step with the menu.
		let item = MenuItemBuilder::with_id(format!("{RECENT}{path}"), crate::folder_name(path)).build(app)?;
		recent = recent.item(&item);
	}
	if recents.is_empty() {
		recent = recent.item(&MenuItemBuilder::new("No recent workspaces").enabled(false).build(app)?);
	}

	let file = SubmenuBuilder::new(app, "File")
		.item(&MenuItemBuilder::with_id(NEW_WINDOW, "New Window").accelerator("CmdOrCtrl+Shift+N").build(app)?)
		.item(&MenuItemBuilder::with_id(OPEN_WORKSPACE, "Open Workspace…").accelerator("CmdOrCtrl+O").build(app)?)
		.item(&recent.build()?)
		.separator()
		.close_window();
	#[cfg(not(target_os = "macos"))]
	let file = file.separator().quit();

	// Cmd+C, Cmd+V and Cmd+Z in the webview *are* these items. Drop this submenu and every
	// input in the app loses the clipboard.
	let edit = SubmenuBuilder::new(app, "Edit")
		.undo()
		.redo()
		.separator()
		.cut()
		.copy()
		.paste()
		.select_all()
		.build()?;

	// The ids matter: Tauri looks these two up by id and hands them to AppKit, and only then
	// does the Window menu list the open windows - which is what keeps one-window-per-
	// workspace navigable at all.
	let window = SubmenuBuilder::with_id(app, WINDOW_SUBMENU_ID, "Window")
		.minimize()
		.maximize()
		.separator()
		.close_window()
		.build()?;
	let help = SubmenuBuilder::with_id(app, HELP_SUBMENU_ID, "Help");
	#[cfg(not(target_os = "macos"))]
	let help = help.about(Some(about.clone()));
	let help = help.build()?;

	let menu = MenuBuilder::new(app);
	#[cfg(target_os = "macos")]
	let menu = menu.item(
		&SubmenuBuilder::new(app, info.name.clone())
			.about(Some(about))
			.separator()
			.services()
			.separator()
			.hide()
			.hide_others()
			.separator()
			.quit()
			.build()?,
	);
	let menu = menu.item(&file.build()?).item(&edit);
	#[cfg(target_os = "macos")]
	let menu = menu.item(&SubmenuBuilder::new(app, "View").fullscreen().build()?);
	menu.item(&window).item(&help).build()
}

/// Rebuild and reinstall the whole menu, because Open Recent is a snapshot of
/// `workspaces.json` and is stale the moment a workspace opens.
///
/// One hop to the main thread for the lot: every builder call is its own round trip
/// otherwise, and there are about thirty of them.
pub fn refresh(app: &AppHandle) {
	let handle = app.clone();
	let _ = app.run_on_main_thread(move || {
		if let Ok(menu) = build(&handle) {
			let _ = handle.set_menu(menu);
		}
	});
}

/// Who the menu click was meant for. A menu event carries no window of its own, and the
/// menu is app-wide on macOS, so the click belongs to whichever window is in front.
fn focused(app: &AppHandle) -> Option<tauri::WebviewWindow<Wry>> {
	app.webview_windows()
		.into_values()
		.find(|window| window.is_focused().unwrap_or(false))
}

/// Route a click. New Window is unambiguous and handled here; the other two depend on
/// whether the focused window already has a workspace, which only that window knows.
pub fn on_event(app: &AppHandle, event: MenuEvent) {
	let id = event.id().0.clone();
	let app = app.clone();
	// Off the event-handler thread: building a window from one deadlocks on Windows.
	tauri::async_runtime::spawn(async move {
		if id == NEW_WINDOW {
			let _ = crate::open_window(&app, None, false);
			return;
		}
		let Some(window) = focused(&app) else {
			// Nothing to ask - a picker window is the honest answer to either verb.
			let _ = crate::open_window(&app, None, id == OPEN_WORKSPACE);
			return;
		};
		if id == OPEN_WORKSPACE {
			let _ = app.emit_to(window.label(), "menu:open-workspace", ());
		} else if let Some(path) = id.strip_prefix(RECENT) {
			let _ = app.emit_to(window.label(), "menu:open-recent", path);
		}
	});
}
