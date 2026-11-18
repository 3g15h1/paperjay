# Installing PaperJay

Three ways: Docker, the Proxmox installer, or by hand. All keep everything in one data folder; back that
folder up ([backups.md](backups.md)) and you have everything.

After installing, open PaperJay in a browser. The first visit asks for your account, the languages your
documents are written in, and (optionally) an AI server. Then add the rest of the household in
**Settings → People**.

## Docker

```sh
mkdir paperjay && cd paperjay
curl -fsSLO https://raw.githubusercontent.com/3g15h1/paperjay/main/docker-compose.yml
docker compose up -d
```

Open `http://<this machine>:8000`. Your data is in `./data`; files dropped into `./consume` are added
automatically. The image has the OCR tools and English, German, French, Spanish, Italian, Dutch and
Portuguese. For other languages, build your own:

```sh
docker build --build-arg OCR_LANGS="eng pol ukr" -t paperjay https://github.com/3g15h1/paperjay.git
```

The container starts as root only to give the data folder to user `PUID`/`PGID` (default 1000), then runs
as that user. The compose file has a commented-out Ollama service for a local AI on the same machine.

**Updating:** `docker compose pull && docker compose up -d`. The database is backed up to `data/backups`
before any change to its structure.

## Proxmox VE

On the Proxmox host, as root:

```sh
bash -c "$(curl -fsSL https://raw.githubusercontent.com/3g15h1/paperjay/main/install.sh)"
```

It creates an unprivileged Debian 13 container (2 cores, 3 GB, 16 GB disk, starts at boot, tagged
`paperjay`), installs the OCR tools and PaperJay as a systemd service on port 80, and prints the address.
Options (`install.sh --help`):

| Option | Default | |
|---|---|---|
| `--ip 192.168.1.60/24 --gw 192.168.1.1` | DHCP | a fixed address |
| `--ocr-langs "eng deu"` | `eng` | Tesseract languages (Debian `tesseract-ocr-*` packages) |
| `--cores --memory --disk` | 2, 3072, 16 | size |
| `--storage NAME --bridge NAME --vmid N --hostname NAME` | first suitable | placement |
| `--ref v0.2.0` | `main` | install a particular release |
| `--dry-run` | | show every step without doing it |

**Updating:** use **Settings → Updates → Update**, or run the installer again. Each release is installed
next to the previous one; the database is backed up first, and if the new version doesn't start, PaperJay
switches back to the previous one and its database by itself.

**New versions:** PaperJay asks GitHub every few hours whether a newer release is out, and people who can
change settings then see a note at the top of every page (**Update** or **Not now**; "Not now" hides it until
the next release). **Settings → Updates → Check for a new version** asks right away. Turn the automatic check
off there, or with `UPDATE_CHECK=false` in `/etc/paperjay/env` (or the Docker environment). While the
repository is private, GitHub only answers with a token: put it in `/etc/paperjay/github-token` (the installer's
`--github-token-file` does this); the updater lets PaperJay read it. Another place: `GITHUB_TOKEN_FILE`. Inside the container: data in
`/var/lib/paperjay`, settings in `/etc/paperjay/env`, logs with `journalctl -u paperjay`.

## By hand (any Linux)

```sh
sudo apt install python3-venv tesseract-ocr tesseract-ocr-eng ghostscript unpaper poppler-utils pngquant
python3 -m venv ~/paperjay && ~/paperjay/bin/pip install https://github.com/3g15h1/paperjay/archive/refs/heads/main.tar.gz
DATA_DIR=~/paperjay-data ~/paperjay/bin/paperjay serve
```

## Settings at startup

Most settings live in the app. These are read when PaperJay starts:

| Variable | Default | |
|---|---|---|
| `DATA_DIR` | `./data` | database, files and backups |
| `CONSUME_DIR` | `DATA_DIR/consume` | folder watched for new files; `off` to turn off |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | where it listens |
| `HTTPS_PORT` | `8443` (`443` in the Proxmox container) | PaperJay's own https, for the live camera scanner (see [phones](phone.md)); `off` to turn off, e.g. behind a reverse proxy |
| `HTTPS_NAMES` | | extra names or addresses for its certificate, e.g. `192.168.1.20, nas.lan` (it adds this machine's own, and the address you open it at) |
| `OCR_WORKERS` | `1` | documents read at the same time (the CPU cores are shared between them) |
| `MAX_UPLOAD_MB` / `MAX_PAGES` / `OCR_TIMEOUT` | 200 / 500 / 1800 | limits per document |
| `OCR_LANGUAGE`, `DATE_ORDER`, `AI_PROVIDER`, `AI_URL`, `AI_MODEL`, `AI_API_KEY`, `EMBED_MODEL`, `AI_THREADS` | | first values for those settings (the app's own settings win) |

## Command line

```text
paperjay serve                     run the web app
paperjay check [--quick]           verify every file against its checksum
paperjay backup                    copy the database to DATA_DIR/backups
paperjay export DIR [--prune]      plain-folder export (see export.md)
paperjay import-paperless URL --token-file F [--dry-run]
paperjay import-export DIR         bring an export back
paperjay add-admin NAME            create an admin, e.g. when locked out
```

## Reaching it from outside

Keep PaperJay on your home network. For access from anywhere, use a VPN such as Tailscale or WireGuard,
or put it behind a reverse proxy that adds HTTPS (Caddy, Traefik, nginx). Tell PaperJay which address the
proxy connects from, so it trusts the proxy's `X-Forwarded-*` headers (and marks sign-in cookies
HTTPS-only): `FORWARDED_ALLOW_IPS=192.168.1.10` (default: only a proxy on the same machine).
