# ec2-sim desktop app

A desktop UI for [ec2-sim](../README.md), built with [Tauri 2](https://tauri.app)
(Rust) and React. Start the simulator, launch instances, connect to them, and
bake AMIs without touching the command line.

![Overview](docs/overview.png)

| Launch wizard | Instances | AMIs |
|---|---|---|
| ![Launch](docs/launch.png) | ![Instances](docs/instances.png) | ![AMIs](docs/amis.png) |

## What you can do

- **Start/stop the simulator** from the top bar. The app runs ec2-sim from your
  checkout, streams its log (**Server log**), and stops it cleanly when you quit,
  so instance containers are removed. A server you started yourself is shown as
  *external* and left alone.
- **Launch instances** with a guided wizard: pick an AMI (base or your own),
  name, type, key pair (or create one on the spot), and optional user data from
  ready-made templates.
- **Connect** to an instance:
  - *Shell*: a root shell via `docker exec`, no key needed.
  - *SSH*: the exact `ssh` command, with your saved key and, on Docker Desktop,
    the bastion jump host.
  
  Both can open your terminal directly or copy the command.
- **Console**: user-data output and the instance's boot log.
- **Start / Stop / Reboot / Terminate** from each row, with confirmation for destructive actions.
- **Bake an AMI** from any instance (optionally stopping it first, like Packer),
  then launch copies from **AMIs**. Deregister AMIs you no longer need.
- **Key pairs**: create and delete them. Private keys are saved to `~/.ec2-sim/keys/<name>.pem` (mode 600).
- **Settings**: ec2-sim folder, endpoint, region, auto-start, and extra
  environment for the server (e.g. `EC2SIM_AMI_ALIASES`).

## Run it

Requirements: everything ec2-sim needs (Docker, uv), plus Rust (stable) and
Node.js 20.19+. On Linux, also the WebKitGTK build dependencies
(`libwebkit2gtk-4.1-dev`, `build-essential`, `libssl-dev`, `libayatana-appindicator3-dev`,
`librsvg2-dev`); see Tauri's [prerequisites](https://tauri.app/start/prerequisites/).

```bash
cd ui
npm install
npm run tauri dev        # development, with hot reload
npm run tauri build      # release build + installers in src-tauri/target/release/bundle/
```

The app looks for ec2-sim in the repository it was built from (`ui/..`). Change
it under **Settings → ec2-sim folder** if you move things.

### Windows with ec2-sim in WSL (recommended on Windows)

Run the app natively on Windows (WebView2, smooth) while ec2-sim and Docker stay in WSL:

- The app reaches the API on `localhost:4566` through WSL's localhost forwarding.
- It starts and stops ec2-sim with `wsl.exe` (clean SIGTERM shutdown).
- Console output and the Docker helpers run inside WSL.
- **Shell** and **SSH** commands are `wsl.exe -- …`, because instance IPs are only
  reachable inside WSL.
- Key files go to WSL's `~/.ec2-sim/keys`.
- Detection is automatic: Docker Desktop on Windows means native mode, otherwise WSL.
  Override it, pick a distro, and set the folder (a WSL path such as
  `~/tools/ec2-sim`) under **Settings**.

Build it on Windows with Rust (MSVC toolchain + Visual Studio Build Tools, C++
workload) and Node.js 20.19+, from a copy of `ui/` on the Windows disk (building
on `\\wsl.localhost\...` is slow):

```powershell
$env:EC2SIM_DEFAULT_DIR = "/home/<you>/tools/ec2-sim"   # optional: default WSL folder
npm ci
npm run tauri build      # -> src-tauri\target\release\ec2-sim-ui.exe + installers in bundle\
```

Notes:

- **WSL2 (Linux build through WSLg):** prefer the Windows build above. If you run the Linux build, the window opens through WSLg. Run it with
  `WEBKIT_DISABLE_DMABUF_RENDERER=1` (otherwise it may render blank). Leave it in
  the default Wayland mode: forcing X11 (`GDK_BACKEND=x11`) makes input laggy and
  the first click only focuses the window. If it still feels slow, also set
  `WEBKIT_DISABLE_COMPOSITING_MODE=1`.
- **macOS/Windows (Docker Desktop):** ec2-sim switches to bastion mode
  automatically; the Connect dialog builds the SSH command accordingly.
- **Open in terminal** uses Terminal.app on macOS, `cmd` on Windows, and the first
  working one of `x-terminal-emulator`, `gnome-terminal`, `konsole`,
  `xfce4-terminal`, `kitty`, `alacritty`, `xterm` on Linux. If none works, copy
  the command instead.

## How it works

- `src-tauri/src/`:
  - `server.rs` starts and stops the ec2-sim process and streams its log to the UI.
  - `ec2.rs` sends EC2 Query API calls to ec2-sim and converts the XML responses to JSON.
  - `host.rs` handles Docker (console output, network mode), terminals and key files.
  - `settings.rs` stores preferences in the OS config dir (`ec2-sim-ui/settings.json`).
- `src/`: the React app.
  - `api.ts`: typed command wrappers and response parsing.
  - `store.tsx`: shared state, polled every 1.5 to 4 seconds.
  - `pages/`: the screens.
  - `ui.tsx`: buttons, modals, toasts.

The app talks to ec2-sim only through its public EC2 API and Docker, so it works
with any ec2-sim server on the configured endpoint.
