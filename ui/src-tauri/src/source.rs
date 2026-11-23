//! Where the ec2-sim CLI/server comes from.
//!
//! - **Checkout**: the configured folder contains `pyproject.toml` (a git clone).
//!   Run its `.venv/bin/ec2-sim`, or `uv run --project <dir> ec2-sim`.
//! - **Managed** (any other folder, e.g. a fresh install with the default
//!   setting): the app installs the ec2-sim version matching its own release
//!   with `uv tool install` into `~/.ec2-sim/{tools,bin}`, kept apart from the
//!   user's other uv tools, and reinstalls it when the app is updated.
//!
//! On Windows with WSL every path here lives inside WSL.

use std::path::PathBuf;
use std::process::Command;
use std::sync::{Mutex, OnceLock};

use crate::host;

pub const REPO: &str = "https://github.com/danztensai/ec2-sim";

static APP_VERSION: OnceLock<String> = OnceLock::new();
/// The spec the managed install was last verified for (skip re-checking every call).
static VERIFIED: Mutex<Option<String>> = Mutex::new(None);

pub fn set_app_version(v: String) {
    let _ = APP_VERSION.set(v);
}

/// What `uv tool install --from` gets: EC2SIM_SERVER_SPEC, else the git tag of this app's release.
pub fn spec() -> String {
    if let Ok(s) = std::env::var("EC2SIM_SERVER_SPEC") {
        if !s.trim().is_empty() {
            return s.trim().to_string();
        }
    }
    let v = APP_VERSION.get().map(String::as_str).unwrap_or(env!("CARGO_PKG_VERSION"));
    format!("git+{REPO}@v{v}")
}

#[derive(Clone, Debug, PartialEq)]
pub enum Source {
    Checkout(String),
    Managed,
}

pub fn source(dir: &str) -> Source {
    let dir = dir.trim();
    if dir.is_empty() {
        return Source::Managed;
    }
    let is_checkout = match host::runtime() {
        host::Runtime::Native => expand(dir).join("pyproject.toml").exists(),
        host::Runtime::Wsl { .. } => host::sh(&format!("test -f {}/pyproject.toml", host::wsl_path(dir)))
            .status()
            .map(|s| s.success())
            .unwrap_or(false),
    };
    if is_checkout { Source::Checkout(dir.to_string()) } else { Source::Managed }
}

/// Human-readable description for logs and Settings.
pub fn describe(dir: &str) -> String {
    match source(dir) {
        Source::Checkout(d) => format!("checkout {d}"),
        Source::Managed => format!("managed install of {} in ~/.ec2-sim", spec()),
    }
}

fn expand(dir: &str) -> PathBuf {
    match dir.strip_prefix("~/") {
        Some(rest) => dirs::home_dir().unwrap_or_default().join(rest),
        None => PathBuf::from(dir),
    }
}

fn managed_root() -> PathBuf {
    dirs::home_dir().unwrap_or_default().join(".ec2-sim")
}

fn managed_bin() -> PathBuf {
    let bin = managed_root().join("bin");
    if cfg!(windows) { bin.join("ec2-sim.exe") } else { bin.join("ec2-sim") }
}

/// PATH additions so `uv` is found when the app wasn't started from a shell.
const WSL_PATH: &str = "export PATH=\"$HOME/.local/bin:$HOME/.cargo/bin:$PATH\"";

/// Install (or update) the managed ec2-sim so it matches `spec()`. Cheap once
/// verified; the first install takes a minute (uv fetches Python if needed).
pub fn ensure_managed(log: &dyn Fn(String)) -> Result<(), String> {
    let spec = spec();
    if VERIFIED.lock().unwrap().as_deref() == Some(spec.as_str()) {
        return Ok(());
    }
    let marker_ok = |content: Option<String>| content.map(|c| c.trim() == spec).unwrap_or(false);
    match host::runtime() {
        host::Runtime::Native => {
            let marker = managed_root().join("bin").join(".ui-spec");
            if !(managed_bin().exists() && marker_ok(std::fs::read_to_string(&marker).ok())) {
                log(format!("[ui] installing ec2-sim ({spec}) with uv, first run takes a minute…"));
                let mut c = Command::new("uv");
                c.args(["tool", "install", "--force", "--python", ">=3.12", "--from", &spec, "ec2-sim"])
                    .env("UV_TOOL_DIR", managed_root().join("tools"))
                    .env("UV_TOOL_BIN_DIR", managed_root().join("bin"));
                host::hide_window(&mut c);
                let out = c.output().map_err(|e| {
                    format!("could not run uv ({e}). Install uv (https://docs.astral.sh/uv/) or set an ec2-sim checkout in Settings.")
                })?;
                if !out.status.success() {
                    return Err(last_line(&out.stderr, "uv tool install failed"));
                }
                std::fs::write(&marker, &spec).map_err(|e| e.to_string())?;
                log("[ui] ec2-sim installed".into());
            }
        }
        host::Runtime::Wsl { .. } => {
            let q = host::sh_quote(&spec);
            let check = format!(
                "[ -x \"$HOME/.ec2-sim/bin/ec2-sim\" ] && [ \"$(cat \"$HOME/.ec2-sim/bin/.ui-spec\" 2>/dev/null)\" = {q} ]"
            );
            let installed = host::sh(&check).status().map(|s| s.success()).unwrap_or(false);
            if !installed {
                log(format!("[ui] installing ec2-sim ({spec}) in WSL with uv, first run takes a minute…"));
                let script = format!(
                    "{WSL_PATH}; command -v uv >/dev/null || {{ echo 'uv is not installed in WSL: curl -LsSf https://astral.sh/uv/install.sh | sh' >&2; exit 127; }}; \
                     UV_TOOL_DIR=\"$HOME/.ec2-sim/tools\" UV_TOOL_BIN_DIR=\"$HOME/.ec2-sim/bin\" \
                     uv tool install --force --python '>=3.12' --from {q} ec2-sim && printf %s {q} > \"$HOME/.ec2-sim/bin/.ui-spec\""
                );
                let out = host::sh(&script).output().map_err(|e| format!("could not run wsl.exe: {e}"))?;
                if !out.status.success() {
                    return Err(last_line(&out.stderr, "uv tool install failed"));
                }
                log("[ui] ec2-sim installed".into());
            }
        }
    }
    *VERIFIED.lock().unwrap() = Some(spec);
    Ok(())
}

fn last_line(bytes: &[u8], fallback: &str) -> String {
    let text = String::from_utf8_lossy(bytes);
    text.lines().rev().find(|l| !l.trim().is_empty()).unwrap_or(fallback).trim().to_string()
}

/// Native: a Command running `ec2-sim <args>` from `src`.
pub fn native_command(src: &Source, args: &[&str]) -> Command {
    let mut c = match src {
        Source::Checkout(dir) => {
            let dir = expand(dir);
            let venv = if cfg!(windows) {
                dir.join(".venv").join("Scripts").join("ec2-sim.exe")
            } else {
                dir.join(".venv").join("bin").join("ec2-sim")
            };
            if venv.exists() {
                Command::new(venv)
            } else {
                let mut c = Command::new("uv");
                c.args(["run", "--project"]).arg(&dir).arg("ec2-sim");
                c
            }
        }
        Source::Managed => Command::new(managed_bin()),
    };
    c.args(args);
    host::hide_window(&mut c);
    c
}

/// WSL: a shell snippet that `exec`s `ec2-sim <args>` (args already shell-quoted),
/// so callers can record `$$` first and it stays ec2-sim's PID.
pub fn wsl_exec(src: &Source, quoted_args: &str) -> String {
    match src {
        Source::Checkout(dir) => {
            let d = host::wsl_path(dir);
            format!(
                "{WSL_PATH}; if [ -x {d}/.venv/bin/ec2-sim ]; then exec {d}/.venv/bin/ec2-sim {quoted_args}; \
                 else exec uv run --project {d} ec2-sim {quoted_args}; fi"
            )
        }
        Source::Managed => format!("{WSL_PATH}; exec \"$HOME/.ec2-sim/bin/ec2-sim\" {quoted_args}"),
    }
}

/// `ec2-sim <args>` for the configured folder, installing the managed copy if needed.
pub fn command(dir: &str, args: &[&str]) -> Result<Command, String> {
    let src = source(dir);
    if src == Source::Managed {
        ensure_managed(&|_| {})?;
    }
    Ok(match host::runtime() {
        host::Runtime::Native => native_command(&src, args),
        host::Runtime::Wsl { .. } => {
            let quoted: Vec<String> = args.iter().map(|a| host::sh_quote(a)).collect();
            host::sh(&wsl_exec(&src, &quoted.join(" ")))
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn spec_follows_app_version() {
        set_app_version("0.1.7".into());
        if std::env::var("EC2SIM_SERVER_SPEC").is_err() {
            assert_eq!(spec(), "git+https://github.com/danztensai/ec2-sim@v0.1.7");
        }
    }

    /// Real install through uv, then run a command from it. Needs uv and network/cache:
    /// EC2SIM_SERVER_SPEC=/path/to/ec2-sim HOME=$(mktemp -d) cargo test -- --ignored managed_
    #[test]
    #[ignore]
    fn managed_install_runs_ec2sim() {
        let stack = concat!(env!("CARGO_MANIFEST_DIR"), "/../../examples/stacks/hello/stack.yaml");
        let out = command("", &["validate", stack]).expect("install").output().unwrap();
        assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
        assert!(String::from_utf8_lossy(&out.stdout).contains("ok (stack hello"));
        assert!(managed_bin().exists());
        // second call: already installed, no reinstall
        assert!(command("", &["validate", stack]).unwrap().output().unwrap().status.success());
    }

    #[test]
    fn non_checkout_is_managed() {
        assert_eq!(source(""), Source::Managed);
        assert_eq!(source("/definitely/not/here"), Source::Managed);
        let repo = concat!(env!("CARGO_MANIFEST_DIR"), "/../..");
        assert_eq!(source(repo), Source::Checkout(repo.to_string()));
    }
}
