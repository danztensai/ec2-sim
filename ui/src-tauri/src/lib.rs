//! ec2-sim desktop UI: Tauri commands bridging the React frontend to the
//! ec2-sim server process, its EC2 API, and the local Docker engine.

mod cli;
mod ec2;
mod host;
mod runner;
mod server;
mod settings;

use serde::Serialize;
use serde_json::Value;
use std::collections::BTreeMap;
use std::sync::Mutex;
use tauri::{Manager, State};

struct AppState {
    server: Mutex<server::Server>,
    settings: Mutex<settings::Settings>,
    runner: Mutex<runner::Runner>,
}

#[derive(Serialize)]
struct Status {
    /// API answers on the endpoint.
    running: bool,
    /// The UI started it (so it can stop it).
    managed: bool,
    docker: bool,
    /// "routed" or "bastion" (Docker Desktop: SSH via 127.0.0.1:2222).
    network_mode: String,
    keys_dir: String,
    /// "linux" | "macos" | "windows"
    platform: String,
    /// The app runs on Windows and drives ec2-sim inside WSL.
    via_wsl: bool,
}

#[tauri::command]
async fn status(state: State<'_, AppState>) -> Result<Status, String> {
    let endpoint = state.settings.lock().unwrap().endpoint.clone();
    let managed = state.server.lock().unwrap().is_managed();
    let running = ec2::ping(&endpoint).await;
    let (docker, network_mode) = tauri::async_runtime::spawn_blocking(|| {
        (host::docker_available(), host::network_mode())
    })
    .await
    .map_err(|e| e.to_string())?;
    Ok(Status {
        running,
        managed,
        docker,
        network_mode,
        keys_dir: host::keys_dir(),
        platform: std::env::consts::OS.into(),
        via_wsl: matches!(host::runtime(), host::Runtime::Wsl { .. }),
    })
}

// Note: non-async Tauri commands run on the main (UI) thread. Anything that
// spawns processes or touches the filesystem is async so the window never stalls.

#[tauri::command]
async fn start_server(app: tauri::AppHandle, state: State<'_, AppState>) -> Result<(), String> {
    let s = state.settings.lock().unwrap().clone();
    state
        .server
        .lock()
        .unwrap()
        .start(&app, &s.ec2sim_dir, settings::env_pairs(&s.extra_env))
}

#[tauri::command]
async fn stop_server(app: tauri::AppHandle, state: State<'_, AppState>) -> Result<(), String> {
    // Stopping waits for ec2-sim to clean up its containers; don't block the UI thread.
    let mut srv = std::mem::take(&mut *state.server.lock().unwrap());
    let logs = srv.logs.clone();
    let srv = tauri::async_runtime::spawn_blocking(move || {
        let r = srv.stop(&app);
        (srv, r)
    })
    .await
    .map_err(|e| e.to_string())?;
    let (mut srv, result) = srv;
    srv.logs = logs;
    *state.server.lock().unwrap() = srv;
    result
}

#[tauri::command]
fn server_logs(state: State<'_, AppState>) -> Vec<String> {
    state.server.lock().unwrap().logs.lock().unwrap().iter().cloned().collect()
}

#[tauri::command]
fn get_settings(state: State<'_, AppState>) -> settings::Settings {
    state.settings.lock().unwrap().clone()
}

#[tauri::command]
async fn save_settings(state: State<'_, AppState>, new: settings::Settings) -> Result<(), String> {
    settings::save(&new)?;
    host::configure(&new.backend, &new.wsl_distro);
    *state.settings.lock().unwrap() = new;
    Ok(())
}

/// Call any EC2 action, e.g. ("DescribeInstances", {"Filter.1.Name": "..."}).
#[tauri::command]
async fn ec2(
    state: State<'_, AppState>,
    action: String,
    params: Option<BTreeMap<String, String>>,
    region: Option<String>,
) -> Result<Value, String> {
    let s = state.settings.lock().unwrap().clone();
    let region = region.unwrap_or(s.region);
    ec2::call(&s.endpoint, &region, &action, &params.unwrap_or_default()).await
}

#[tauri::command]
async fn console_output(instance_id: String) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || host::console_output(&instance_id))
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
fn shell_command(instance_id: String) -> Result<String, String> {
    host::shell_command(&instance_id)
}

#[tauri::command]
async fn ssh_command(ip: String, user: String, key_name: String, bastion: bool) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || host::ssh_command(&ip, &user, &key_name, bastion))
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn open_terminal(command: String) -> Result<(), String> {
    // Waits briefly to detect terminals that crash on start: keep it off the UI thread.
    tauri::async_runtime::spawn_blocking(move || host::open_terminal(&command))
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn save_key(name: String, material: String) -> Result<String, String> {
    host::save_key(&name, &material)
}

#[tauri::command]
async fn key_path(name: String) -> Option<String> {
    tauri::async_runtime::spawn_blocking(move || host::key_path(&name)).await.ok().flatten()
}

#[tauri::command]
async fn delete_key_file(name: String) -> Result<(), String> {
    host::delete_key_file(&name)
}

/// Run a project's own command against ec2-sim (`ec2-sim run`), streaming
/// "run-output" lines and a final "run-exit" code.
#[tauri::command]
async fn run_script(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    dir: String,
    command: String,
    region: String,
    env: Vec<(String, String)>,
) -> Result<(), String> {
    let ec2sim_dir = state.settings.lock().unwrap().ec2sim_dir.clone();
    state.runner.lock().unwrap().start(&app, &ec2sim_dir, &dir, &command, &region, env)
}

#[tauri::command]
async fn stop_script(state: State<'_, AppState>) -> Result<(), String> {
    let ec2sim_dir = state.settings.lock().unwrap().ec2sim_dir.clone();
    let mut r = std::mem::take(&mut *state.runner.lock().unwrap());
    let r = tauri::async_runtime::spawn_blocking(move || {
        r.stop(&ec2sim_dir);
        r
    })
    .await
    .map_err(|e| e.to_string())?;
    *state.runner.lock().unwrap() = r;
    Ok(())
}

#[tauri::command]
fn script_running(state: State<'_, AppState>) -> bool {
    state.runner.lock().unwrap().is_running()
}

// ---------------------------------------------------------------- port forwards

fn ec2sim_dir(state: &State<'_, AppState>) -> String {
    state.settings.lock().unwrap().ec2sim_dir.clone()
}

/// Publish localhost:local_port -> instance:remote_port (`ec2-sim forward`).
#[tauri::command]
async fn forward_add(state: State<'_, AppState>, instance_id: String, remote_port: u16, local_port: u16) -> Result<String, String> {
    if !instance_id.starts_with("i-") || !instance_id[2..].chars().all(|c| c.is_ascii_hexdigit()) {
        return Err("invalid instance id".into());
    }
    let dir = ec2sim_dir(&state);
    tauri::async_runtime::spawn_blocking(move || {
        cli::output(&dir, &["forward", &instance_id, &remote_port.to_string(), "--local", &local_port.to_string()])
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn forward_list(state: State<'_, AppState>) -> Result<Value, String> {
    let dir = ec2sim_dir(&state);
    let out = tauri::async_runtime::spawn_blocking(move || cli::output(&dir, &["forwards", "--json"]))
        .await
        .map_err(|e| e.to_string())??;
    let json = out.lines().rev().find(|l| l.trim_start().starts_with('[')).unwrap_or("[]");
    serde_json::from_str(json).map_err(|e| e.to_string())
}

#[tauri::command]
async fn forward_remove(state: State<'_, AppState>, name: String) -> Result<(), String> {
    if !name.starts_with("ec2sim-fwd-") || !name.chars().all(|c| c.is_ascii_alphanumeric() || c == '-') {
        return Err("invalid forward name".into());
    }
    let dir = ec2sim_dir(&state);
    tauri::async_runtime::spawn_blocking(move || cli::output(&dir, &["unforward", &name]).map(|_| ()))
        .await
        .map_err(|e| e.to_string())?
}

// ---------------------------------------------------------------- HTTP gateway

/// Ports the HTTP gateway listens on (http://<name>.localhost[:port]).
#[tauri::command]
async fn gateway_list(state: State<'_, AppState>) -> Result<Value, String> {
    let dir = ec2sim_dir(&state);
    let out = tauri::async_runtime::spawn_blocking(move || cli::output(&dir, &["gateway", "list", "--json"]))
        .await
        .map_err(|e| e.to_string())??;
    let json = out.lines().rev().find(|l| l.trim_start().starts_with('{')).unwrap_or("{\"ports\":[]}");
    serde_json::from_str(json).map_err(|e| e.to_string())
}

#[tauri::command]
async fn gateway_set_port(state: State<'_, AppState>, port: u16, enabled: bool) -> Result<(), String> {
    let dir = ec2sim_dir(&state);
    let action = if enabled { "add-port" } else { "remove-port" };
    tauri::async_runtime::spawn_blocking(move || cli::output(&dir, &["gateway", action, &port.to_string()]).map(|_| ()))
        .await
        .map_err(|e| e.to_string())?
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let initial = settings::load();
    host::configure(&initial.backend, &initial.wsl_distro);
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(AppState {
            server: Mutex::new(server::Server::default()),
            settings: Mutex::new(initial),
            runner: Mutex::new(runner::Runner::default()),
        })
        .invoke_handler(tauri::generate_handler![
            status,
            start_server,
            stop_server,
            server_logs,
            get_settings,
            save_settings,
            ec2,
            console_output,
            shell_command,
            ssh_command,
            open_terminal,
            save_key,
            key_path,
            delete_key_file,
            run_script,
            stop_script,
            script_running,
            forward_add,
            forward_list,
            forward_remove,
            gateway_list,
            gateway_set_port,
        ])
        .build(tauri::generate_context!())
        .expect("error while building the ec2-sim UI")
        .run(|app, event| {
            // Stop a server the UI started, so its containers are cleaned up.
            if let tauri::RunEvent::Exit = event {
                let state = app.state::<AppState>();
                let dir = state.settings.lock().unwrap().ec2sim_dir.clone();
                {
                    let mut r = state.runner.lock().unwrap();
                    if r.is_running() {
                        r.stop(&dir);
                    }
                }
                let mut srv = state.server.lock().unwrap();
                if srv.is_managed() {
                    let _ = srv.stop(app);
                }
            }
        });
}
