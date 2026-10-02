// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
//! Long-running conversations with Tag: guided setup and the installer.
//! Each streams its output line by line to the window over a Channel.

use serde::Serialize;
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tauri::ipc::Channel;

#[derive(Clone, Serialize)]
#[serde(tag = "event", content = "data", rename_all = "lowercase")]
pub enum Output {
    Line(String),
    Exit { code: i32, stderr: String },
}

struct Session {
    child: Arc<Mutex<Child>>,
    stdin: Option<ChildStdin>,
}

#[derive(Default)]
pub struct Sessions {
    next: AtomicU32,
    live: Mutex<HashMap<u32, Session>>,
}

impl Sessions {
    /// Start `command`, streaming stdout lines (and stderr lines too when
    /// `merge_stderr`) to `output`. `on_line` sees each line first.
    pub fn start(
        self: &Arc<Self>,
        mut command: Command,
        merge_stderr: bool,
        output: Channel<Output>,
        on_line: impl Fn(&str) + Send + Sync + 'static,
    ) -> Result<u32, String> {
        command.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
        let mut child = command.spawn().map_err(|e| format!("Couldn't start Tag: {e}"))?;
        let stdin = child.stdin.take();
        let stdout = child.stdout.take().expect("piped stdout");
        let stderr = child.stderr.take().expect("piped stderr");
        let id = self.next.fetch_add(1, Ordering::Relaxed);
        let child = Arc::new(Mutex::new(child));
        self.live.lock().unwrap().insert(id, Session { child: child.clone(), stdin });

        let on_line = Arc::new(on_line);
        let errors = Arc::new(Mutex::new(String::new()));
        let err_reader = {
            let (output, errors, on_line) = (output.clone(), errors.clone(), on_line.clone());
            std::thread::spawn(move || {
                if merge_stderr {
                    for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                        on_line(&line);
                        let _ = output.send(Output::Line(line));
                    }
                } else {
                    let mut text = String::new();
                    let _ = BufReader::new(stderr).read_to_string(&mut text);
                    *errors.lock().unwrap() = crate::cli::tail(&text);
                }
            })
        };
        let sessions = self.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                on_line(&line);
                let _ = output.send(Output::Line(line));
            }
            let _ = err_reader.join();
            let code = loop {
                if let Ok(Some(status)) = child.lock().unwrap().try_wait() {
                    break status.code().unwrap_or(-1);
                }
                std::thread::sleep(Duration::from_millis(50));
            };
            sessions.live.lock().unwrap().remove(&id);
            let stderr = errors.lock().unwrap().clone();
            let _ = output.send(Output::Exit { code, stderr });
        });
        Ok(id)
    }

    pub fn send(&self, id: u32, line: &str) -> Result<(), String> {
        let mut live = self.live.lock().unwrap();
        let stdin = live.get_mut(&id).and_then(|s| s.stdin.as_mut()).ok_or("This session has ended")?;
        writeln!(stdin, "{line}").and_then(|_| stdin.flush()).map_err(|e| e.to_string())
    }

    /// Close stdin so Tag can save and stop on its own; end it after a grace period.
    pub fn stop(&self, id: u32) {
        let Some(session) = self.live.lock().unwrap().get_mut(&id).map(|s| {
            s.stdin.take();
            s.child.clone()
        }) else {
            return;
        };
        std::thread::spawn(move || {
            for _ in 0..100 {
                if !matches!(session.lock().unwrap().try_wait(), Ok(None)) {
                    return;
                }
                std::thread::sleep(Duration::from_millis(50));
            }
            let _ = session.lock().unwrap().kill();
        });
    }

    pub fn stop_all(&self) {
        let ids: Vec<u32> = self.live.lock().unwrap().keys().copied().collect();
        for id in ids {
            self.stop(id);
        }
    }
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use std::sync::mpsc;
    use tauri::ipc::InvokeResponseBody;

    fn channel() -> (Channel<Output>, mpsc::Receiver<serde_json::Value>) {
        let (tx, rx) = mpsc::channel();
        let channel = Channel::new(move |body| {
            if let InvokeResponseBody::Json(json) = body {
                tx.send(serde_json::from_str(&json).unwrap()).unwrap();
            }
            Ok(())
        });
        (channel, rx)
    }

    fn next(rx: &mpsc::Receiver<serde_json::Value>) -> serde_json::Value {
        rx.recv_timeout(Duration::from_secs(5)).expect("output")
    }

    #[test]
    fn streams_lines_takes_answers_and_reports_exit() {
        let sessions = Arc::new(Sessions::default());
        let (output, rx) = channel();
        let seen = Arc::new(Mutex::new(Vec::new()));
        let record = seen.clone();
        let mut command = Command::new("/bin/sh");
        command.args(["-c", "echo question; read answer; echo got $answer; echo oops >&2; exit 3"]);
        let id = sessions.start(command, false, output, move |l| record.lock().unwrap().push(l.to_string())).unwrap();
        assert_eq!(next(&rx), serde_json::json!({"event": "line", "data": "question"}));
        sessions.send(id, r#"{"answer":1}"#).unwrap();
        assert_eq!(next(&rx)["data"], r#"got {"answer":1}"#);
        assert_eq!(next(&rx), serde_json::json!({"event": "exit", "data": {"code": 3, "stderr": "oops\n"}}));
        assert_eq!(seen.lock().unwrap().len(), 2);
        assert!(sessions.send(id, "late").is_err());
    }

    #[test]
    fn installer_progress_on_stderr_is_streamed_too() {
        let sessions = Arc::new(Sessions::default());
        let (output, rx) = channel();
        let mut command = Command::new("/bin/sh");
        command.args(["-c", r#"echo '@tag-progress {"step":"tools"}' >&2"#]);
        sessions.start(command, true, output, |_| {}).unwrap();
        assert_eq!(next(&rx)["data"], r#"@tag-progress {"step":"tools"}"#);
        assert_eq!(next(&rx)["event"], "exit");
    }

    #[test]
    fn stopping_closes_stdin_so_setup_can_save_first() {
        let sessions = Arc::new(Sessions::default());
        let (output, rx) = channel();
        let mut command = Command::new("/bin/sh");
        command.args(["-c", "read line || echo saved; exit 0"]);
        let id = sessions.start(command, false, output, |_| {}).unwrap();
        sessions.stop(id);
        assert_eq!(next(&rx)["data"], "saved");
        assert_eq!(next(&rx)["data"]["code"], 0);
    }
}
