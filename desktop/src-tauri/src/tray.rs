// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
//! The menu bar (macOS) or system tray (Windows, Linux) menu. The window owns
//! the list of Tags and sends it here; clicks go back to the window as events.

use serde::{Deserialize, Serialize};
use tauri::image::Image;
use tauri::menu::{CheckMenuItem, IsMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{TrayIcon, TrayIconBuilder};
use tauri::{AppHandle, Emitter, Manager, Runtime, Wry};

#[derive(Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TrayTag {
    pub id: String,
    pub title: String,
    pub group: String,
    pub running: bool,
    pub needs_setup: bool,
}

#[derive(Clone, Serialize)]
struct TrayEvent {
    action: String,
    tag: Option<String>,
}

pub const TRAY_ID: &str = "tag";

fn icon<R: Runtime>(app: &AppHandle<R>, online: bool) -> Option<Image<'static>> {
    // macOS draws template images in the menu bar's colour; elsewhere use white on dark panels.
    let name = match (cfg!(target_os = "macos"), online) {
        (true, false) => "tray.png",
        (true, true) => "tray-online.png",
        (false, false) => "tray-light.png",
        (false, true) => "tray-light-online.png",
    };
    let path = app.path().resource_dir().ok()?.join(name);
    Image::from_path(path).ok()
}

pub fn create(app: &AppHandle) -> tauri::Result<TrayIcon> {
    let menu = build_menu(app, &[], false)?;
    let mut builder = TrayIconBuilder::with_id(TRAY_ID)
        .menu(&menu)
        .tooltip("Tag")
        .icon_as_template(cfg!(target_os = "macos"))
        .show_menu_on_left_click(true)
        .on_menu_event(|app, event| handle(app, event.id().as_ref()));
    if let Some(image) = icon(app, false).or_else(|| app.default_window_icon().cloned()) {
        builder = builder.icon(image);
    }
    builder.build(app)
}

fn handle(app: &AppHandle, id: &str) {
    match id {
        "open" => crate::show_window_now(app),
        "quit" => crate::quit_now(app),
        "add" | "settings" => {
            crate::show_window_now(app);
            let _ = app.emit("tray", TrayEvent { action: id.into(), tag: None });
        }
        _ => {
            let (action, tag) = match id.split_once(':') {
                Some((action, tag)) => (action.to_string(), Some(tag.to_string())),
                None => (id.to_string(), None),
            };
            let _ = app.emit("tray", TrayEvent { action, tag });
        }
    }
}

fn build_menu(app: &AppHandle, tags: &[TrayTag], keep_running: bool) -> tauri::Result<Menu<Wry>> {
    let online = tags.iter().filter(|t| t.running).count();
    let summary = if tags.is_empty() {
        "No Tags yet".to_string()
    } else {
        format!("{online} of {} Tags online", tags.len())
    };
    let mut items: Vec<Box<dyn IsMenuItem<Wry>>> = vec![
        Box::new(MenuItem::with_id(app, "summary", summary, false, None::<&str>)?),
        Box::new(PredefinedMenuItem::separator(app)?),
    ];
    let mut group = None;
    for tag in tags {
        if !tag.group.is_empty() && group.as_ref() != Some(&tag.group) {
            group = Some(tag.group.clone());
            items.push(Box::new(MenuItem::with_id(app, format!("group:{}", tag.group), &tag.group, false, None::<&str>)?));
        }
        if tag.needs_setup {
            let label = format!("{} — finish setup…", tag.title);
            items.push(Box::new(MenuItem::with_id(app, format!("toggle:{}", tag.id), label, true, None::<&str>)?));
        } else {
            items.push(Box::new(CheckMenuItem::with_id(
                app, format!("toggle:{}", tag.id), &tag.title, true, tag.running, None::<&str>,
            )?));
        }
    }
    if tags.iter().any(|t| !t.needs_setup) {
        items.push(Box::new(PredefinedMenuItem::separator(app)?));
        items.push(Box::new(MenuItem::with_id(app, "start-all", "Start All Tags", true, None::<&str>)?));
        items.push(Box::new(MenuItem::with_id(app, "stop-all", "Stop All Tags", true, None::<&str>)?));
    }
    items.push(Box::new(PredefinedMenuItem::separator(app)?));
    items.push(Box::new(MenuItem::with_id(app, "open", "Open Tag…", true, Some("CmdOrCtrl+O"))?));
    items.push(Box::new(MenuItem::with_id(app, "add", "Add Tag…", true, None::<&str>)?));
    items.push(Box::new(MenuItem::with_id(app, "settings", "Settings…", true, Some("CmdOrCtrl+,"))?));
    items.push(Box::new(PredefinedMenuItem::separator(app)?));
    items.push(Box::new(CheckMenuItem::with_id(app, "keep-running", "Keep Tags Running", true, keep_running, None::<&str>)?));
    items.push(Box::new(PredefinedMenuItem::separator(app)?));
    items.push(Box::new(MenuItem::with_id(app, "quit", "Quit Tag", true, Some("CmdOrCtrl+Q"))?));
    let refs: Vec<&dyn IsMenuItem<Wry>> = items.iter().map(|b| b.as_ref()).collect();
    Menu::with_items(app, &refs)
}

pub fn update(app: &AppHandle, tags: &[TrayTag], keep_running: bool) -> tauri::Result<()> {
    let Some(tray) = app.tray_by_id(TRAY_ID) else { return Ok(()) };
    tray.set_menu(Some(build_menu(app, tags, keep_running)?))?;
    let online = tags.iter().any(|t| t.running);
    if let Some(image) = icon(app, online) {
        tray.set_icon(Some(image))?;
        tray.set_icon_as_template(cfg!(target_os = "macos"))?;
    }
    Ok(())
}
