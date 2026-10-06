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
use std::sync::{Arc, Mutex};
use tauri::ipc::Channel;
use tauri::{AppHandle, Manager, RunEvent, State, WindowEvent};

/// Where each release line publishes its newest Tag.app (see scripts/desktop_update_manifest.py).
const UPDATE_BASE: &str = "https://github.com/klovr-co/hover-tag/releases/download/channels";

/// The release line this build follows: alpha builds also take newer betas
/// and stable releases, which the manifests for each line already include.
fn update_channel(version: &str) -> &'static str {
    if version.contains("-alpha") {
        "alpha"
    } else if version.contains("-beta") {
        "beta"
    } else {
        "stable"
    }
}

#[derive(Default)]
struct PendingUpdate(Mutex<Option<tauri_plugin_updater::Update>>);

#[derive(Serialize)]
struct AppUpdate {
    version: String,
    notes: Option<String>,
}

/// The Tag.app feed to check: the channel the person chose in Settings (saved by
/// `tag upgrade --channel`), or this build's own line when Tag follows none.
fn feed_channel(requested: Option<&str>, version: &str) -> Result<&'static str, String> {
    match requested {
        None => Ok(update_channel(version)),
        Some("stable") => Ok("stable"),
        Some("beta") => Ok("beta"),
        Some("alpha") => Ok("alpha"),
        Some(other) => Err(format!("Tag.app has no update feed for the {other} channel.")),
    }
}

/// Explain missing feeds without mistaking an unsuccessful check for no update.
fn update_check_error(error: tauri_plugin_updater::Error, channel: &str) -> String {
    use tauri_plugin_updater::Error;
    match error {
        Error::ReleaseNotFound => format!(
            "Tag.app's {channel} update feed is unavailable. Check again later or choose another release channel."
        ),
        Error::TargetNotFound(_) | Error::TargetsNotFound(_) => format!(
            "A Tag.app update for this computer isn't available on {channel} yet. Check again later or choose another release channel."
        ),
        other => format!("Couldn't check for Tag.app updates: {other}"),
    }
}

/// Ask the release line's manifest for a newer, signed Tag.app.
#[tauri::command]
async fn app_update_check(
    app: AppHandle,
    pending: State<'_, PendingUpdate>,
    channel: Option<String>,
) -> Result<Option<AppUpdate>, String> {
    use tauri_plugin_updater::UpdaterExt;
    *pending.0.lock().unwrap() = None;
    let version = app.package_info().version.to_string();
    let channel = feed_channel(channel.as_deref(), &version)?;
    let url = format!("{UPDATE_BASE}/tag-app-{channel}.json");
    let updater = app
        .updater_builder()
        .endpoints(vec![url.parse().map_err(|e| format!("{e}"))?])
        .map_err(|e| e.to_string())?
        .build()
        .map_err(|e| e.to_string())?;
    let update = updater.check().await.map_err(|e| update_check_error(e, channel))?;
    let found = update.as_ref().map(|u| AppUpdate { version: u.version.clone(), notes: u.body.clone() });
    *pending.0.lock().unwrap() = update;
    Ok(found)
}

/// Download, verify the signature, install, and restart into the new version.
#[tauri::command]
async fn app_update_install(app: AppHandle, pending: State<'_, PendingUpdate>, version: String) -> Result<(), String> {
    let update = pending.0.lock().unwrap().take().ok_or("No update is ready; check again.")?;
    if update.version != version {
        return Err("The update changed. Check again to finish updating Tag.".into());
    }
    update.download_and_install(|_, _| {}, || {}).await.map_err(|e| e.to_string())?;
    app.state::<Arc<Sessions>>().stop_all();
    app.restart();
}

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
    channel_initialized: bool,
    legacy_wanted_tags: Option<Vec<String>>,
    first_name: Option<String>,
    /// A development build (`./tag app`, `npm run tauri -- dev`): it never updates itself.
    development: bool,
}

/// The first word of the account's full name, for Home's greeting. Windows
/// keeps only a login name, so Home greets without a name there.
fn first_name() -> Option<String> {
    #[cfg(target_os = "macos")]
    let full = Command::new("id").arg("-F").output().ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).into_owned());
    #[cfg(target_os = "linux")]
    let full = std::env::var("USER").ok().and_then(|user| Command::new("getent").args(["passwd", &user]).output().ok())
        .filter(|o| o.status.success())
        .and_then(|o| gecos_name(&String::from_utf8_lossy(&o.stdout)));
    #[cfg(windows)]
    let full: Option<String> = None;
    full.as_deref().and_then(first_word)
}

/// The full name in a passwd line's comment field, before any extra comma fields.
#[cfg_attr(not(target_os = "linux"), allow(dead_code))]
fn gecos_name(line: &str) -> Option<String> {
    line.trim().split(':').nth(4).map(|g| g.split(',').next().unwrap_or("").to_string())
}

fn first_word(full: &str) -> Option<String> {
    full.split_whitespace().next().map(str::to_string)
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
    migration_marker(&config_dir(app), cli, "migrated-from-swift-app")
}

fn migration_marker(config: &std::path::Path, cli: &std::path::Path, name: &str) -> PathBuf {
    use std::hash::{Hash, Hasher};
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    cli.hash(&mut hasher);
    config.join(format!("{name}-{:016x}", hasher.finish()))
}

const CHANNEL_MIGRATION: &str = "app-release-channel-v1";

#[tauri::command]
fn app_info(app: AppHandle) -> AppInfo {
    let cli = cli::find(&config_dir(&app));
    let migrated = cli.as_ref().is_some_and(|c| migrated_marker(&app, c).exists());
    let channel_initialized = cli.as_ref().is_some_and(|c| migration_marker(&config_dir(&app), c, CHANNEL_MIGRATION).exists());
    AppInfo {
        platform: if cfg!(target_os = "macos") { "macos" } else if cfg!(windows) { "windows" } else { "linux" },
        demo: std::env::var_os("TAG_INSTALLER_DEMO").is_some(),
        cli: cli.map(|p| p.display().to_string()),
        version: app.package_info().version.to_string(),
        launched_at_login: std::env::args().any(|a| a == AUTOSTART_FLAG),
        channel_initialized,
        legacy_wanted_tags: if migrated { None } else { legacy_wanted_tags() },
        first_name: first_name(),
        development: cfg!(debug_assertions),
    }
}

#[tauri::command]
fn mark_migrated(app: AppHandle) -> Result<(), String> {
    let cli = find_cli(&app)?;
    let marker = migrated_marker(&app, &cli);
    std::fs::create_dir_all(config_dir(&app)).and_then(|_| std::fs::write(marker, "1")).map_err(|e| e.to_string())
}

#[tauri::command]
fn mark_channel_initialized(app: AppHandle) -> Result<(), String> {
    let cli = find_cli(&app)?;
    let marker = migration_marker(&config_dir(&app), &cli, CHANNEL_MIGRATION);
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
    let version = app.package_info().version.to_string();
    let channel = feed_channel(if channel.is_empty() { None } else { Some(channel) }, &version)?;
    let mut command = if cfg!(windows) {
        let mut c = Command::new("powershell.exe");
        c.args(["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"])
            .arg(dir.join("install.ps1"))
            .args(["-Channel", channel]);
        c
    } else {
        let mut c = Command::new("/bin/sh");
        c.arg(dir.join("install.sh")).args(["--channel", channel]);
        c
    };
    cli::quiet(&mut command);
    Ok(command)
}

#[tauri::command]
fn install_start(app: AppHandle, sessions: State<Arc<Sessions>>, channel: String, output: Channel<Output>) -> Result<u32, String> {
    let mut command = installer_command(&app, &channel)?;
    command
        .env("TAG_INSTALL_PROGRESS", "jsonl")
        .env("NO_COLOR", "1")
        .env("TERM", "dumb")
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .env("PATH", cli::tool_path());
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
    // Let setup save its progress and the installer stop before exiting.
    let sessions = app.state::<Arc<Sessions>>().inner().clone();
    sessions.stop_all();
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.hide();
    }
    let app = app.clone();
    std::thread::spawn(move || {
        sessions.wait_all(std::time::Duration::from_secs(6));
        app.exit(0);
    });
}

#[cfg(test)]
mod tests {
    use super::{first_word, gecos_name, parse_defaults_array};

    #[test]
    fn asset_scope_allows_tag_pictures_in_the_hidden_private_home() {
        let config: serde_json::Value = serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        let patterns = config["app"]["security"]["assetProtocol"]["scope"].as_array().unwrap();
        // These are Tauri's Unix match options; ** does not cross hidden directories.
        let options = glob::MatchOptions {
            require_literal_separator: true,
            require_literal_leading_dot: true,
            ..Default::default()
        };
        let allowed = |path: &str| patterns.iter().any(|pattern| {
            glob::Pattern::new(pattern.as_str().unwrap()).unwrap().matches_with(path, options)
        });
        assert!(allowed("$HOME/Tag/default/.tag/state/slack-avatar-abc.png"));
        assert!(allowed("$HOME/Tag/default/.tag/state/workspace-icon.png"));
        assert!(allowed("$HOME/Tag/default/.tag/integrations/slack-cli/assets/tag-profile.png"));
        assert!(!allowed("$HOME/Tag/default/.tag/config/settings.json"));
        assert!(!allowed("$HOME/Tag/default/.tag/state/slack-avatar.json"));
    }

    #[test]
    fn greets_by_the_first_word_of_the_account_name() {
        assert_eq!(first_word("Maya Chen\n").as_deref(), Some("Maya"));
        assert_eq!(first_word("  \n"), None);
        assert_eq!(gecos_name("maya:x:1000:1000:Maya Chen,,,:/home/maya:/bin/bash").as_deref(), Some("Maya Chen"));
        assert_eq!(gecos_name("maya:x:1000:1000::/home/maya:/bin/bash").as_deref(), Some(""));
    }

    #[test]
    fn each_build_follows_its_own_release_line() {
        assert_eq!(super::update_channel("0.3.0-alpha.2"), "alpha");
        assert_eq!(super::update_channel("0.3.0-beta.1"), "beta");
        assert_eq!(super::update_channel("0.3.0"), "stable");
    }

    #[test]
    fn channel_migration_is_versioned_and_isolated_from_other_installations() {
        use std::path::Path;
        let config = Path::new("/config");
        let cli = Path::new("/user/bin/tag");
        let marker = super::migration_marker(config, cli, super::CHANNEL_MIGRATION);
        assert_eq!(marker, super::migration_marker(config, cli, super::CHANNEL_MIGRATION));
        assert_ne!(marker, super::migration_marker(config, Path::new("/dev/bin/tag"), super::CHANNEL_MIGRATION));
        assert_ne!(marker, super::migration_marker(config, cli, "app-release-channel-v2"));
        assert_ne!(marker, super::migration_marker(config, cli, "migrated-from-swift-app"));
    }

    #[test]
    fn follows_the_channel_chosen_in_settings() {
        assert_eq!(super::feed_channel(None, "0.3.0-alpha.2"), Ok("alpha"));
        assert_eq!(super::feed_channel(Some("stable"), "0.3.0-alpha.2"), Ok("stable"));
        assert_eq!(super::feed_channel(Some("beta"), "0.3.0"), Ok("beta"));
        // Only real feeds: edge has none, and nothing else can reach the URL.
        assert!(super::feed_channel(Some("edge"), "0.3.0").is_err());
        assert!(super::feed_channel(Some("../stable"), "0.3.0").is_err());
    }

    #[test]
    fn explains_unavailable_desktop_feeds_and_platforms() {
        use tauri_plugin_updater::Error;
        let missing = super::update_check_error(Error::ReleaseNotFound, "alpha");
        assert!(missing.contains("alpha update feed is unavailable"));
        assert!(missing.contains("choose another release channel"));
        for error in [Error::TargetNotFound("darwin-aarch64".into()), Error::TargetsNotFound(vec![])] {
            let message = super::update_check_error(error, "beta");
            assert!(message.contains("this computer isn't available on beta"));
        }
        let network = super::update_check_error(Error::Network("offline".into()), "stable");
        assert!(network.contains("Couldn't check"));
        assert!(network.contains("offline"));
    }

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
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(Arc::new(Sessions::default()))
        .manage(PendingUpdate::default())
        .invoke_handler(tauri::generate_handler![
            app_info, run_tag, setup_start, install_start, session_send, session_stop,
            update_tray, show_window, quit, mark_migrated, mark_channel_initialized,
            app_update_check, app_update_install
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
