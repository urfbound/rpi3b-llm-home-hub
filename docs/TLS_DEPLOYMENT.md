# LAN HTTPS with Caddy

Home Hub should use HTTPS before household members sign in. This guide puts
Caddy in front of Waitress, keeps the Python app on loopback, and uses Caddy's
local certificate authority. It does not require a public domain or expose
Home Hub to the Internet.

## 1. Give the Pi a stable home-network name

Reserve a stable address for the Pi in the router, then add a local DNS entry
such as `homehub.home.arpa` pointing to that address. Configure household
devices to use that local name. The name must resolve to the Pi on the LAN.
Replace it in [Caddyfile.example](../deploy/Caddyfile.example) if you choose a
different name.

## 2. Bind Home Hub to loopback and enable secure cookies

Set these values in `/etc/homehub/homehub.env`:

```ini
HOMEHUB_BIND=127.0.0.1
HOMEHUB_PORT=8080
HOMEHUB_COOKIE_SECURE=true
```

Keep `HOMEHUB_SECRET_KEY` persistent and private. Restrict the environment
file, for example with owner `root`, group `homehub`, and mode `0640`. Restart
Home Hub after changing it:

```sh
sudo systemctl restart homehub
sudo systemctl status homehub
```

Waitress now accepts requests only from the Pi itself. Caddy will be the only
LAN-facing web service.

## 3. Install and configure Caddy

Install the current stable Caddy package for Raspberry Pi OS using the
[official Caddy installation instructions](https://caddyserver.com/docs/install).
The official Debian-family package includes a systemd service. Copy
[Caddyfile.example](../deploy/Caddyfile.example) to `/etc/caddy/Caddyfile` and
replace `homehub.home.arpa` with the local name configured in step 1.

The example configuration is:

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
