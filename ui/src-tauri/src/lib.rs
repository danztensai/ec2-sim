//! ec2-sim desktop UI: Tauri commands bridging the React frontend to the
//! ec2-sim server process, its EC2 API, and the local Docker engine.

mod ec2;
mod host;
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
        keys_dir: host::keys_dir().to_string_lossy().into_owned(),
    })
}

#[tauri::command]
fn start_server(app: tauri::AppHandle, state: State<'_, AppState>) -> Result<(), String> {
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
fn save_settings(state: State<'_, AppState>, new: settings::Settings) -> Result<(), String> {
    settings::save(&new)?;
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
fn open_terminal(command: String) -> Result<(), String> {
    host::open_terminal(&command)
}

#[tauri::command]
fn save_key(name: String, material: String) -> Result<String, String> {
    host::save_key(&name, &material)
}

#[tauri::command]
fn key_path(name: String) -> Option<String> {
    host::key_path(&name)
}

#[tauri::command]
fn delete_key_file(name: String) -> Result<(), String> {
    host::delete_key_file(&name)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(AppState {
            server: Mutex::new(server::Server::default()),
            settings: Mutex::new(settings::load()),
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
            open_terminal,
            save_key,
            key_path,
            delete_key_file,
        ])
        .build(tauri::generate_context!())
        .expect("error while building the ec2-sim UI")
        .run(|app, event| {
            // Stop a server the UI started, so its containers are cleaned up.
            if let tauri::RunEvent::Exit = event {
                let state = app.state::<AppState>();
                let mut srv = state.server.lock().unwrap();
                if srv.is_managed() {
                    let _ = srv.stop(app);
                }
            }
        });
}
