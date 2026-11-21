//! Persistent UI settings (JSON in the OS config dir).

use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default)]
pub struct Settings {
    /// Folder of the ec2-sim checkout (contains pyproject.toml / .venv).
    pub ec2sim_dir: String,
    /// ec2-sim API endpoint.
    pub endpoint: String,
    /// Region the UI works in.
    pub region: String,
    /// Start the server automatically when the app opens (if not running).
    pub auto_start: bool,
    /// Extra environment for the server, one KEY=VALUE per line.
    pub extra_env: String,
    /// Windows only: where ec2-sim runs. "auto" | "wsl" | "native".
    pub backend: String,
    /// Windows only: WSL distro name ("" = default distro).
    pub wsl_distro: String,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            ec2sim_dir: default_ec2sim_dir(),
            endpoint: "http://localhost:4566".into(),
            region: "us-east-1".into(),
            auto_start: true,
            extra_env: String::new(),
            backend: "auto".into(),
            wsl_distro: String::new(),
        }
    }
}

/// EC2SIM_DEFAULT_DIR at build time, else the repo this UI was built from
/// (ui/src-tauri/../..), else ~/tools/ec2-sim.
fn default_ec2sim_dir() -> String {
    if let Some(d) = option_env!("EC2SIM_DEFAULT_DIR") {
        return d.to_string();
    }
    if cfg!(windows) {
        // On Windows ec2-sim usually lives in WSL; `~` is expanded there.
        return "~/tools/ec2-sim".into();
    }
    let built_from = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
    if built_from.join("pyproject.toml").exists() {
        if let Ok(p) = built_from.canonicalize() {
            return p.to_string_lossy().into_owned();
        }
    }
    dirs::home_dir()
        .map(|h| h.join("tools/ec2-sim").to_string_lossy().into_owned())
        .unwrap_or_default()
}

fn path() -> PathBuf {
    dirs::config_dir()
        .unwrap_or_else(std::env::temp_dir)
        .join("ec2-sim-ui")
        .join("settings.json")
}

pub fn load() -> Settings {
    std::fs::read_to_string(path())
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

pub fn save(s: &Settings) -> Result<(), String> {
    let p = path();
    if let Some(dir) = p.parent() {
        std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    let json = serde_json::to_string_pretty(s).map_err(|e| e.to_string())?;
    std::fs::write(&p, json).map_err(|e| e.to_string())
}

/// Parse KEY=VALUE lines (blank lines and # comments ignored).
pub fn env_pairs(extra: &str) -> Vec<(String, String)> {
    extra
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty() && !l.starts_with('#'))
        .filter_map(|l| l.split_once('='))
        .map(|(k, v)| (k.trim().to_string(), v.trim().to_string()))
        .collect()
}
