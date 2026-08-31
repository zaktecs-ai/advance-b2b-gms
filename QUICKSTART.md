# Advance B2B GMS — Quick Start (no programming needed)

You need exactly **three commands** to install and run the scraper on your OCI
Oracle Cloud server. Everything else below is explanation.

## The three commands

Run these on the server, one after the other:

```bash
# 1. Get the code (only the first time)
git clone https://github.com/zaktecs-ai/advance-b2b-gms.git && cd advance-b2b-gms

# 2. Install everything (Python + Playwright + Chromium)
bash server.sh setup

# 3. Edit your searches, then run
./server.sh config      # opens your settings; change queries, then save + exit
./server.sh run         # start the real scraper
```

That is the whole setup. You do **not** need to remember Git, Python, tmux, or
Playwright commands.

> If the folder is already cloned, skip command 1 and just `cd advance-b2b-gms`.

---

## Everyday use (after setup)

| Want to… | Run this |
| --- | --- |
| Test safely without contacting Google | `./server.sh demo` |
| Start the real scraper | `./server.sh run` |
| See if it is running | `./server.sh status` |
| Watch the progress live | `./server.sh logs` |
| Stop it cleanly | `./server.sh stop` |
| Change searches / settings | `./server.sh config` |

## Pull the latest update (after we push changes)

Exactly two commands:

```bash
./server.sh update    # downloads new code + refreshes dependencies
./server.sh run       # start / resume the scraper
```

`update` never overwrites your settings (`config.local.yaml`), your secrets
(`.env`), or your saved results (`output/`).

---

## Optional: VNC screen for CAPTCHA solving

By default the scraper runs "headless" (no visible window) — this is the
recommended, safest mode. Use VNC only when you want to *watch* the browser or
solve a CAPTCHA yourself.

```bash
# 1. Start a separate, non-common-ported screen
./vnc-screen.sh

# 2. Open your settings and set headless to false
./server.sh config          # change:  maps.headless: false

# 3. Connect your TightVNC viewer to the printed address (your-ip:43873)
#    and start the scraper
./server.sh run
```

To stop the screen afterwards:

```bash
./vnc-screen.sh stop
```

---

## Where your results go

The scraper writes to `output/<client_name>/` — for example
`output/houston-plumbers/` contains `leads.csv`, `leads.xlsx`, `summary.json`,
`run.log`, and a checkpoint so an interrupted job can resume.
