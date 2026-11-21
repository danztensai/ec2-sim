//! Run `ec2-sim <args>` (the CLI from the configured checkout), natively or in WSL.

use std::process::Command;

use crate::host;

pub fn command(ec2sim_dir: &str, args: &[&str]) -> Command {
    match host::runtime() {
        host::Runtime::Native => {
            #[cfg(windows)]
            let bin = std::path::Path::new(ec2sim_dir).join(".venv").join("Scripts").join("ec2-sim.exe");
            #[cfg(not(windows))]
            let bin = std::path::Path::new(ec2sim_dir).join(".venv").join("bin").join("ec2-sim");
            let mut c = Command::new(bin);
            c.args(args);
            host::hide_window(&mut c);
            c
        }
        host::Runtime::Wsl { .. } => {
            let quoted: Vec<String> = args.iter().map(|a| host::sh_quote(a)).collect();
            host::sh(&format!("exec {}/.venv/bin/ec2-sim {}", host::wsl_path(ec2sim_dir), quoted.join(" ")))
        }
    }
}

/// Run and return stdout, or stderr as the error.
pub fn output(ec2sim_dir: &str, args: &[&str]) -> Result<String, String> {
    let out = command(ec2sim_dir, args).output().map_err(|e| format!("could not run ec2-sim: {e}"))?;
    if out.status.success() {
        Ok(String::from_utf8_lossy(&out.stdout).into_owned())
    } else {
        let err = String::from_utf8_lossy(&out.stderr);
        Err(err.lines().rev().find(|l| !l.trim().is_empty()).unwrap_or("ec2-sim failed").trim().to_string())
    }
}
