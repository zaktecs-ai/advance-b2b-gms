# Advance B2B GMS

## Google Maps B2B Lead Scraper

Advance B2B GMS collects business listings from Google Maps, enriches the
business website, checks useful sales signals, and writes clean CSV/XLSX files.
It is a command-line application designed for a Linux server or VPS.

The current export has **75 producer-backed columns**. Missing values are written
as `N/A`; unsupported Google Maps fields are not added as empty columns.

## What the program does

For each search query, the program:

1. Opens Google Maps with Playwright and collects listing cards.
2. Clicks each listing to read the detail panel.
3. Cleans names, addresses, URLs, text, and global phone numbers.
4. Removes duplicate businesses.
5. Crawls a small number of relevant website pages.
6. Finds emails, social profiles, technologies, and business signals.
7. Optionally extracts a decision-maker name and title.
8. Analyzes reviews and creates a lead score and pitch hook.
9. Writes `leads.csv`, `leads.xlsx`, and `summary.json`.
10. Saves a checkpoint so an interrupted job can continue later.

## 1. First-time installation on a server

These commands assume Ubuntu 22.04/24.04 and the repository is installed in
`/opt/advance-b2b-gms`. You can use another directory; just change the `cd`
path in every command.

### Connect to the server

Run this on your own computer:

```bash
ssh YOUR_USER@YOUR_SERVER_IP
```

### Download and install the project

Run these commands after you are connected to the server:

```bash
sudo mkdir -p /opt
sudo chown "$USER":"$USER" /opt
cd /opt
git clone https://github.com/zaktecs-ai/advance-b2b-gms.git advance-b2b-gms
cd /opt/advance-b2b-gms
chmod +x setup.sh run.sh
./setup.sh
cp .env.example .env
```

`setup.sh` creates `.venv`, installs the Python packages, and installs the
Playwright Chromium browser. It may ask for the server user's `sudo` password.

### Confirm the installation

Run the offline demo first. It does not open a browser and does not contact
Google Maps:

```bash
cd /opt/advance-b2b-gms
./run.sh --demo
```

If the demo completes, the installation is working. Its files are written under
`output/demo/` unless you changed the configuration.

## 2. Configure a real scrape

### Edit the search queries

```bash
cd /opt/advance-b2b-gms
nano config.yaml
```

Start with a small job. For example:

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

reviews:
  enabled: true
  per_business: 5

enrichment:
  decision_makers: true
  mx_verify: false
  smtp_verify: false
```

Important settings:

| Setting | Purpose |
| --- | --- |
| `job.client_name` | Creates the output folder name. Use letters, numbers, `_`, or `-`. |
| `job.default_country` | Region used for national-format phone numbers, such as `PK`, `US`, or `GB`. |
| `job.max_results_per_query` | Maximum listings per query; `0` means unlimited. |
| `job.max_total_results` | Maximum listings for the entire job; `0` means unlimited. |
| `maps.headless` | Keep `true` on a normal server. Set `false` only when VNC is ready. |
| `reviews.enabled` | Collect review text for sentiment and scoring. |
| `enrichment.decision_makers` | Read names/titles from fetched about/team pages. |
| `enrichment.mx_verify` | Check whether the email domain has an MX record. |
| `enrichment.smtp_verify` | Perform an SMTP mailbox probe; this is slower and often inconclusive. |

Do not put API keys in `config.yaml`. The optional AI hook keys belong in
`.env`:

```bash
nano .env
```

```dotenv
OPENAI_API_KEY=
DEEPSEEK_API_KEY=
```

Then enable the selected provider in `config.yaml` only if you want AI hooks:

```yaml
ai_hook:
  enabled: true
  provider: openai
```

The rule-based pitch hook remains available when AI is disabled, a key is
missing, or the AI request fails.

## 3. Commands to run the scraper

Run commands from the repository directory.

### Show the installed version

```bash
./run.sh --version
```

### Run the offline demo

```bash
./run.sh --demo
```

### Run the live Google Maps job

```bash
./run.sh
```

### Use a different configuration file

```bash
./run.sh --config /absolute/path/to/other-config.yaml
```

### Resume an interrupted job

Run the same command again:

```bash
./run.sh
```

The SQLite checkpoint is under `output/<client_name>/checkpoint.sqlite`. Already
completed queries are skipped, and committed records are used for deduplication.
Do not delete the checkpoint unless you intentionally want to start a new job.

### Run in the background with `nohup`

```bash
nohup ./run.sh > run-console.log 2>&1 &
echo $!
```

Watch the console output:

```bash
tail -f run-console.log
```

Find the process later:

```bash
pgrep -af "scraper.main"
```

Stop it gracefully by replacing `PROCESS_ID` with the PID printed by `pgrep`:

```bash
kill PROCESS_ID
```

### Run inside `tmux` (recommended for long jobs)

```bash
tmux new -s abgms
cd /opt/advance-b2b-gms
./run.sh
```

Detach without stopping the job by pressing `Ctrl+B`, then `D`. Reconnect later:

```bash
tmux attach -t abgms
```

## 4. Updating the code on the server

This is the normal update procedure after a new commit has been pushed to
GitHub. Stop the current scraper before updating; never run `git pull` while a
job is actively writing output files.

```bash
cd /opt/advance-b2b-gms

# Check for an active scraper and stop it before updating, if necessary.
pgrep -af "scraper.main"
# kill PROCESS_ID

# Back up local configuration and secrets first.
cp config.yaml "config.yaml.backup-$(date +%F-%H%M%S)"
if [ -f .env ]; then cp .env ".env.backup-$(date +%F-%H%M%S)"; fi

# Check whether you have local code changes.
git status --short

# Download and apply the latest main branch.
git fetch origin
git checkout main
git pull --ff-only origin main
git log -1 --oneline

# Install any newly added Python dependencies.
source .venv/bin/activate
python -m pip install -r requirements.txt

# Use this only when Playwright reports that Chromium is missing.
python -m playwright install chromium

# Verify before starting a real job.
./run.sh --demo
```

Then run the live job:

```bash
./run.sh
```

### If `git pull` stops because of local changes

Do not use `git reset --hard` unless you intentionally want to discard those
changes. First inspect the files:

```bash
git status --short
git diff -- config.yaml .env
```

If the only local changes are your configuration, copy the backups, restore the
configuration after updating, and then pull again. If you are unsure, stop and
review the diff before choosing a Git command.

## 5. VNC/headed mode for CAPTCHA solving

Headless mode is the default. Use headed mode only when a VNC desktop is
already running on the server:

```yaml
maps:
  headless: false

vnc:
  display: ":2"
  resolution: "1366x900"
```

The repository does not create or secure your VNC server. A common TightVNC
setup is:

```bash
sudo apt-get update
sudo apt-get install -y tightvncserver
vncserver :2 -geometry 1366x900 -depth 24
```

Run the scraper from the same server account that owns the VNC session:

```bash
cd /opt/advance-b2b-gms
./run.sh
```

Stop that VNC display when finished:

```bash
vncserver -kill :2
```

Do not expose port `5902` directly to the public internet. Prefer an SSH tunnel
from your computer:

```bash
ssh -L 5902:127.0.0.1:5902 YOUR_USER@YOUR_SERVER_IP
```

If Google shows a CAPTCHA, solve it in the VNC browser. A blocked or challenged
query is left retryable instead of being marked as successfully completed.

## 6. Where the output files are

For `client_name: houston-plumbers`, files are created here:

```text
output/houston-plumbers/
├── leads.csv
├── leads.xlsx
├── summary.json
├── run.log
├── checkpoint.sqlite
└── checkpoint.json
```

The CSV and XLSX use the same 75-column order from
`scraper/models.py`. The export intentionally excludes unsupported fields such
as timezone, popular times, competitors, ownership posts, gas prices, featured
questions, and rating buckets.

## 7. Common problems

### `Permission denied: ./run.sh`

The repository normally stores both launcher scripts as executable. Fix a copy
that lost its file permissions:

```bash
chmod +x setup.sh run.sh
```

### `No module named ...`

Activate the environment and refresh dependencies:

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### `Chromium binary is missing`

```bash
source .venv/bin/activate
python -m playwright install chromium
```

### The server has an old 85-column CSV

The current writer fails closed rather than appending 75-column rows to an old
header. Back up the old client output, choose a new client name or output
folder, and run again:

```bash
mv output/OLD_CLIENT "output/OLD_CLIENT.backup-$(date +%F-%H%M%S)"
./run.sh
```

Only do this after confirming you no longer need the old checkpoint and export.

### The job says zero listings were extracted

Check the query in `config.yaml`. Zero listings can mean a real empty result,
Google consent, a CAPTCHA, a selector change, or a temporary block. Try headed
VNC mode, inspect `output/<client_name>/run.log`, and rerun after resolving the
browser challenge.

### A job is still running after an SSH disconnect

If it was started with `tmux` or `nohup`, reconnect with `tmux attach -t abgms`
or inspect it with:

```bash
pgrep -af "scraper.main"
```

## 8. Development checks

From the repository root:

```bash
source .venv/bin/activate
python -m pytest -q
python -m compileall -q scraper
git diff --check
```

The test suite covers the schema contract, text and phone normalization,
international address parsing, decision-maker propagation, signal mapping,
checkpoint recovery, and the offline pipeline demo.

## Architecture at a glance

```text
config.yaml
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

For the detailed data contracts and extension rules, read
`docs/ARCHITECTURE.md`.

## License

Original, self-contained code. Not derived from any other project.
