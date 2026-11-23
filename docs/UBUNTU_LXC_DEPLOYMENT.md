# Ubuntu Proxmox LXC deployment checkpoint

Home Hub now targets a systemd-based Ubuntu Proxmox LXC as well as the original
Raspberry Pi OS host. The application remains LAN-only. Waitress binds to
`127.0.0.1:8080` inside the container; Caddy may serve household HTTPS in the
same container. No local model runtime is needed for the planned setup.

## Container requirements

- Use an Ubuntu system container with systemd running. The installer checks for
  this before changing the system. A regular login with `sudo` is required;
  the installer refuses a root login. On a fresh Ubuntu container, create that
  account and install `sudo` from the root console first.
- Provide Python 3.11 or newer, `python3-venv`, `git` or a transferred release
  bundle, outbound package access during installation, and enough persistent
  storage for `/var/lib/homehub` and backups. The installer can offer to add
  `python3-venv` if it is missing.
- Give the container a stable LAN address and a local DNS name before enabling
  Caddy. Allow household clients to reach only its HTTPS port. Keep the
  Waitress port private and avoid public port forwarding.
- Allow the container to reach the agent node's chosen TCP port. Configure the
  agent node to accept the container's IP, and keep that API on a trusted LAN
  or behind HTTPS. Do not expose an unauthenticated model API to the Internet.
- Preserve `/etc/homehub/homehub.env`, `/var/lib/homehub`, and Caddy's CA data
  across container recreation. Home Hub's verified backups complement any
  Proxmox container snapshots; snapshots alone do not replace app backups.

## Remote Chat API

In **Admin → Chat settings**, select either **Ollama on another node** or
**OpenAI-compatible API on another node**. Enter an origin with scheme, host,
and port, such as `http://192.168.1.50:11434` or
`https://agent.home.arpa:8443`. Home Hub discovers models using `/api/tags`
or `/v1/models`, respectively, and sends conversation messages to `/api/chat`
or `/v1/chat/completions`. Ordinary replies stream into the current Chat view;
read-only tool requests use complete messages so tool calls can be validated
and bounded before the next model call.

HTTP is accepted for loopback or private-LAN addresses and local hostnames;
other hostnames require HTTPS. Remote API calls ignore system proxy settings
and refuse redirects so prompts and credentials stay at the configured origin.
For an API that requires a bearer token, set `HOMEHUB_CHAT_API_KEY` in the
private `/etc/homehub/homehub.env` file and restart `homehub.service`. The
token is not stored in the database or shown in Admin settings. Use HTTPS
when a bearer token crosses the network.

Chat remains disabled by default. Its optional data tools remain off by
default and enforce the signed-in user's read permissions and result limits.
Enabling them sends the permitted result to the selected agent node. The
remote node must support OpenAI-style function calls or Ollama tools to use
that feature. No Chat tool can modify household data.

## Live validation before household use

On the target LXC, verify a clean install and a backup-preserving upgrade,
the web, backup, and RSS systemd units, Caddy HTTPS, database ownership,
restore, and uninstall preservation choices. Confirm that the service starts
with its systemd sandbox settings, that only intended ports are reachable,
and that the Admin page discovers models from the agent node. Send a normal
prompt and a read-only tool prompt, inspect streamed text and final history,
then test endpoint failure and recovery. Review answer quality before enabling
Chat for the household. Resource readings from `/proc` may reflect the
Proxmox host rather than the LXC limits; compare them with Proxmox's container
metrics during the checkpoint.
