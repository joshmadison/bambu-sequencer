# Public deployment

This guide takes PlateLoop from a local container to a public HTTPS site on a
VPS — and leaves room for more apps on other subdomains later. The stack is
deliberately small: Docker Compose, one Caddy edge proxy, one app container.

> PlateLoop has no login. Anyone who can reach it can upload files and trigger
> gcode generation. The rate limits and container limits below are not optional
> for a public instance.

## 1. What you need

- A VPS with **2 vCPU and 4 GB RAM** (more is fine), Ubuntu 24.04 LTS.
  - The app keeps the upload, the parsed 3MF, the gcode and the result in
    memory. 2 GB is tight for large files.
  - Hostinger KVM 2 or a comparable Hetzner/Netcup plan works. Use an EU
    location if your users are in the EU.
- A domain (e.g. `example.com`) where you can edit DNS records.
- About 10 minutes for the first deploy.

## 2. Prepare the server

Log in as root once, create a non-root user and copy your SSH key:

```bash
adduser deploy
usermod -aG sudo deploy
rsync --archive --chown=deploy:deploy ~/.ssh /home/deploy
```

From now on use `deploy@your-server`.

### SSH hardening

Create `/etc/ssh/sshd_config.d/99-hardening.conf`:

```text
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
```

Then:

```bash
sudo systemctl restart ssh
```

Keep your current session open until key-only login works in a second terminal.

### Firewall and basic updates

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y ufw fail2ban unattended-upgrades

sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 443/udp
sudo ufw enable

sudo dpkg-reconfigure -plow unattended-upgrades
```

Some VPS providers also have a firewall in their control panel. Open the same
ports there (22, 80, 443) and nothing else.

### Docker

Use the official convenience script (read it first if you prefer):

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker deploy
```

Log out and back in so the group applies.

## 3. DNS

Point the hostname at the server. At your DNS provider:

| Type | Name | Value |
| --- | --- | --- |
| A | `plateloop` | your server IPv4 |
| AAAA | `plateloop` | your server IPv6 (optional) |
| A | `*` | your server IPv4 (optional, for future apps) |

Caddy obtains a separate certificate for every hostname that has a site block.
The wildcard `*` record is only a convenience so you do not have to add DNS
records for every future app.

Wait until `dig +short plateloop.example.com` returns your server IP before
starting Caddy, otherwise certificate issuance fails.

## 4. Deploy

```bash
sudo mkdir -p /srv && sudo chown deploy:deploy /srv
git clone https://github.com/joshmadison/plateloop.git /srv/plateloop
cd /srv/plateloop
cp .env.example .env
nano .env
```

Fill in at least:

```dotenv
DOMAIN=plateloop.example.com
ACME_EMAIL=you@example.com
LEGAL_NAME=...
LEGAL_ADDRESS=...
LEGAL_EMAIL=...
HOSTING_PROVIDER=...
LOG_RETENTION_DAYS=30
SUPPORT_URL=https://ko-fi.com/...
SUPPORT_LABEL=Support on Ko-fi
```

`.env` is git-ignored and stays on the server. `git pull` will not touch it. Back
it up somewhere safe, because the legal data is intentionally not in the public
repository. The app shows a visible warning on `/impressum` (and its English
translation `/imprint`) while `LEGAL_NAME`, `LEGAL_ADDRESS` or `LEGAL_EMAIL` is
empty.

Then start the stack:

```bash
docker compose -f compose.prod.yaml up -d --build
docker compose -f compose.prod.yaml ps
docker compose -f compose.prod.yaml logs -f caddy
```

Caddy should log a successful certificate issuance. Check:

```bash
curl -I https://plateloop.example.com
```

Then open the site, upload a small sliced 3MF, and run one build.

## 5. Configuration reference

All values live in `.env` (see `.env.example`).

| Variable | Default | Purpose |
| --- | --- | --- |
| `DOMAIN` | required | Public hostname; Caddy serves this site block. |
| `ACME_EMAIL` | required | Let's Encrypt account address. |
| `PLATELOOP_IMAGE` | empty | Empty builds locally; set to a GHCR image to pull instead. |
| `MAX_UPLOAD_MB` | `100` in prod | Upload cap. Keep **≤ 95** behind Cloudflare Free. |
| `MAX_BODY_MB` | `110` | Caddy rejects larger bodies before they reach the app. Keep it slightly above `MAX_UPLOAD_MB`. |
| `RATE_LIMIT_REQUESTS` | `60` | POST `/api/*` requests per IP per window. `0` disables. |
| `RATE_LIMIT_WINDOW_S` | `600` | Window in seconds. |
| `MAX_CONCURRENT_REQUESTS` | `2` | Simultaneous upload/build requests across the instance. |
| `FORWARDED_ALLOW_IPS` | `*` in prod | Trust Caddy's `X-Forwarded-For`. Only safe because the app port is not published. |
| `SUPPORT_URL` | empty | Footer donation link (BMC, Ko-fi, …). Empty hides it. |
| `SUPPORT_LABEL` | `Buy me a coffee` | Link text. |
| `LEGAL_NAME`, `LEGAL_ADDRESS`, `LEGAL_EMAIL` | empty | Impressum/Datenschutz. Pages show a warning while incomplete. |
| `LEGAL_VAT_ID` | empty | Optional VAT ID. |
| `HOSTING_PROVIDER` | generic | Named in the privacy page. |
| `LOG_RETENTION_DAYS` | `30` | Stated log retention. Keep it in sync with reality. |
| `ADS_PROVIDER` | empty | Only set after adding a consent banner and updating the privacy page. |

The container runs as a non-root user, with a read-only root filesystem,
`cap_drop: ALL`, `no-new-privileges`, a 2 GB memory cap, a 2 CPU cap and a PID
limit. Uploads are processed in memory; there is no volume for user data.

### Donations

A plain link to Ko-fi, Buy Me a Coffee or GitHub Sponsors needs **no cookie
banner** — no script from the provider is loaded until the visitor clicks it.
Set `SUPPORT_URL` and `SUPPORT_LABEL`; an empty URL hides the footer link.

Rough fee comparison (2026, check current pricing):

- **Ko-fi Free:** 0 % platform fee on one-time tips, 5 % on memberships/shop;
  payment processor fees (PayPal/Stripe) apply.
- **Buy Me a Coffee:** 5 % platform fee plus processor fees, possibly +1 % for
  international payments.
- **GitHub Sponsors:** 0 % platform fee for personal accounts; processor fees
  may apply. Best when your audience already lives on GitHub.

Donations are income. Occasional small tips are usually unproblematic for a
private hobby project, but regular or larger amounts can be taxable. Keep
records and ask a tax advisor if it grows.

## 6. Adding more apps / subdomains

The edge network is named `proxy`. Create it once if Compose has not already:

```bash
docker network create proxy
```

A second app is just another Compose project that joins the external network
and does **not** publish ports:

```yaml
services:
  app2:
    image: ghcr.io/you/app2:latest
    restart: unless-stopped
    expose:
      - "8000"
    networks:
      - proxy

networks:
  proxy:
    external: true
    name: proxy
```

Add a block to `deploy/Caddyfile`:

```caddyfile
app2.example.com {
	encode zstd gzip
	reverse_proxy app2:8000
}
```

Reload Caddy without downtime:

```bash
docker compose -f compose.prod.yaml exec caddy caddy reload --config /etc/caddy/Caddyfile
```

Each app should get its own database/volumes and its own resource limits. Do
not publish app ports to the host; only Caddy exposes 80/443.

## 7. Updates

```bash
cd /srv/plateloop
git pull --ff-only
docker compose -f compose.prod.yaml up -d --build
docker image prune -f
```

A restart takes a few seconds. The app has no persistent user data, so there is
nothing to migrate. Keep `.env` out of git.

For a prebuilt image, set `PLATELOOP_IMAGE=ghcr.io/<user>/plateloop:latest` and
run `docker compose -f compose.prod.yaml pull && docker compose -f compose.prod.yaml up -d`.
Automatic updates (Watchtower) are possible but can surprise you; manual
updates are usually the better trade-off for a small instance.

## 8. Backups and monitoring

- **Config:** the repo and `.env` are the deployment. Back up `.env` somewhere
  safe (password manager).
- **Caddy:** certificates live in the `caddy_data` volume and are reissued
  automatically; a VPS snapshot is enough.
- **User data:** none is persisted, so no upload backups are needed.
- **Snapshots:** enable weekly provider snapshots (Hetzner/Hostinger).
- **Uptime:** point Uptime Kuma, healthchecks.io or similar at
  `https://plateloop.example.com/` or `/api/printers`.
- **Disk:** `docker system df`; old images are the usual culprit.

## 9. Cloudflare (optional)

Cloudflare gives DDoS protection and a WAF, but adds two caveats:

1. The Free plan caps request bodies at **100 MB** → set `MAX_UPLOAD_MB=95`.
2. Rate limiting by client IP only works if Caddy restores the real client IP.
   Otherwise every visitor looks like a Cloudflare edge IP and shares one
   bucket.

If you use Cloudflare, set SSL/TLS mode to **Full (strict)** and configure
Caddy with Cloudflare's current IP ranges:

```caddyfile
{
	servers {
		trusted_proxies static 173.245.48.0/20 # replace with the full list
		client_ip_headers CF-Connecting-IP
	}
}
```

Use the current list from <https://www.cloudflare.com/ips/>. Also restrict the
origin firewall to Cloudflare IPs (or use a Tunnel) so the origin cannot be hit
directly. If that sounds like too much for the start: skip Cloudflare and rely
on the rate limits plus the provider firewall.

## 10. Security checklist

- [ ] `.env` is not committed and contains no secrets in git.
- [ ] SSH: key-only, root login disabled, fail2ban active.
- [ ] Firewall allows only 22/80/443.
- [ ] `DISABLE_DOCS=1` (already set in `compose.prod.yaml`).
- [ ] `MAX_UPLOAD_MB` is set (100 or lower).
- [ ] Rate limits and `MAX_CONCURRENT_REQUESTS` are set.
- [ ] Container limits (memory/CPU/PIDs/read-only) are active.
- [ ] `/docs` and `/openapi.json` return 404 in production.
- [ ] Security headers are present (`curl -I https://…`).
- [ ] Snapshots and uptime monitoring are enabled.
- [ ] Legal pages are filled and reviewed.

## 11. Legal checklist (Germany/EU)

The legal pages are generated from `.env`: `/impressum` and `/datenschutz` are
the German, legally binding versions; `/imprint` and `/privacy` are English
convenience translations for the English UI. They are a starting point, **not
legal advice** — have them reviewed if the site becomes more than a hobby
project.

- [ ] **Impressum (§ 5 DDG):** real name, reachable address, email. Also as a
      private individual.
- [ ] **Datenschutz/Privacy:** hosting provider, server logs/IPs, retention,
      uploads processed in RAM, external donation link, rights and complaint body.
- [ ] **Log retention:** the privacy page promises `LOG_RETENTION_DAYS`. Docker's
      `json-file` driver rotates by **size** (10 MB × 3 here), not by time. Add
      time-based rotation (logrotate/journald) or adjust the text so it matches
      reality.
- [ ] **Hosting contract:** sign the provider's data processing agreement
      (AVV/DPA, Art. 28 GDPR).
- [ ] **Donations:** a plain link to Buy Me a Coffee/Ko-fi needs no cookie
      banner. Check the provider's own privacy policy.
- [ ] **Ads:** only with a consent banner/CMP, Consent Mode v2, updated privacy
      page and `ADS_PROVIDER` set. Ads are the most legally sensitive part —
      do this after the site works, not before.
- [ ] **Trademarks:** no Bambu Lab logo; "compatible with" wording only. The
      Impressum already contains a disclaimer.
- [ ] **Taxes:** ad/donation income can require a business registration and
      VAT handling. Check with a tax advisor.
- [ ] **Product safety:** the generated gcode changes printer motion. Keep the
      warning visible and do not promise unattended operation.

## 12. Troubleshooting

| Symptom | Check |
| --- | --- |
| `502 Bad Gateway` | `docker compose -f compose.prod.yaml logs plateloop`; is the container healthy? |
| No certificate | DNS points to the server, ports 80/443 open, `docker compose logs caddy`; wait for DNS propagation. |
| `413 Request Entity Too Large` | `MAX_UPLOAD_MB`; if behind Cloudflare also its 100 MB body limit. |
| `429 Too Many Requests` | Expected after `RATE_LIMIT_REQUESTS` POSTs per window; raise the value or wait. |
| Everyone gets `429` at once | Caddy is not passing the real client IP; check `FORWARDED_ALLOW_IPS` and, behind Cloudflare, `trusted_proxies`. |
| Container restarts / OOM | `docker stats`; lower `MAX_UPLOAD_MB` or `MAX_CONCURRENT_REQUESTS`, or use more RAM. |
| `Read-only file system` | The app must not write to disk; only `/tmp` is writable (tmpfs). |
| Healthcheck fails | `docker inspect --format '{{json .State.Health}}' <container>`; the check calls `/` on port 8000. |
| Logs are older than `LOG_RETENTION_DAYS` | `json-file` rotates by size, not time. Add logrotate/journald or shorten the retention claim. |

## 13. Why one worker?

The rate limiter and the build cap are in-process counters. Running multiple
uvicorn workers would multiply the effective limits and split the counters.
For this workload one worker with a thread pool is enough; scale vertically
(more CPU/RAM) before adding workers. If you ever need multiple workers, move
the limits to Redis or the proxy.
