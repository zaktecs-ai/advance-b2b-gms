# Deploying to Oracle Cloud Always-Free (Ampere A1)

Advance B2B GMS runs comfortably on an OCI **Ampere A1 Always-Free** shape
(4 OCPU / 24 GB RAM), Ubuntu 22.04 or 24.04. This guide walks the full path
from fresh instance to a scheduled scrape.

## 1. Provision the instance

1. Create an Always-Free VM.Standard.A1.Flex with 4 OCPU / 24 GB RAM.
2. Use Ubuntu 22.04 or 24.04 (ARM64 — the installer handles this).
3. Open SSH (22). If you serve the API, open 8000 (or use an SSH tunnel).

## 2. Install

```bash
sudo apt-get update -y && sudo apt-get install -y git python3 python3-venv
git clone https://github.com/<you>/advance-b2b-gms.git
cd advance-b2b-gms
bash setup.sh          # idempotent: apt -> venv -> pip -> playwright chromium
```

## 3. Configure

```bash
cp .env.example .env       # add secrets if any (referenced as ${VAR})
nano config.yaml           # set queries, workers, reviews, etc.
```

### Shape → worker sizing (defaults are safe for A1)

| Shape | `website_workers` | `playwright_workers` | Notes |
|-------|-------------------|----------------------|-------|
| A1 4 OCPU / 24 GB | 4–8 | 2–4 | Default (4/2) is conservative and stable |

## 4. Run (resumable)

Best practice: run inside `tmux` so an SSH drop never kills the job. The
checkpoint makes it resumable even after a hard reboot.

```bash
tmux new -s scrape
source .venv/bin/activate
PYTHONPATH=src python -m scraper.main --config config.yaml
# detach with Ctrl-B d; reattach with: tmux attach -t scrape
```

Output: `output/<client_name>/leads.csv` (+ `leads.xlsx`, `summary.json`).

## 5. Run as a service (systemd)

`/etc/systemd/system/abgms.service`:

```ini
[Unit]
Description=Advance B2B GMS scraper
After=network.target

[Service]
WorkingDirectory=/home/ubuntu/advance-b2b-gms
ExecStart=/home/ubuntu/advance-b2b-gms/.venv/bin/python -m scraper.main --config config.yaml
Environment=PYTHONPATH=/home/ubuntu/advance-b2b-gms/src
Restart=on-failure
User=ubuntu

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now abgms
journalctl -u abgms -f
```

## 6. Serve the REST API / Web UI

```bash
PYTHONPATH=src python -m scraper.main --config config.yaml --serve
# remote tunnel: ssh -L 8000:localhost:8000 ubuntu@<instance-ip>
# then open http://localhost:8000
```

## Cost

Always-Free A1 costs **$0/month**. The core needs no paid APIs or proxies.
Optional proxy support is off by default — only enable it (and only use free or
self-hosted proxies) if a target site blocks your datacenter IP.

## Safety tips

- Keep `output/` on a persistent volume if you recreate the instance.
- Respect ToS and rate limits; the built-in pacing clock and failure taxonomy
  are there to keep you polite and accurate.
- Never bypass CAPTCHAs programmatically — the tool classifies them and lets
  you solve manually.
