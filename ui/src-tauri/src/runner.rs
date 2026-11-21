//! "Run script": run a project's own command against ec2-sim via `ec2-sim run`
//! (a disposable runner container), streaming its output to the UI.
//! Variable values travel in the process environment, never on a command line.

use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use tauri::{AppHandle, Emitter};

use crate::host;

#[derive(Default)]
pub struct Runner {
    child: Option<Arc<Mutex<Child>>>,
}

fn valid_name(k: &str) -> bool {
    !k.is_empty() && k.chars().all(|c| c.is_ascii_alphanumeric() || c == '_') && !k.starts_with(|c: char| c.is_ascii_digit())
}

/// `ec2-sim run -C <dir> --region <r> -e K… -- <command>` for this runtime.
fn build(ec2sim_dir: &str, dir: &str, command: &str, region: &str, env: &[(String, String)]) -> Result<Command, String> {
    let names: Vec<&str> = env.iter().map(|(k, _)| k.as_str()).collect();
    if let Some(bad) = names.iter().find(|k| !valid_name(k)) {
        return Err(format!("invalid variable name: {bad}"));
    }
    let mut c = match host::runtime() {
        host::Runtime::Native => {
            #[cfg(windows)]
            let bin = std::path::Path::new(ec2sim_dir).join(".venv").join("Scripts").join("ec2-sim.exe");
            #[cfg(not(windows))]
            let bin = std::path::Path::new(ec2sim_dir).join(".venv").join("bin").join("ec2-sim");
            let mut c = Command::new(bin);
            c.args(["run", "-C", dir, "--region", region]);
            for k in &names {
                c.args(["-e", k]);
            }
            c.args(["--", command]);
            host::hide_window(&mut c);
            c
        }
        host::Runtime::Wsl { .. } => {
            // Values reach WSL through WSLENV; names go on the (quoted) command line.
            let e_args: String = names.iter().map(|k| format!(" -e {k}")).collect();
            let script = format!(
                "exec {}/.venv/bin/ec2-sim run -C {} --region {}{e_args} -- {}",
                host::wsl_path(ec2sim_dir),
                host::wsl_path(dir),
                host::sh_quote(region),
                host::sh_quote(command),
            );
            let mut c = host::sh(&script);
            let wslenv: String = names.iter().map(|k| format!("{k}/u")).collect::<Vec<_>>().join(":");
            let merged = match std::env::var("WSLENV") {
                Ok(existing) if !existing.is_empty() => format!("{existing}:{wslenv}"),
                _ => wslenv,
            };
            c.env("WSLENV", merged);
            c
        }
    };
    c.envs(env.iter().map(|(k, v)| (k.as_str(), v.as_str())));
    Ok(c)
}

impl Runner {
    pub fn is_running(&mut self) -> bool {
        let Some(child) = &self.child else { return false };
        let done = matches!(child.lock().unwrap().try_wait(), Ok(Some(_)) | Err(_));
        if done {
            self.child = None;
        }
        !done
    }

    pub fn start(
        &mut self,
        app: &AppHandle,
        ec2sim_dir: &str,
        dir: &str,
        command: &str,
        region: &str,
        env: Vec<(String, String)>,
    ) -> Result<(), String> {
        if self.is_running() {
            return Err("A script is already running. Stop it first.".into());
        }
        if dir.trim().is_empty() || command.trim().is_empty() {
            return Err("Folder and command are required.".into());
        }
        let mut cmd = build(ec2sim_dir, dir.trim(), command.trim(), region, &env)?;
        cmd.stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped());
        let mut child = cmd.spawn().map_err(|e| format!("could not start ec2-sim run: {e}"))?;
        let streams = [
            child.stdout.take().map(|s| Box::new(s) as Box<dyn std::io::Read + Send>),
            child.stderr.take().map(|s| Box::new(s) as Box<dyn std::io::Read + Send>),
        ];
        let child = Arc::new(Mutex::new(child));
        let mut readers = Vec::new();
        for stream in streams.into_iter().flatten() {
            let app = app.clone();
            readers.push(std::thread::spawn(move || {
                for line in BufReader::new(stream).lines().map_while(Result::ok) {
                    let _ = app.emit("run-output", line);
                }
            }));
        }
        // Report the exit code once output is drained.
        let waiter = child.clone();
        let app2 = app.clone();
        std::thread::spawn(move || {
            for r in readers {
                let _ = r.join();
            }
            let code = loop {
                if let Ok(Some(status)) = waiter.lock().unwrap().try_wait() {
                    break status.code().unwrap_or(-1);
                }
                std::thread::sleep(std::time::Duration::from_millis(100));
            };
            let _ = app2.emit("run-exit", code);
        });
        self.child = Some(child);
        Ok(())
    }

    /// Stop the runner container(s) and the local process.
    pub fn stop(&mut self, ec2sim_dir: &str) {
        let stop = match host::runtime() {
            host::Runtime::Native => {
                #[cfg(windows)]
                let bin = std::path::Path::new(ec2sim_dir).join(".venv").join("Scripts").join("ec2-sim.exe");
                #[cfg(not(windows))]
                let bin = std::path::Path::new(ec2sim_dir).join(".venv").join("bin").join("ec2-sim");
                let mut c = Command::new(bin);
                c.arg("stop-runs");
                host::hide_window(&mut c);
                c.status()
            }
            host::Runtime::Wsl { .. } => {
                host::sh(&format!("{}/.venv/bin/ec2-sim stop-runs", host::wsl_path(ec2sim_dir))).status()
            }
        };
        let _ = stop;
        if let Some(child) = self.child.take() {
            let _ = child.lock().unwrap().kill();
        }
    }
}
