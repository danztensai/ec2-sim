//! Start/stop the ec2-sim server process and stream its log to the UI.

use std::collections::VecDeque;
use std::io::{BufRead, BufReader};
use std::path::Path;
use std::process::{Child, Stdio};
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
use crate::source::{self, Source};

/// PID of the ec2-sim process the app started inside WSL.
const WSL_PID_FILE: &str = "/tmp/ec2sim-ui-server.pid";

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
        let src = source::source(dir);
        if src == Source::Managed {
            let (logs, app2) = (self.logs.clone(), app.clone());
            source::ensure_managed(&move |line| push_log(&logs, &app2, line))?;
        }
        let mut cmd = match host::runtime() {
            host::Runtime::Native => {
                let mut c = source::native_command(&src, &[]);
                if let Source::Checkout(d) = &src {
                    c.current_dir(Path::new(d));
                }
                c.envs(env).env("PYTHONUNBUFFERED", "1");
                c
            }
            host::Runtime::Wsl { .. } => {
                // Inside WSL: record the shell's PID, then `exec` so that PID becomes ec2-sim.
                let envs: String = env
                    .iter()
                    .filter(|(k, _)| k.chars().all(|c| c.is_ascii_alphanumeric() || c == '_'))
                    .map(|(k, v)| format!("export {k}={}; ", host::sh_quote(v)))
                    .collect();
                host::sh(&format!(
                    "echo $$ > {WSL_PID_FILE}; {envs}export PYTHONUNBUFFERED=1; {}",
                    source::wsl_exec(&src, "")
                ))
            }
        };
        cmd.stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped());
        let mut child = cmd
            .spawn()
            .map_err(|e| format!("could not start ec2-sim ({e}). Is uv installed and on PATH?"))?;
        push_log(&self.logs, app, format!("[ui] started ec2-sim (pid {}) from {}", child.id(), match &src {
            Source::Checkout(d) => format!("checkout {d}"),
            Source::Managed => format!("{} (managed)", source::spec()),
        }));
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
