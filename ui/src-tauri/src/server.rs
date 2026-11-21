//! Start/stop the ec2-sim server process and stream its log to the UI.

use std::collections::VecDeque;
use std::io::{BufRead, BufReader};
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter};

const MAX_LOG_LINES: usize = 3000;

#[derive(Default)]
pub struct Server {
    child: Option<Child>,
    pub logs: Arc<Mutex<VecDeque<String>>>,
}

fn push_log(logs: &Arc<Mutex<VecDeque<String>>>, app: &AppHandle, line: String) {
    {
        let mut l = logs.lock().unwrap();
        if l.len() >= MAX_LOG_LINES {
            l.pop_front();
        }
        l.push_back(line.clone());
    }
    let _ = app.emit("server-log", line);
}

use crate::host;

/// PID of the ec2-sim process the app started inside WSL.
const WSL_PID_FILE: &str = "/tmp/ec2sim-ui-server.pid";

/// A path inside WSL for sh: `~/x` -> `"$HOME"/'x'`, otherwise single-quoted.
fn wsl_dir(dir: &str) -> String {
    match dir.strip_prefix("~/") {
        Some(rest) => format!("\"$HOME\"/{}", host::sh_quote(rest)),
        None if dir == "~" => "\"$HOME\"".into(),
        None => host::sh_quote(dir),
    }
}

/// Program + args to launch ec2-sim from its checkout.
fn command_for(dir: &Path) -> Command {
    #[cfg(windows)]
    let venv_bin = dir.join(".venv").join("Scripts").join("ec2-sim.exe");
    #[cfg(not(windows))]
    let venv_bin = dir.join(".venv").join("bin").join("ec2-sim");
    if venv_bin.exists() {
        Command::new(venv_bin)
    } else {
        let mut c = Command::new("uv");
        c.args(["run", "ec2-sim"]);
        c
    }
}

impl Server {
    pub fn is_managed(&mut self) -> bool {
        match &mut self.child {
            Some(c) => match c.try_wait() {
                Ok(None) => true,
                _ => {
                    self.child = None;
                    false
                }
            },
            None => false,
        }
    }

    pub fn start(&mut self, app: &AppHandle, dir: &str, env: Vec<(String, String)>) -> Result<(), String> {
        if self.is_managed() {
            return Ok(());
        }
        let mut cmd = match host::runtime() {
            host::Runtime::Native => {
                let dir_path = Path::new(dir);
                if !dir_path.join("pyproject.toml").exists() {
                    return Err(format!(
                        "'{dir}' doesn't look like an ec2-sim folder (no pyproject.toml). Set it in Settings."
                    ));
                }
                let mut c = command_for(dir_path);
                c.current_dir(dir_path).envs(env).env("PYTHONUNBUFFERED", "1");
                host::hide_window(&mut c);
                c
            }
            host::Runtime::Wsl { .. } => {
                // Inside WSL: record the shell's PID, then `exec` so that PID becomes ec2-sim.
                let d = wsl_dir(dir);
                let ok = host::sh(&format!("test -f {d}/pyproject.toml")).status().map(|s| s.success()).unwrap_or(false);
                if !ok {
                    return Err(format!(
                        "'{dir}' (inside WSL) doesn't look like an ec2-sim folder (no pyproject.toml). Set it in Settings."
                    ));
                }
                let envs: String = env
                    .iter()
                    .filter(|(k, _)| k.chars().all(|c| c.is_ascii_alphanumeric() || c == '_'))
                    .map(|(k, v)| format!("{k}={} ", host::sh_quote(v)))
                    .collect();
                host::sh(&format!(
                    "cd {d} && echo $$ > {WSL_PID_FILE} && export PATH=\"$HOME/.local/bin:$HOME/.cargo/bin:$PATH\" && \
                     exec env {envs}PYTHONUNBUFFERED=1 sh -c 'if [ -x .venv/bin/ec2-sim ]; then exec .venv/bin/ec2-sim; else exec uv run ec2-sim; fi'"
                ))
            }
        };
        cmd.stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped());
        let mut child = cmd
            .spawn()
            .map_err(|e| format!("could not start ec2-sim ({e}). Is uv installed and on PATH?"))?;
        push_log(&self.logs, app, format!("[ui] started ec2-sim (pid {}) in {dir}", child.id()));
        for stream in [
            child.stdout.take().map(|s| Box::new(s) as Box<dyn std::io::Read + Send>),
            child.stderr.take().map(|s| Box::new(s) as Box<dyn std::io::Read + Send>),
        ]
        .into_iter()
        .flatten()
        {
            let logs = self.logs.clone();
            let app = app.clone();
            std::thread::spawn(move || {
                for line in BufReader::new(stream).lines().map_while(Result::ok) {
                    push_log(&logs, &app, line);
                }
            });
        }
        self.child = Some(child);
        Ok(())
    }

    /// Ask ec2-sim to shut down cleanly (it removes its containers on exit).
    pub fn stop(&mut self, app: &AppHandle) -> Result<(), String> {
        let Some(mut child) = self.child.take() else {
            return Ok(());
        };
        match host::runtime() {
            // SIGTERM lets ec2-sim remove its containers before exiting.
            host::Runtime::Wsl { .. } => {
                let _ = host::sh(&format!("kill -TERM \"$(cat {WSL_PID_FILE})\" 2>/dev/null; rm -f {WSL_PID_FILE}")).status();
            }
            host::Runtime::Native => {
                #[cfg(unix)]
                unsafe {
                    libc::kill(child.id() as i32, libc::SIGTERM);
                }
                #[cfg(not(unix))]
                let _ = child.kill();
            }
        }
        let deadline = Instant::now() + Duration::from_secs(20);
        loop {
            match child.try_wait() {
                Ok(Some(_)) => break,
                Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(200)),
                _ => {
                    let _ = child.kill();
                    let _ = child.wait();
                    break;
                }
            }
        }
        push_log(&self.logs, app, "[ui] ec2-sim stopped".into());
        Ok(())
    }
}
