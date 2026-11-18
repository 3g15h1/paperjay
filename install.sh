#!/usr/bin/env bash
# PaperJay installer for Proxmox VE. Run as root on the Proxmox host:
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/3g15h1/paperjay/main/install.sh)"
#
# Creates an unprivileged Debian 13 container with PaperJay as a systemd service on port 80, then prints
# its address; the first visit opens the setup. Run it again to upgrade: the container is found by its
# "paperjay" tag, your documents and settings stay, and the database is backed up first.
set -euo pipefail

REF=main VMID="" HOSTNAME_=paperjay STORAGE="" BRIDGE=vmbr0 IP=dhcp GW="" CORES=2 MEMORY=3072 DISK=16
SRC="" TOKEN_FILE="" LANGS="eng" DRY=0 DNS=""
TAG=paperjay

usage() {
  cat <<EOF
Usage: install.sh [options]

  --vmid N            container ID (default: next free; with an existing PaperJay container: upgrade it)
  --hostname NAME     container name (default: paperjay)
  --storage NAME      storage for the container disk (default: the first that holds containers)
  --bridge NAME       network bridge (default: vmbr0)
  --ip CIDR|dhcp      address, e.g. 192.168.1.60/24 (default: dhcp); --gw IP is needed with a static one
  --cores N --memory MB --disk GB   size (default: 2 cores, 3072 MB, 16 GB)
  --ocr-langs "eng deu"   Tesseract languages to install (default: eng); see apt's tesseract-ocr-* packages
  --nameserver "IP IP"    DNS servers for the container (default: the host's, unless a container can't
                          use them, e.g. Tailscale's MagicDNS; then 1.1.1.1 9.9.9.9)
  --ref REF           release tag or branch to install (default: main)
  --src DIR|FILE      install from a local checkout or source .tar.gz instead of downloading
  --github-token-file F   token for a private repository (kept in the container for updates)
  --dry-run           print every change instead of making it
  -h, --help          this help
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --vmid) VMID=$2; shift 2 ;;
    --hostname) HOSTNAME_=$2; shift 2 ;;
    --storage) STORAGE=$2; shift 2 ;;
    --bridge) BRIDGE=$2; shift 2 ;;
    --ip) IP=$2; shift 2 ;;
    --gw) GW=$2; shift 2 ;;
    --cores) CORES=$2; shift 2 ;;
    --memory) MEMORY=$2; shift 2 ;;
    --disk) DISK=$2; shift 2 ;;
    --ocr-langs) LANGS=$2; shift 2 ;;
    --nameserver) DNS=$2; shift 2 ;;
    --ref) REF=$2; shift 2 ;;
    --src) SRC=$2; shift 2 ;;
    --github-token-file) TOKEN_FILE=$2; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
run() { if [ "$DRY" = 1 ]; then printf '+ %s\n' "$*"; else "$@"; fi; }
in_ct() { if [ "$DRY" = 1 ]; then printf '+ pct exec %s -- bash -c %q\n' "$VMID" "$1"; else pct exec "$VMID" -- bash -c "$1"; fi; }

# ------------------------------------------------------------------ preflight
[ "$(id -u)" = 0 ] || die "run as root on the Proxmox host"
for c in pveversion pct pveam pvesm pvesh; do command -v "$c" >/dev/null || die "'$c' not found: run this on a Proxmox VE host"; done
if [ "$IP" != dhcp ] && [ -z "$GW" ]; then die "--ip needs --gw"; fi
[[ "$LANGS" =~ ^[a-z_\ ]+$ ]] || die "--ocr-langs takes names like \"eng deu\""
[[ "$REF" =~ ^[A-Za-z0-9._/-]+$ ]] || die "bad --ref"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# ------------------------------------------------------------------ source
if [ -n "$SRC" ] && [ -d "$SRC" ]; then
  [ -f "$SRC/paperjay/__init__.py" ] || die "$SRC isn't a PaperJay checkout"
  tar -czf "$WORK/src.tgz" -C "$(dirname "$(realpath "$SRC")")" --exclude=.git --exclude=.venv --exclude='__pycache__' \
      --exclude=data --exclude=demo-data "$(basename "$(realpath "$SRC")")"
elif [ -n "$SRC" ]; then
  cp "$SRC" "$WORK/src.tgz"
else
  AUTH=()
  [ -n "$TOKEN_FILE" ] && AUTH=(-H "Authorization: Bearer $(cat "$TOKEN_FILE")")
  say "Downloading PaperJay ($REF)"
  curl -fsSL "${AUTH[@]}" -o "$WORK/src.tgz" "https://api.github.com/repos/3g15h1/paperjay/tarball/$REF" ||
    die "couldn't download $REF (private repository? use --github-token-file)"
fi

# ------------------------------------------------------------------ find or create the container
if [ -z "$VMID" ]; then
  for id in $(pct list | awk 'NR>1 {print $1}'); do
    if pct config "$id" | grep -qE "^tags:.*\b$TAG\b"; then VMID=$id; break; fi
  done
fi
UPGRADE=0
if [ -n "$VMID" ] && pct config "$VMID" >/dev/null 2>&1; then
  pct config "$VMID" | grep -qE "^tags:.*\b$TAG\b" || die "CT $VMID exists but isn't a PaperJay container"
  UPGRADE=1
  say "Upgrading PaperJay in CT $VMID"
  [ "$(pct status "$VMID" | awk '{print $2}')" = running ] || run pct start "$VMID"
else
  VMID=${VMID:-$(pvesh get /cluster/nextid)}
  if [ -z "$STORAGE" ]; then
    STORAGE=$(pvesm status --content rootdir | awk 'NR>1 && $3=="active" {print $1; exit}')
    [ -n "$STORAGE" ] || die "no active storage for containers; pass --storage"
  fi
  TSTORE=$(pvesm status --content vztmpl | awk 'NR>1 && $3=="active" {print $1; exit}')
  [ -n "$TSTORE" ] || die "no storage for container templates"
  run pveam update >/dev/null
  TEMPLATE=$(pveam available --section system | awk '{print $2}' | grep -E '^debian-13-standard_.*amd64' | sort -V | tail -n1)
  [ -n "$TEMPLATE" ] || die "Debian 13 template not found in pveam"
  pveam list "$TSTORE" | grep -q "$TEMPLATE" || { say "Downloading $TEMPLATE"; run pveam download "$TSTORE" "$TEMPLATE"; }
  if [ -z "$DNS" ]; then
    # The host's resolvers, unless a container can't reach them: Tailscale's MagicDNS address
    # and local stubs (127.x, e.g. systemd-resolved) only work on the host itself.
    HOST_DNS=$(awk '/^nameserver/ {print $2}' /etc/resolv.conf | grep -vE '^(127\.|100\.100\.100\.100$|fd7a:115c:a1e0:|::1$)' | head -n2 | xargs)
    DNS=${HOST_DNS:-1.1.1.1 9.9.9.9}
  fi
  NET="name=eth0,bridge=$BRIDGE,ip=$IP"
  [ "$IP" != dhcp ] && NET="$NET,gw=$GW"
  say "Creating CT $VMID ($HOSTNAME_: $CORES cores, $MEMORY MB, $DISK GB on $STORAGE)"
  run pct create "$VMID" "$TSTORE:vztmpl/$TEMPLATE" --hostname "$HOSTNAME_" --cores "$CORES" --memory "$MEMORY" \
      --swap 512 --rootfs "$STORAGE:$DISK" --net0 "$NET" --nameserver "$DNS" --unprivileged 1 --features nesting=1 --onboot 1 \
      --tags "$TAG" --description "PaperJay document archive. Upgrade: run install.sh again on the host."
  run pct start "$VMID"
  say "Waiting for the network (DNS: $DNS)"
  NET_OK=0
  for _ in $(seq 1 30); do
    [ "$DRY" = 1 ] && { NET_OK=1; break; }
    if pct exec "$VMID" -- timeout 5 getent hosts deb.debian.org >/dev/null 2>&1; then NET_OK=1; break; fi
    sleep 2
  done
  [ "$NET_OK" = 1 ] || die "CT $VMID can't resolve deb.debian.org with DNS $DNS: check its network, or pass --nameserver"
  PKGS="python3 python3-venv curl ca-certificates tesseract-ocr ghostscript unpaper poppler-utils pngquant"
  for l in $LANGS; do PKGS="$PKGS tesseract-ocr-$l"; done
  say "Installing OCR tools ($LANGS)"
  in_ct "export DEBIAN_FRONTEND=noninteractive LC_ALL=C.UTF-8 LANG=C.UTF-8; apt-get update -q && apt-get install -y -q --no-install-recommends $PKGS >/dev/null"
  in_ct "id paperjay >/dev/null 2>&1 || useradd --system --home-dir /var/lib/paperjay --shell /usr/sbin/nologin paperjay
    mkdir -p /var/lib/paperjay/consume /etc/paperjay /opt/paperjay && chown -R paperjay:paperjay /var/lib/paperjay
    [ -f /etc/paperjay/env ] || cat > /etc/paperjay/env <<'ENV'
# PaperJay settings for systemd (see README). Changes need: systemctl restart paperjay
DATA_DIR=/var/lib/paperjay
PORT=80
# https for the live camera scanner (see Settings → Secure address); off to disable
HTTPS_PORT=443
OCR_WORKERS=1
PAPERJAY_INSTALL=lxc
PAPERJAY_UPDATE_DIR=/var/lib/paperjay
ENV
    chmod 644 /etc/paperjay/env"
fi
# The host's time zone, so nightly times (mailbox checks, AI re-reads) are the household's, not UTC
HOST_TZ=$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || true)
if [[ "$HOST_TZ" =~ ^[A-Za-z0-9_+/-]+$ ]]; then
  in_ct "grep -q '^TZ=' /etc/paperjay/env || printf 'TZ=%s\n' '$HOST_TZ' >> /etc/paperjay/env"
fi
if [ -n "$TOKEN_FILE" ]; then
  run pct push "$VMID" "$TOKEN_FILE" /etc/paperjay/github-token --perms 600
fi

# ------------------------------------------------------------------ install the release
run pct push "$VMID" "$WORK/src.tgz" /root/paperjay-src.tgz
in_ct 'D=$(mktemp -d); trap "rm -rf $D /root/paperjay-src.tgz" EXIT
  tar -xzf /root/paperjay-src.tgz -C "$D" && bash "$D"/*/deploy/update.sh /root/paperjay-src.tgz' ||
  die "the install inside CT $VMID failed (the previous version, if any, is still in place)"

[ "$DRY" = 1 ] && exit 0
say "Checking that PaperJay answers"
for _ in $(seq 1 60); do
  if pct exec "$VMID" -- python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1/healthz', timeout=3)" 2>/dev/null; then
    ADDR=$(pct exec "$VMID" -- hostname -I | awk '{print $1}')
    if [ "$UPGRADE" = 1 ]; then say "PaperJay in CT $VMID is upgraded: http://$ADDR/ (camera scanner: https://$ADDR/)"
    else say "PaperJay is ready in CT $VMID: open http://$ADDR/ to set it up (camera scanner: https://$ADDR/)"; fi
    exit 0
  fi
  sleep 2
done
die "PaperJay didn't answer in CT $VMID; see: pct exec $VMID -- journalctl -u paperjay -n 50"
