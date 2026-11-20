//! Host helpers: Docker queries, opening a terminal, key pair files.

use std::path::PathBuf;
use std::process::Command;

fn valid_instance_id(id: &str) -> Result<(), String> {
    if id.starts_with("i-") && id[2..].chars().all(|c| c.is_ascii_hexdigit()) && id.len() <= 32 {
        Ok(())
    } else {
        Err(format!("invalid instance id: {id}"))
    }
}

fn container(id: &str) -> String {
    format!("ec2sim-{id}")
}

fn run(cmd: &mut Command) -> Result<String, String> {
    let out = cmd.output().map_err(|e| format!("could not run {:?}: {e}", cmd.get_program()))?;
    if out.status.success() {
        Ok(String::from_utf8_lossy(&out.stdout).into_owned())
    } else {
        Err(String::from_utf8_lossy(&out.stderr).trim().to_string())
    }
}

pub fn docker_available() -> bool {
    Command::new("docker").args(["info", "--format", "{{.ServerVersion}}"]).output()
        .map(|o| o.status.success()).unwrap_or(false)
}

/// "bastion" when ec2-sim runs its SSH jump host (Docker Desktop), else "routed".
pub fn network_mode() -> String {
    let out = Command::new("docker")
        .args(["ps", "-q", "--filter", "label=ec2sim.bastion"])
        .output();
    match out {
        Ok(o) if !o.stdout.trim_ascii().is_empty() => "bastion".into(),
        _ => "routed".into(),
    }
}

/// User-data output and container log of an instance.
pub fn console_output(id: &str) -> Result<String, String> {
    valid_instance_id(id)?;
    let ud = run(Command::new("docker").args([
        "exec", &container(id), "sh", "-c",
        "cat /var/log/cloud-init-output.log 2>/dev/null || true",
    ]))
    .unwrap_or_default();
    let logs = Command::new("docker").args(["logs", "--tail", "200", &container(id)]).output()
        .map(|o| format!("{}{}", String::from_utf8_lossy(&o.stdout), String::from_utf8_lossy(&o.stderr)))
        .unwrap_or_default();
    let mut s = String::new();
    s.push_str("── user data output (/var/log/cloud-init-output.log) ──\n");
    s.push_str(if ud.trim().is_empty() { "(no user data output yet)\n" } else { &ud });
    s.push_str("\n── instance boot log (last 200 lines) ──\n");
    s.push_str(if logs.trim().is_empty() { "(empty)\n" } else { &logs });
    Ok(s)
}

/// Open a terminal window running `command` (best effort per OS).
pub fn open_terminal(command: &str) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        let escaped = command.replace('\\', "\\\\").replace('"', "\\\"");
        return run(Command::new("osascript").args([
            "-e", &format!("tell application \"Terminal\" to do script \"{escaped}\""),
            "-e", "tell application \"Terminal\" to activate",
        ]))
        .map(|_| ());
    }
    #[cfg(target_os = "windows")]
    {
        return Command::new("cmd").args(["/C", "start", "", "cmd", "/K", command]).spawn()
            .map(|_| ()).map_err(|e| e.to_string());
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let keep_open = format!("{command}; echo; read -r -p 'Press Enter to close' _");
        let candidates: [(&str, &[&str]); 7] = [
            ("x-terminal-emulator", &["-e", "bash", "-c"]),
            ("gnome-terminal", &["--", "bash", "-c"]),
            ("konsole", &["-e", "bash", "-c"]),
            ("xfce4-terminal", &["-x", "bash", "-c"]),
            ("kitty", &["bash", "-c"]),
            ("alacritty", &["-e", "bash", "-c"]),
            ("xterm", &["-e", "bash", "-c"]),
        ];
        for (term, args) in candidates {
            let Ok(mut child) = Command::new(term).args(args).arg(&keep_open).spawn() else {
                continue;
            };
            // A terminal that dies straight away with an error (missing font,
            // no display...) counts as unavailable: try the next one. Some
            // (gnome-terminal) hand off to a server and exit 0 immediately.
            let deadline = std::time::Instant::now() + std::time::Duration::from_millis(1200);
            let failed = loop {
                match child.try_wait() {
                    Ok(Some(status)) => break !status.success(),
                    Ok(None) if std::time::Instant::now() < deadline => {
                        std::thread::sleep(std::time::Duration::from_millis(100))
                    }
                    _ => {
                        // Still running: reap it when it closes so it doesn't linger as a zombie.
                        std::thread::spawn(move || child.wait());
                        break false;
                    }
                }
            };
            if !failed {
                return Ok(());
            }
        }
        Err("No terminal emulator found (tried x-terminal-emulator, gnome-terminal, konsole, \
             xfce4-terminal, kitty, alacritty, xterm). Copy the command instead."
            .into())
    }
}

pub fn shell_command(id: &str) -> Result<String, String> {
    valid_instance_id(id)?;
    Ok(format!("docker exec -it {} bash || docker exec -it {} sh", container(id), container(id)))
}

// ------------------------------------------------------------------ key files

pub fn keys_dir() -> PathBuf {
    dirs::home_dir().unwrap_or_else(std::env::temp_dir).join(".ec2-sim").join("keys")
}

fn valid_key_name(name: &str) -> Result<(), String> {
    if !name.is_empty() && name.len() <= 255
        && name.chars().all(|c| c.is_ascii_alphanumeric() || "-_.@".contains(c))
        && !name.starts_with('.')
    {
        Ok(())
    } else {
        Err("Key names may use letters, digits, - _ . @".into())
    }
}

pub fn save_key(name: &str, material: &str) -> Result<String, String> {
    valid_key_name(name)?;
    let dir = keys_dir();
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let path = dir.join(format!("{name}.pem"));
    std::fs::write(&path, material).map_err(|e| e.to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600)).map_err(|e| e.to_string())?;
    }
    Ok(path.to_string_lossy().into_owned())
}

pub fn key_path(name: &str) -> Option<String> {
    valid_key_name(name).ok()?;
    let p = keys_dir().join(format!("{name}.pem"));
    p.exists().then(|| p.to_string_lossy().into_owned())
}

pub fn delete_key_file(name: &str) -> Result<(), String> {
    valid_key_name(name)?;
    let p = keys_dir().join(format!("{name}.pem"));
    if p.exists() {
        std::fs::remove_file(p).map_err(|e| e.to_string())?;
    }
    Ok(())
}
