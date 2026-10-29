# LAN HTTPS with Caddy

Home Hub should use HTTPS before household members sign in. This guide puts
Caddy in front of Waitress, keeps the Python app on loopback, and uses Caddy's
local certificate authority. It does not require a public domain or expose
Home Hub to the Internet.

## 1. Give the Pi a stable home-network name

Reserve a stable address for the Pi in the router, then add a local DNS entry
such as `homehub.home.arpa` pointing to that address. `home.arpa` is the
special-use DNS domain reserved for residential home networks by
[RFC 8375](https://www.rfc-editor.org/rfc/rfc8375.html). Configure household
devices to use that local name. The name must resolve to the Pi on the LAN.
Replace it in [Caddyfile.example](../deploy/Caddyfile.example) if you choose a
different name.

## 2. Install and configure Caddy

For a fresh Home Hub installation, Caddy can be installed as part of setup by
passing `--install-caddy` to `deploy/install.sh` (or the release installer).
This uses Caddy's official Debian-family package repository. The package
starts and keeps its system service running. It does not add the Home Hub site
to the Caddyfile or configure local DNS. A fresh package install may create its
default Caddyfile; `enable_https.sh` asks before replacing it. Existing Caddy
installations and `caddy.service` units are left untouched.

Install the current stable Caddy package for Raspberry Pi OS using the
[official Caddy installation instructions](https://caddyserver.com/docs/install).
The official Debian-family package includes a systemd service. From the
transferred Home Hub repository on the Pi, check whether
`/etc/caddy/Caddyfile` already exists. The setup command refuses to replace
any existing file by default. If it contains only the package's default site,
or you have decided to replace the existing sites, run:

```sh
./deploy/enable_https.sh --replace-existing-caddyfile homehub.home.arpa
```

The script refuses to replace an existing Caddyfile unless you pass
`--replace-existing-caddyfile`. The opt-in replaces the entire Caddyfile and
saves a timestamped backup, so use it only when replacing existing Caddy
sites is intended. For a custom Caddy setup you want to retain, merge the
generated site into that configuration yourself instead of running the
replacement command. The script generates and validates a configuration using
Caddy's internal CA, restricts Waitress to loopback, enables secure session
cookies, then restarts Home Hub and Caddy. It preserves other Home Hub
environment settings and secrets. Pass a different local hostname as the
argument if needed.

The generated configuration follows this pattern:

```caddyfile
homehub.home.arpa {
	tls internal
	reverse_proxy 127.0.0.1:8080
}
```

Validate and start Caddy:

```sh
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl enable --now caddy
sudo systemctl status caddy
```

Caddy listens for HTTPS and redirects HTTP to HTTPS. Its `internal` issuer
creates certificates signed by Caddy's private local CA. Keep Caddy's data
directory persistent; deleting it can require trusting a replacement root CA
on every device.

## 4. Trust the Caddy root certificate on household devices

Other devices will reject the certificate until they trust Caddy's root CA.
For the packaged Linux service, Caddy documents the root certificate at:

```text
/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt
```

Transfer **only** `root.crt` to household-managed devices over a trusted
administrative path. Verify its SHA-256 fingerprint with the Pi administrator
before installing it. Import it as a trusted root certificate in the device's
operating-system or browser certificate store. Some browsers keep their own
trust store, and mobile platforms may require an additional explicit
certificate-trust step. Follow the device vendor's current instructions.

The root certificate is public; the CA's private key is not. Never copy or
share the CA private key. Only install this root certificate on devices you
trust: it allows the Pi's CA to issue certificates those devices will trust.

## 5. Limit network access and verify the setup

Allow TCP port 443 only from the household subnet in the Pi's firewall. Port
80 may also be allowed from that subnet if you want Caddy's HTTP-to-HTTPS
redirect. Do not add router port-forwarding rules, and do not expose the
Waitress port 8080 or Caddy's administrative interface to the LAN.

From a trusted household device, open `https://homehub.home.arpa` and confirm
that the browser reports a trusted certificate. Confirm that `http://` for the
same name redirects to HTTPS. Sign in, then inspect the session cookie in the
browser's developer tools: it should have `Secure`, `HttpOnly`, and
`SameSite=Lax` attributes. The app's secure-cookie setting is controlled by
`HOMEHUB_COOKIE_SECURE`; leave it off only for localhost HTTP development.

Useful service checks on the Pi:

```sh
systemctl status homehub caddy
sudo journalctl -u homehub -u caddy --since today
```

If the hostname does not resolve, fix the router's local DNS entry. If the
certificate is untrusted, verify the root certificate fingerprint and install
it in the trust store used by that device's browser. Never work around a
certificate warning by disabling certificate checks.

## References

- [Caddy installation](https://caddyserver.com/docs/install)
- [Caddy local HTTPS and service operation](https://caddyserver.com/docs/running)
- [Caddy TLS internal issuer](https://caddyserver.com/docs/caddyfile/directives/tls)
- [Caddy reverse proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)
- [Flask cookie security settings](https://flask.palletsprojects.com/en/stable/web-security/)
- [RFC 8375: Special-Use Domain `home.arpa`](https://www.rfc-editor.org/rfc/rfc8375.html)
