# Troubleshooting

For the server's own view of a problem, check its log: the terminal running
`ec2-sim`, or **Server log** in the app.

## Server

| Problem | Fix |
|---|---|
| `Address already in use` on 4566 | Another ec2-sim (or LocalStack) is running. Stop it, or set `EC2SIM_PORT`. |
| The first launch of a base AMI takes minutes | The base image is building. Run `uv run ec2-sim build-images` once ahead of time. |
| Leftover containers or networks after a crash | `uv run ec2-sim cleanup` removes instance containers and networks and keeps AMIs. |
| The gateway didn't start | Port 80 or 443 is taken on `127.0.0.1`. The log names the port. Free it, or set `EC2SIM_GATEWAY_PORTS=8080,8443`. |

## Instances

| Problem | Fix |
|---|---|
| `systemctl: System has not been booted with systemd` | Use a **systemd** base AMI (Ubuntu 18.04 / 26.04), or an AMI baked from one. |
| User data didn't run | Look at `/var/log/cloud-init-output.log`, or **Console** in the app. User data runs once per instance, not on restart. |
| SSH times out (macOS / Windows Docker Desktop) | Instance IPs aren't routable there. Use the bastion (`ssh -J ec2sim@127.0.0.1:2222 …`) or **Connect → Shell**. |
| A service can't reach another instance | Both must be in the **same VPC**. Use the Name tag or private IP, and make the service listen on `0.0.0.0`. |
| A name from the build's `/etc/hosts` doesn't resolve | Launch an instance at that IP (`--private-ip-address`) in a VPC with the right CIDR. |

## Browser access (gateway)

| Problem | Fix |
|---|---|
| **403** from `name.localhost` | Either no security group allows that port from an IP range, or no running instance has that name. Add an inbound rule (`--cidr 0.0.0.0/0`), tag `ec2sim:web=80`, or use **Connect → Browser → Allow**. Check the name: it's the Name tag (lowercased) or the instance ID. |
| **404** "use http://&lt;instance-name&gt;.localhost" | The address isn't in the form `<name>.localhost` (for example plain `localhost`). |
| **502** | The instance has nothing listening on that port yet, or the app only listens on `127.0.0.1`. If the instance's own nginx returns the 502, its upstream app is down: check the app's service log. |
| `:9404` doesn't connect at all | Add the port to the gateway: `uv run ec2-sim gateway add-port 9404`. |
| Certificate warning on `https://name.localhost` | Expected: 443 passes TLS through, so you see the instance's own certificate. |

## Baking and `ec2-sim run`

| Problem | Fix |
|---|---|
| Packer: `Timeout waiting for AMI` / `ResourceNotReady` | Normal while the AMI is `pending`; it bakes in the background. Large images (several GB) take a few minutes. Check progress in the server log. |
| `/bin/bash^M: bad interpreter` | Windows (CRLF) checkout. Clone the repository inside WSL. |
| The script can't find its source AMI | Map its hard-coded AMI ID with `EC2SIM_AMI_ALIASES=ami-…=ubuntu-26.04`. |
| The script reports success but no AMI exists | Check the server log for `CreateImage … failed`, and check the script's own error handling: some wrappers exit 0 even when Packer fails. |
| A deregistered AMI comes back after a restart | Fixed in current versions: `DeregisterImage` now removes the image even if instances launched from it are still running. Update and restart. |

## Windows and WSL

| Problem | Fix |
|---|---|
| Windows commands stop working inside WSL (`cmd.exe: Exec format error`) | WSL interop was unregistered. Current ec2-sim masks `systemd-binfmt` in its systemd instances, the cause of this. To repair once: `echo ':WSLInterop:M::MZ::/init:PF' | sudo tee /etc/binfmt.d/WSLInterop.conf` and run `wsl --shutdown` from Windows. |
| The desktop app freezes or renders blank under WSLg | Use the native Windows build (see [Desktop app](desktop-app.md)). |
| "Windows cannot find 'ec2-sim'" when opening a terminal | Update the app: older builds quoted the WSL command incorrectly. |
| `name.localhost` doesn't open from Windows | WSL's localhost forwarding is off. Check `localhostForwarding` in `.wslconfig` (it's on by default), or use mirrored networking. |

Still stuck? [Open an issue](https://github.com/danztensai/ec2-sim/issues).
Include your OS, Docker flavour, the command and the server log around the
failure. Remove any secrets first.
