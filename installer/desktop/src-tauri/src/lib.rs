// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
//! Tag.app: a window and a tray menu over the `tag` command. Installs Tag on
//! first run with the installer bundled in the app, then lists, starts and
//! adds Tags through `tag … --json` (docs/reference/app-protocol.md).

mod cli;
mod sessions;
mod tray;

use serde::Serialize;
use sessions::{Output, Sessions};
use std::path::PathBuf;
use std::process::Command;
use std::sync::Arc;
use tauri::ipc::Channel;
use tauri::{AppHandle, Manager, RunEvent, State, WindowEvent};

/// Passed by the login item so the app starts quietly in the tray.
const AUTOSTART_FLAG: &str = "--autostart";

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct AppInfo {
    platform: &'static str,
    demo: bool,
    cli: Option<String>,
    version: String,
    launched_at_login: bool,
    legacy_wanted_tags: Option<Vec<String>>,
}

#[derive(Serialize)]
struct RunResult {
    code: i32,
    stdout: String,
    stderr: String,
}

fn config_dir(app: &AppHandle) -> PathBuf {
    app.path().app_config_dir().unwrap_or_else(|_| std::env::temp_dir())
}

fn find_cli(app: &AppHandle) -> Result<PathBuf, String> {
    cli::find(&config_dir(app)).ok_or_else(|| "Tag isn't installed yet.".to_string())
}

/// Tags the earlier Swift Tag.app restored at login, from its preferences.
fn legacy_wanted_tags() -> Option<Vec<String>> {
    if !cfg!(target_os = "macos") {
        return None;
    }
    let output = cli::quiet(&mut Command::new("/usr/bin/defaults"))
        .args(["read", "team.hover.tag", "wantedRunningTags"])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    Some(parse_defaults_array(&String::from_utf8_lossy(&output.stdout)))
}

/// `defaults read` prints arrays as `(\n    "a",\n    b\n)`; quotes only when needed.
fn parse_defaults_array(text: &str) -> Vec<String> {
    let inner = text.trim().trim_start_matches('(').trim_end_matches(')');
    inner
        .split(',')
        .map(|item| item.trim().trim_matches('"').to_string())
        .filter(|item| !item.is_empty())
        .collect()
}

/// Marks the one-time carry-over from the Swift app as done, per `tag` command,
/// so a development build pointed at another Tag never affects the real one.
fn migrated_marker(app: &AppHandle, cli: &std::path::Path) -> PathBuf {
    use std::hash::{Hash, Hasher};
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    cli.hash(&mut hasher);
    config_dir(app).join(format!("migrated-from-swift-app-{:016x}", hasher.finish()))
}

#[tauri::command]
fn app_info(app: AppHandle) -> AppInfo {
    let cli = cli::find(&config_dir(&app));
    let migrated = cli.as_ref().is_some_and(|c| migrated_marker(&app, c).exists());
    AppInfo {
        platform: if cfg!(target_os = "macos") { "macos" } else if cfg!(windows) { "windows" } else { "linux" },
        demo: std::env::var_os("TAG_INSTALLER_DEMO").is_some(),
        cli: cli.map(|p| p.display().to_string()),
        version: app.package_info().version.to_string(),
        launched_at_login: std::env::args().any(|a| a == AUTOSTART_FLAG),
        legacy_wanted_tags: if migrated { None } else { legacy_wanted_tags() },
    }
}

#[tauri::command]
fn mark_migrated(app: AppHandle) -> Result<(), String> {
    let cli = find_cli(&app)?;
    let marker = migrated_marker(&app, &cli);
    std::fs::create_dir_all(config_dir(&app)).and_then(|_| std::fs::write(marker, "1")).map_err(|e| e.to_string())
}

#[tauri::command]
async fn run_tag(app: AppHandle, args: Vec<String>) -> Result<RunResult, String> {
    let cli = find_cli(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        let output = cli::command(&cli, &args).output().map_err(|e| format!("Couldn't run Tag: {e}"))?;
        Ok(RunResult {
            code: output.status.code().unwrap_or(-1),
            stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
            stderr: cli::tail(&String::from_utf8_lossy(&output.stderr)),
        })
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
fn setup_start(app: AppHandle, sessions: State<Arc<Sessions>>, args: Vec<String>, output: Channel<Output>) -> Result<u32, String> {
    let cli = find_cli(&app)?;
    let mut args = args;
    args.push("--json".into());
    sessions.start(cli::command(&cli, &args), false, output, |_| {})
}

/// The installer bundled with this app, from the same commit as the app.
fn installer_command(app: &AppHandle, channel: &str) -> Result<Command, String> {
    // Development: install this checkout instead (`TAG_INSTALLER_SOURCE=/path/to/repo`).
    if let Some(source) = std::env::var_os("TAG_INSTALLER_SOURCE") {
        let mut command = Command::new("/bin/sh");
        command.arg(PathBuf::from(source).join("install.sh"));
        return Ok(command);
    }
    let dir = app.path().resource_dir().map_err(|e| e.to_string())?.join("installer");
    let channel = if channel.is_empty() { default_channel(&dir) } else { channel.to_string() };
    let mut command = if cfg!(windows) {
        let mut c = Command::new("powershell.exe");
        c.args(["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"])
            .arg(dir.join("install.ps1"))
            .args(["-Channel", &channel]);
        c
    } else {
        let mut c = Command::new("/bin/sh");
        c.arg(dir.join("install.sh")).args(["--channel", &channel]);
        c
    };
    cli::quiet(&mut command);
    Ok(command)
}

fn default_channel(dir: &std::path::Path) -> String {
    std::fs::read_to_string(dir.join("release-channels.json"))
        .ok()
        .and_then(|text| serde_json::from_str::<serde_json::Value>(&text).ok())
        .and_then(|policy| policy["default_channel"].as_str().map(str::to_string))
        .unwrap_or_else(|| "stable".into())
}

#[tauri::command]
fn install_start(app: AppHandle, sessions: State<Arc<Sessions>>, channel: String, output: Channel<Output>) -> Result<u32, String> {
    let mut command = installer_command(&app, &channel)?;
    command.env("TAG_INSTALL_PROGRESS", "jsonl").env("NO_COLOR", "1").env("TERM", "dumb").env("PATH", cli::tool_path());
    let saved = cli::saved_command_file(&config_dir(&app));
    sessions.start(command, true, output, move |line| {
        // Remember the exact command the installer reported, so PATH never matters.
        if let Some(json) = line.split_once("@tag-progress ").map(|(_, j)| j) {
            if let Ok(event) = serde_json::from_str::<serde_json::Value>(json) {
                if let (Some("done"), Some(path)) = (event["step"].as_str(), event["command"].as_str()) {
                    if let Some(parent) = saved.parent() {
                        let _ = std::fs::create_dir_all(parent);
                    }
                    let _ = std::fs::write(&saved, path);
                }
            }
        }
    })
}

#[tauri::command]
fn session_send(sessions: State<Arc<Sessions>>, id: u32, line: String) -> Result<(), String> {
    sessions.send(id, &line)
}

#[tauri::command]
fn session_stop(sessions: State<Arc<Sessions>>, id: u32) {
    sessions.stop(id);
}

#[tauri::command]
fn update_tray(app: AppHandle, tags: Vec<tray::TrayTag>, keep_running: bool) -> Result<(), String> {
    tray::update(&app, &tags, keep_running).map_err(|e| e.to_string())
}

#[tauri::command]
fn show_window(app: AppHandle) {
    show_window_now(&app);
}

#[tauri::command]
fn quit(app: AppHandle) {
    quit_now(&app);
}

pub(crate) fn show_window_now(app: &AppHandle) {
    #[cfg(target_os = "macos")]
    let _ = app.set_activation_policy(tauri::ActivationPolicy::Regular);
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

pub(crate) fn quit_now(app: &AppHandle) {
    app.state::<Arc<Sessions>>().stop_all();
    app.exit(0);
}

#[cfg(test)]
mod tests {
    use super::parse_defaults_array;

    #[test]
    fn reads_the_swift_apps_saved_tags() {
        let text = "(\n    \"t0klovr1-a0maya01\",\n    research\n)\n";
        assert_eq!(parse_defaults_array(text), ["t0klovr1-a0maya01", "research"]);
        assert!(parse_defaults_array("(\n)\n").is_empty());
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| show_window_now(app)))
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            Some(vec![AUTOSTART_FLAG]),
        ))
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .manage(Arc::new(Sessions::default()))
        .invoke_handler(tauri::generate_handler![
            app_info, run_tag, setup_start, install_start, session_send, session_stop,
            update_tray, show_window, quit, mark_migrated
        ])
        .setup(|app| {
            tray::create(app.handle())?;
            // Opened by the login item: stay in the tray, no window or Dock icon.
            if std::env::args().any(|a| a == AUTOSTART_FLAG) {
                #[cfg(target_os = "macos")]
                app.set_activation_policy(tauri::ActivationPolicy::Accessory);
            } else {
                show_window_now(app.handle());
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            // Closing the window keeps Tag in the tray, like the menu bar app it replaces.
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
                #[cfg(target_os = "macos")]
                let _ = window.app_handle().set_activation_policy(tauri::ActivationPolicy::Accessory);
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building Tag");
    app.run(|app, event| {
        #[cfg(target_os = "macos")]
        if let RunEvent::Reopen { .. } = event {
            show_window_now(app);
        }
        if let RunEvent::ExitRequested { api, code, .. } = &event {
            // Only an explicit Quit ends the app; closing windows does not.
            if code.is_none() {
                api.prevent_exit();
            }
        }
        let _ = app;
    });
}
