//! Host helpers: running commands (natively, or inside WSL when the app runs on
//! Windows and ec2-sim/Docker live in WSL), Docker queries, terminals, key files.

use std::io::Write;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::RwLock;

// ------------------------------------------------------------------ runtime

/// Where ec2-sim, Docker and the instances live, relative to this app.
#[derive(Clone, Debug, PartialEq)]
pub enum Runtime {
    /// Same machine/OS as the app (Linux, macOS, or Windows with Docker Desktop).
    Native,
    /// The app runs on Windows; ec2-sim and Docker run inside a WSL distro.
    Wsl { distro: Option<String> },
}

static RUNTIME: RwLock<Runtime> = RwLock::new(Runtime::Native);

pub fn runtime() -> Runtime {
    RUNTIME.read().unwrap().clone()
}

/// Decide the runtime from settings (`backend`: auto | native | wsl).
pub fn configure(backend: &str, distro: &str) {
    let distro = (!distro.trim().is_empty()).then(|| distro.trim().to_string());
    let rt = if !cfg!(windows) {
        Runtime::Native
    } else {
        match backend {
            "native" => Runtime::Native,
            "wsl" => Runtime::Wsl { distro },
            // auto: Docker Desktop on Windows -> native; otherwise use WSL.
            _ if native_docker_works() => Runtime::Native,
            _ => Runtime::Wsl { distro },
        }
    };
    *RUNTIME.write().unwrap() = rt;
}

fn native_docker_works() -> bool {
    let mut c = Command::new("docker");
    c.args(["info", "--format", "{{.ServerVersion}}"]);
    hide_window(&mut c);
    c.output().map(|o| o.status.success()).unwrap_or(false)
}

/// Don't flash a console window for background commands on Windows.
pub fn hide_window(_c: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        _c.creation_flags(CREATE_NO_WINDOW);
    }
}

/// A command for `program`, run natively or inside WSL. Callers add args.
pub fn cmd(program: &str) -> Command {
    let mut c = match runtime() {
        Runtime::Native => Command::new(program),
        Runtime::Wsl { distro } => {
            // `-e` runs the program directly with exactly these arguments.
            // (`--` would hand the joined command line to bash to re-parse.)
            let mut c = Command::new("wsl.exe");
            if let Some(d) = distro {
                c.args(["-d", &d]);
            }
            c.args(["-e", program]);
            c
        }
    };
    hide_window(&mut c);
    c
}

/// `sh -c script` in the ec2-sim environment.
pub fn sh(script: &str) -> Command {
    let mut c = cmd("sh");
    c.args(["-c", script]);
    c
}

/// Prefix for commands the user runs in a terminal ("" natively, "wsl.exe -e " on Windows+WSL).
fn user_prefix() -> String {
    match runtime() {
        Runtime::Native => String::new(),
        Runtime::Wsl { distro: Some(d) } => format!("wsl.exe -d {d} -e "),
        Runtime::Wsl { distro: None } => "wsl.exe -e ".into(),
    }
}

/// Single-quote for POSIX sh.
pub fn sh_quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', r"'\''"))
}

// ------------------------------------------------------------------ helpers

fn valid_instance_id(id: &str) -> Result<(), String> {
    if id.starts_with("i-") && id.len() <= 32 && id[2..].chars().all(|c| c.is_ascii_hexdigit()) {
        Ok(())
    } else {
        Err(format!("invalid instance id: {id}"))
    }
}

fn container(id: &str) -> String {
    format!("ec2sim-{id}")
}

fn run(c: &mut Command) -> Result<String, String> {
    let out = c.output().map_err(|e| format!("could not run {:?}: {e}", c.get_program()))?;
    if out.status.success() {
        Ok(String::from_utf8_lossy(&out.stdout).into_owned())
    } else {
        Err(String::from_utf8_lossy(&out.stderr).trim().to_string())
    }
}

pub fn docker_available() -> bool {
    cmd("docker").args(["info", "--format", "{{.ServerVersion}}"]).output()
        .map(|o| o.status.success()).unwrap_or(false)
}

/// "bastion" when ec2-sim runs its SSH jump host (Docker Desktop), else "routed".
pub fn network_mode() -> String {
    match cmd("docker").args(["ps", "-q", "--filter", "label=ec2sim.bastion"]).output() {
        Ok(o) if !o.stdout.trim_ascii().is_empty() => "bastion".into(),
        _ => "routed".into(),
    }
}

/// User-data output and container log of an instance.
pub fn console_output(id: &str) -> Result<String, String> {
    valid_instance_id(id)?;
    let ud = run(cmd("docker").args([
        "exec", &container(id), "sh", "-c",
        "cat /var/log/cloud-init-output.log 2>/dev/null || true",
    ]))
    .unwrap_or_default();
    let logs = cmd("docker").args(["logs", "--tail", "200", &container(id)]).output()
        .map(|o| format!("{}{}", String::from_utf8_lossy(&o.stdout), String::from_utf8_lossy(&o.stderr)))
        .unwrap_or_default();
    let mut s = String::new();
    s.push_str("── user data output (/var/log/cloud-init-output.log) ──\n");
    s.push_str(if ud.trim().is_empty() { "(no user data output yet)\n" } else { &ud });
    s.push_str("\n── instance boot log (last 200 lines) ──\n");
    s.push_str(if logs.trim().is_empty() { "(empty)\n" } else { &logs });
    Ok(s)
}

// ------------------------------------------------------------------ commands for the user

pub fn shell_command(id: &str) -> Result<String, String> {
    valid_instance_id(id)?;
    let c = container(id);
    Ok(match runtime() {
        Runtime::Native => format!("docker exec -it {c} bash || docker exec -it {c} sh"),
        // One process inside WSL so `||` is evaluated there.
        Runtime::Wsl { .. } => format!("{}sh -c \"docker exec -it {c} bash || docker exec -it {c} sh\"", user_prefix()),
    })
}

fn safe_token(s: &str) -> bool {
    !s.is_empty() && s.chars().all(|c| c.is_ascii_alphanumeric() || "-_.@:".contains(c))
}

/// SSH command for an instance, valid where the user's terminal runs.
pub fn ssh_command(ip: &str, user: &str, key_name: &str, bastion: bool) -> Result<String, String> {
    if !safe_token(ip) || !safe_token(user) {
        return Err("invalid IP or user".into());
    }
    let mut parts = vec!["ssh".to_string()];
    if !key_name.is_empty() {
        if let Some(path) = key_path(key_name) {
            parts.push("-i".into());
            parts.push(if path.contains(' ') { format!("\"{path}\"") } else { path });
        }
    }
    if bastion {
        parts.push("-J".into());
        parts.push("ec2sim@127.0.0.1:2222".into());
    }
    parts.push("-o".into());
    parts.push("StrictHostKeyChecking=no".into());
    parts.push(format!("{user}@{ip}"));
    let ssh = parts.join(" ");
    Ok(match runtime() {
        Runtime::Native => ssh,
        // Through sh so `~/.ec2-sim/keys/...` expands inside WSL.
        Runtime::Wsl { .. } => format!("{}sh -c \"{ssh}\"", user_prefix()),
    })
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
        // `cmd /K <command>` in a new console (Windows 11 hosts it in Windows
        // Terminal when that's the default). The command line is passed
        // verbatim: cmd doesn't understand the \" escaping Rust would apply to
        // the quotes inside commands like `sh -c "a || b"`.
        use std::os::windows::process::CommandExt;
        const CREATE_NEW_CONSOLE: u32 = 0x0000_0010;
        return Command::new("cmd.exe")
            .raw_arg(format!("/K {command}"))
            .creation_flags(CREATE_NEW_CONSOLE)
            .spawn()
            .map(|_| ())
            .map_err(|e| format!("could not open a terminal: {e}"));
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

// ------------------------------------------------------------------ key files
// Keys live where ssh runs: natively in ~/.ec2-sim/keys, or inside WSL.

pub fn keys_dir() -> String {
    match runtime() {
        Runtime::Native => native_keys_dir().to_string_lossy().into_owned(),
        Runtime::Wsl { .. } => "~/.ec2-sim/keys (inside WSL)".into(),
    }
}

fn native_keys_dir() -> PathBuf {
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
    match runtime() {
        Runtime::Native => {
            let dir = native_keys_dir();
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
        Runtime::Wsl { .. } => {
            let file = format!("$HOME/.ec2-sim/keys/{name}.pem");
            let mut child = sh(&format!(
                "umask 077 && mkdir -p \"$HOME/.ec2-sim/keys\" && cat > \"{file}\" && chmod 600 \"{file}\""
            ))
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| e.to_string())?;
            child.stdin.take().unwrap().write_all(material.as_bytes()).map_err(|e| e.to_string())?;
            let out = child.wait_with_output().map_err(|e| e.to_string())?;
            if !out.status.success() {
                return Err(String::from_utf8_lossy(&out.stderr).trim().to_string());
            }
            Ok(format!("~/.ec2-sim/keys/{name}.pem"))
        }
    }
}

pub fn key_path(name: &str) -> Option<String> {
    valid_key_name(name).ok()?;
    match runtime() {
        Runtime::Native => {
            let p = native_keys_dir().join(format!("{name}.pem"));
            p.exists().then(|| p.to_string_lossy().into_owned())
        }
        Runtime::Wsl { .. } => {
            let ok = sh(&format!("test -f \"$HOME/.ec2-sim/keys/{name}.pem\"")).status().map(|s| s.success()).unwrap_or(false);
            ok.then(|| format!("~/.ec2-sim/keys/{name}.pem"))
        }
    }
}

pub fn delete_key_file(name: &str) -> Result<(), String> {
    valid_key_name(name)?;
    match runtime() {
        Runtime::Native => {
            let p = native_keys_dir().join(format!("{name}.pem"));
            if p.exists() {
                std::fs::remove_file(p).map_err(|e| e.to_string())?;
            }
            Ok(())
        }
        Runtime::Wsl { .. } => run(&mut sh(&format!("rm -f \"$HOME/.ec2-sim/keys/{name}.pem\""))).map(|_| ()),
    }
}

#[cfg(all(test, windows))]
mod windows_tests {
    use super::*;

    /// Opens a real console window and checks the command ran inside WSL with
    /// its quotes and `||` intact. Run on Windows: cargo test -- --ignored
    #[test]
    #[ignore]
    fn terminal_command_runs_in_wsl() {
        configure("wsl", "");
        let marker = "/tmp/ec2sim-ui-terminal-test";
        let _ = sh(&format!("rm -f {marker}")).status();
        // Quoting-sensitive: if the quotes were lost, `sh -c echo` would print an
        // empty line and the marker would not contain "a b|ok".
        let cmd = format!("{}sh -c \"false || echo 'a b'\\|ok > {marker}\" & exit", user_prefix());
        open_terminal(&cmd).expect("open terminal");
        let ok = (0..40).any(|_| {
            std::thread::sleep(std::time::Duration::from_millis(250));
            sh(&format!("grep -qxF 'a b|ok' {marker}")).status().map(|s| s.success()).unwrap_or(false)
        });
        let _ = sh(&format!("rm -f {marker}")).status();
        assert!(ok, "command did not run inside WSL via the terminal");
    }

    /// sh() passes the script to WSL byte-for-byte (quotes, $, ;, |).
    #[test]
    #[ignore]
    fn sh_script_is_passed_verbatim() {
        configure("wsl", "");
        let out = sh(r#"x='a "b" $c'; printf '%s|%s\n' "$x" "$(echo ok)""#).output().unwrap();
        assert_eq!(String::from_utf8_lossy(&out.stdout), "a \"b\" $c|ok\n");
    }
}
