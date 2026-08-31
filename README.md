# Advance B2B GMS

## Google Maps B2B Lead Scraper

Advance B2B GMS collects business listings from Google Maps, cleans the data,
enriches business websites, and writes CSV/XLSX lead files. It is a command-line
program for a Linux server or VPS.

The export contains **75 producer-backed columns**. Missing values are written as
`N/A`; unsupported Google Maps fields are not added as empty columns.

## The simple way: use `server.sh`

You do not need to remember Git, tmux, or Python commands for normal use.
Run these commands from the repository folder:

```bash
bash server.sh setup   # first time only; also fixes local permissions
./server.sh config      # edit searches and settings
./server.sh demo        # safe offline test
./server.sh run         # start the real scraper
```

After new code is pushed to GitHub:

```bash
./server.sh update      # update code and dependencies
./server.sh run         # start or resume the scraper
```

### All controller commands

| Command | What it does |
| --- | --- |
| `bash server.sh setup` | Creates the Python environment, installs dependencies and Chromium, and fixes local launcher permissions. |
| `./server.sh update` | Downloads the latest `main` branch and refreshes dependencies. It refuses to update while a scraper is running or when local code was changed. |
| `./server.sh config` | Opens your private `config.local.yaml` in `nano`. |
| `./server.sh demo` | Runs sample data only. It does not contact Google Maps. |
| `./server.sh run` | Starts the live scraper in `tmux` so it continues after an SSH disconnect. |
| `./server.sh status` | Shows whether the scraper is running. |
| `./server.sh logs` | Follows the live console log. Press `Ctrl+C` or `q` to leave the viewer. |
| `./server.sh stop` | Requests a clean stop. The checkpoint remains safe. |
| `./server.sh help` | Prints the same command list on the server. |

The controller uses `config.local.yaml` for your settings and `.env` for optional
secrets. Both files are ignored by Git and are not overwritten by an update.

## 1. First-time server installation

These instructions use Ubuntu 22.04/24.04 and `/opt/advance-b2b-gms`. If you
choose another folder, use that folder consistently.

### Connect to the server

Run this on your own computer:

```bash
ssh YOUR_USER@YOUR_SERVER_IP
```

### Install the project

Run these commands after you are connected:

```bash
sudo mkdir -p /opt
sudo chown "$USER":"$USER" /opt
cd /opt
git clone https://github.com/zaktecs-ai/advance-b2b-gms.git advance-b2b-gms
cd /opt/advance-b2b-gms
bash server.sh setup
```

`bash server.sh setup` runs the project setup, installs Python packages, installs
`tmux`, installs Playwright Chromium, and creates these private files when they
do not exist:

```text
.env                 optional API keys and proxy settings
config.local.yaml    your local search configuration
```

The tracked `config.yaml` is a safe template. Your everyday settings belong in
`config.local.yaml`, which is created from that template.

## 2. Configure the search

Open the private configuration file:

```bash
cd /opt/advance-b2b-gms
./server.sh config
```

For a first test, use a small query and a small result limit:

```yaml
job:
  client_name: houston-plumbers
  output_dir: output
  default_country: US
  max_results_per_query: 20
  max_total_results: 0

queries:
  - "plumbers in Houston, TX"

maps:
  headless: true

enrichment:
  decision_makers: true
  mx_verify: false
  smtp_verify: false
```

The settings you will use most often are:

| Setting | Meaning |
| --- | --- |
| `job.client_name` | Name of the output folder. Use letters, numbers, `_`, or `-`. |
| `job.default_country` | Country for national phone numbers, for example `PK`, `US`, or `GB`. |
| `job.max_results_per_query` | Maximum listings per search. `0` means unlimited. |
| `job.max_total_results` | Maximum listings for the entire run. `0` means unlimited. |
| `queries` | Google Maps searches to run. Add one query per line. |
| `maps.headless` | Keep `true` on a normal server. Use `false` only with VNC configured. |
| `reviews.enabled` | Collect reviews for sentiment and lead scoring. |
| `enrichment.decision_makers` | Extract names and titles from about/team pages. |
| `enrichment.mx_verify` | Check whether an email domain has MX records. |
| `enrichment.smtp_verify` | Probe mail servers; slower and often inconclusive. |

### Optional AI pitch hooks

Do not put secrets in `config.local.yaml`. Add an API key to `.env` only when
using AI-generated pitch hooks:

```bash
nano .env
```

```dotenv
OPENAI_API_KEY=
DEEPSEEK_API_KEY=
```

Then enable one provider in `config.local.yaml`:

```yaml
ai_hook:
  enabled: true
  provider: openai
```

If AI is disabled, the key is missing, or the request fails, the rule-based
pitch hook is used automatically.

## 3. Run and control the scraper

### Safe offline test

```bash
./server.sh demo
```

This uses sample businesses only. It does not open Google Maps.

### Start the live scraper

```bash
./server.sh run
```

The scraper runs in a `tmux` session. You can close SSH and the job continues.
The same command can be used again after an interruption; the checkpoint skips
completed queries and continues unfinished work.

### Check progress

```bash
./server.sh status
./server.sh logs
```

If `tmux` is available, the status output also shows the command to view the
live terminal directly:

```bash
tmux attach -t abgms
```

Detach from tmux without stopping the scraper with `Ctrl+B`, then `D`.

### Stop the scraper

```bash
./server.sh stop
```

The stop command requests a graceful shutdown. Do not delete the output folder
or checkpoint when stopping a job.

## 4. Update the server after a GitHub change

The normal update is only two commands:

```bash
cd /opt/advance-b2b-gms
./server.sh update
./server.sh run
```

`server.sh update` does the following safely:

- refuses to update if a scraper is still running;
- downloads the latest `main` branch;
- refreshes the Python dependencies; and
- keeps `config.local.yaml`, `.env`, and `output/` in place.

Run the offline demo between update and the live run when you want an extra
check:

```bash
./server.sh update
./server.sh demo
./server.sh run
```

### If the update says local code changes exist

Do not use `git reset --hard` unless you intentionally want to delete local code.
Inspect the situation first:

```bash
git status --short
git diff
```

Your normal search settings should be in `config.local.yaml`, not in tracked
`config.yaml`, so configuration changes normally do not block updates.

## 5. Optional VNC mode for CAPTCHA solving

Headless mode is recommended. Use VNC only when you need to see the browser or
solve a CAPTCHA manually.

Install TightVNC once (the launcher will remind you if it is missing):

```bash
sudo apt-get update
sudo apt-get install -y tightvncserver
```

Start the dedicated screen with the bundled launcher:

```bash
./vnc-screen.sh
```

This starts a **separate** display (`:2`) on a **non-common** viewer port
(`43873` by default). It prints the exact address to connect your TightVNC
viewer to. The engine does not open a port itself — the launcher owns it.

Open the config and turn off headless:

```yaml
maps:
  headless: false

vnc:
  display: ":2"
```

Then start the scraper normally:

```bash
./server.sh config
./server.sh run
```

Connect your TightVNC viewer to the address printed by `./vnc-screen.sh`
(usually `YOUR_SERVER_IP:43873`).

> **Do not** expose a common VNC port such as `5901`/`5902` to the public
> internet. The launcher uses a non-common port on purpose so the screen is not
> an easy scan target. Prefer an SSH tunnel if you must reach it remotely:
>
> ```bash
> ssh -L 43873:127.0.0.1:43873 YOUR_USER@YOUR_SERVER_IP
> ```

Check / stop the screen:

```bash
./vnc-screen.sh status
./vnc-screen.sh stop
```

## 6. Output files

For `client_name: houston-plumbers`, the output is:

```text
output/houston-plumbers/
├── leads.csv
├── leads.xlsx
├── summary.json
├── run.log
├── checkpoint.sqlite
└── checkpoint.json
```

CSV and XLSX use the same 75-column order from `scraper/models.py`. Unsupported
fields such as timezone, popular times, competitors, ownership posts, gas
prices, featured questions, and rating buckets are intentionally excluded.

## 7. Common problems

### `Permission denied: ./server.sh`

```bash
chmod +x server.sh setup.sh run.sh vnc-screen.sh
```

### `No module named ...`

```bash
./server.sh update
```

If this is a new installation, use:

```bash
./server.sh setup
```

### `Chromium binary is missing`

```bash
./server.sh setup
```

### The job is already running

```bash
./server.sh status
./server.sh logs
```

Stop it only when you really want to stop the run:

```bash
./server.sh stop
```

### The server has an old 85-column CSV

The current writer refuses to mix the old and new schemas. Keep the old output
as a backup, change `job.client_name` in `config.local.yaml`, and run again:

```bash
./server.sh config
./server.sh run
```

If you intentionally archive the old client folder, do that only after confirming
you no longer need its CSV, XLSX, or checkpoint.

### Google shows a CAPTCHA or zero listings

Check the log:

```bash
./server.sh logs
```

A challenge or temporary block is retryable. If needed, configure VNC mode,
solve the challenge in the visible browser, and run again.

### SSH disconnected

The normal `./server.sh run` command uses `tmux`. Reconnect and check:

```bash
./server.sh status
./server.sh logs
```

## 8. Development checks

From the repository root:

```bash
python3 -m pytest -q
python3 -m compileall -q scraper
git diff --check
```

The offline demo is also a quick end-to-end check:

```bash
./server.sh demo
```

The lower-level launcher is still available for advanced use:

```bash
./run.sh --demo
./run.sh --config /absolute/path/to/config.yaml
```

## Architecture at a glance

```text
config.local.yaml
    ↓
AppConfig → Pipeline
    ↓
MapsCollector (Playwright reads raw browser data)
    ↓
normalize_listing (pure cleaning and schema projection)
    ↓
dedup → pre-filters → website enrichment
    ↓
MX/SMTP verification → review analysis → post-filters
    ↓
quality gate → atomic CSV → checkpoint → XLSX + summary
```

For module responsibilities, data contracts, normalization rules, failure
handling, and extension guidance, read
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## License

Original, self-contained code. Not derived from any other project.
