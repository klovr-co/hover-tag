// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
//! Finding and running the `tag` command. The app does no Tag work itself.

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

#[cfg(windows)]
use std::os::windows::process::CommandExt;
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

fn home() -> PathBuf {
    std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" })
        .map(PathBuf::from)
        .unwrap_or_default()
}

/// Where the installer puts the `tag` command by default.
pub fn default_command() -> PathBuf {
    if cfg!(windows) {
        let local = std::env::var_os("LOCALAPPDATA")
            .map(PathBuf::from)
            .unwrap_or_else(|| home().join("AppData/Local"));
        local.join("Tag/bin/tag.cmd")
    } else {
        home().join(".local/bin/tag")
    }
}

/// The file remembering the command the installer reported, which wins over defaults.
pub fn saved_command_file(config_dir: &Path) -> PathBuf {
    config_dir.join("tag-command.txt")
}

/// The `tag` command to drive: TAG_CLI (development), then the path the
/// installer reported, then the default install location, then PATH.
pub fn find(config_dir: &Path) -> Option<PathBuf> {
    if let Some(cli) = std::env::var_os("TAG_CLI") {
        return Some(PathBuf::from(cli));
    }
    if let Ok(saved) = std::fs::read_to_string(saved_command_file(config_dir)) {
        let path = PathBuf::from(saved.trim());
        if path.is_file() {
            return Some(path);
        }
    }
    let default = default_command();
    if default.is_file() {
        return Some(default);
    }
    search_path(if cfg!(windows) { "tag.cmd" } else { "tag" })
}

fn search_path(name: &str) -> Option<PathBuf> {
    std::env::split_paths(&tool_path()).map(|dir| dir.join(name)).find(|p| p.is_file())
}

/// Apps opened from Finder get a minimal PATH; add where Tag and its tools live.
pub fn tool_path() -> std::ffi::OsString {
    let mut dirs: Vec<PathBuf> = Vec::new();
    if !cfg!(windows) {
        dirs.push(home().join(".local/bin"));
        dirs.extend(["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"].map(PathBuf::from));
    }
    if let Some(existing) = std::env::var_os("PATH") {
        dirs.extend(std::env::split_paths(&existing));
    }
    std::env::join_paths(dirs).unwrap_or_default()
}

/// The Python and launcher named by Tag's managed `tag.cmd` (`@"PYTHON" "LAUNCHER" %*`).
/// Running them directly keeps arguments such as `R&D` or `"` away from cmd.exe.
pub fn windows_launcher(cli: &Path) -> Option<(PathBuf, PathBuf)> {
    let text = std::fs::read_to_string(cli).ok()?;
    let line = text.lines().find(|l| l.trim_start().starts_with("@\""))?;
    let mut parts = line.trim_start().trim_start_matches('@').split('"').filter(|p| !p.trim().is_empty());
    let python = PathBuf::from(parts.next()?);
    let launcher = PathBuf::from(parts.next()?);
    (parts.next()?.trim() == "%*").then_some((python, launcher))
}

/// A Command for `tag ARGS`, with plain output and no console window on Windows.
pub fn command(cli: &Path, args: &[String]) -> Command {
    let is_script = cli.extension().is_some_and(|e| e.eq_ignore_ascii_case("cmd") || e.eq_ignore_ascii_case("bat"));
    let mut command = match windows_launcher(cli).filter(|_| cfg!(windows) && is_script) {
        Some((python, launcher)) => {
            let mut c = Command::new(python);
            c.arg(launcher);
            c
        }
        // Rust escapes arguments for batch files itself, and refuses ones it can't.
        None => Command::new(cli),
    };
    command
        .args(args)
        .env("PATH", tool_path())
        .env("NO_COLOR", "1")
        .env("TERM", "dumb")
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUTF8", "1")
        .stdin(Stdio::null());
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    command
}

/// Hide the console window for any helper process on Windows.
pub fn quiet(command: &mut Command) -> &mut Command {
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    command
}

/// The last ~4 KB of text, for error messages.
pub fn tail(text: &str) -> String {
    let start = text.len().saturating_sub(4000);
    let start = (start..text.len()).find(|&i| text.is_char_boundary(i)).unwrap_or(text.len());
    text[start..].to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tail_keeps_the_end_on_a_character_boundary() {
        let text = "é".repeat(3000);
        let kept = tail(&text);
        assert!(kept.len() <= 4000 && kept.chars().all(|c| c == 'é'));
        assert_eq!(tail("short"), "short");
    }

    #[test]
    fn reads_the_managed_windows_launcher() {
        let dir = std::env::temp_dir().join(format!("tag-cli-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let cmd = dir.join("tag.cmd");
        std::fs::write(&cmd, "@rem TAG managed launcher\n@\"C:\\Tag\\python.exe\" \"C:\\Users\\John Smith\\Tag\\bin\\tag-launch.py\" %*\n").unwrap();
        let (python, launcher) = windows_launcher(&cmd).unwrap();
        assert_eq!(python, PathBuf::from("C:\\Tag\\python.exe"));
        assert_eq!(launcher, PathBuf::from("C:\\Users\\John Smith\\Tag\\bin\\tag-launch.py"));
        std::fs::write(&cmd, "@echo off\nsomething else\n").unwrap();
        assert!(windows_launcher(&cmd).is_none());
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn tag_cli_overrides_everything() {
        std::env::set_var("TAG_CLI", "/tmp/tag-dev");
        assert_eq!(find(Path::new("/nonexistent")), Some(PathBuf::from("/tmp/tag-dev")));
        std::env::remove_var("TAG_CLI");
    }
}
