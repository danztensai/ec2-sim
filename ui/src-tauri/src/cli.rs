//! Run `ec2-sim <args>` (from the configured checkout or the managed install), natively or in WSL.

use std::process::Command;

use crate::source;

pub fn command(ec2sim_dir: &str, args: &[&str]) -> Result<Command, String> {
    source::command(ec2sim_dir, args)
}

/// Run and return stdout, or stderr as the error.
pub fn output(ec2sim_dir: &str, args: &[&str]) -> Result<String, String> {
    let out = command(ec2sim_dir, args)?.output().map_err(|e| format!("could not run ec2-sim: {e}"))?;
    if out.status.success() {
        Ok(String::from_utf8_lossy(&out.stdout).into_owned())
    } else {
        let err = String::from_utf8_lossy(&out.stderr);
        Err(err.lines().rev().find(|l| !l.trim().is_empty()).unwrap_or("ec2-sim failed").trim().to_string())
    }
}
