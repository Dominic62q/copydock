#!/bin/sh
# CopyDock installer. Runs on the destination server; needs only Docker + Docker Compose.
#
#   ./install.sh               verify, check the server, ask for settings, load images, start
#   ./install.sh --check       verify and check the server only; changes nothing
#   ./install.sh --help
#
# Safe to run again: it never deletes volumes and never prints setting values.
# Nothing here downloads anything: images come from images/images.tar and compose runs with
# --pull never.

set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"

MIN_COMPOSE="2.20.0" # compose.yaml uses depends_on.required, added in Compose 2.20

# ---------------------------------------------------------------- terminal -------------------
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  B=$(printf '\033[1m'); D=$(printf '\033[2m'); R=$(printf '\033[31m'); G=$(printf '\033[32m')
  Y=$(printf '\033[33m'); C=$(printf '\033[36m'); Z=$(printf '\033[0m')
else
  B=''; D=''; R=''; G=''; Y=''; C=''; Z=''
fi

banner() {
  printf '\n  %s▛▀▜▛▀▜%s  %scopydock%s installer\n' "$C$B" "$Z" "$B" "$Z"
  printf '  %s▙▄▟▙▄▟%s  %s%s%s\n\n' "$C$B" "$Z" "$D" "$1" "$Z"
}
ok()   { printf ' %s✓%s  %-34s %s%s%s\n' "$G$B" "$Z" "$1" "$D" "${2:-}" "$Z"; }
bad()  { printf ' %s✗%s  %-34s %s\n' "$R$B" "$Z" "$1" "${2:-}"; FAILS=$((FAILS + 1)); }
warn() { printf ' %s▲%s  %-34s %s\n' "$Y$B" "$Z" "$1" "${2:-}"; }
note() { printf '    %s%s%s\n' "$D" "$1" "$Z"; }
die()  { printf '\n %s✗%s  %s\n\n' "$R$B" "$Z" "$1" >&2; exit 1; }

FAILS=0
cleanup() { [ -t 0 ] && stty echo 2>/dev/null || true; rm -f "$HERE/.env.tmp" "$HERE/.copydock-compose.log"; }
trap cleanup EXIT
trap 'exit 130' INT TERM

usage() {
  cat <<'EOF'
Usage: ./install.sh [options]

  --check             Verify the bundle and check this server. Changes nothing.
  --non-interactive   Never ask questions. Fails if .env is missing or incomplete.
  --env-file PATH     Use PATH as .env (only when .env does not exist yet).
  -h, --help          Show this help.

Settings go in .env next to this script (mode 600). See config/environment.example.
EOF
}

MODE=install
INTERACTIVE=1
ENV_SRC=""
while [ $# -gt 0 ]; do
  case "$1" in
    --check) MODE=check ;;
    --non-interactive | --yes | -y) INTERACTIVE=0 ;;
    --env-file) [ $# -ge 2 ] || die "--env-file needs a path"; ENV_SRC=$2; shift ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; die "Unknown option: $1" ;;
  esac
  shift
done
[ -t 0 ] || INTERACTIVE=0

have() { command -v "$1" >/dev/null 2>&1; }

human_kb() { # kilobytes -> "1.2 GB"
  awk -v k="$1" 'BEGIN { if (k >= 1048576) printf "%.1f GB", k / 1048576; else printf "%.0f MB", k / 1024 }'
}

# ---------------------------------------------------------------- 1. verify bundle -----------
banner "installing from $(basename "$HERE")"

for f in checksums.txt bundle.conf compose.yaml images/images.tar; do
  [ -f "$f" ] || die "This does not look like a CopyDock bundle: $f is missing."
done

if have sha256sum; then SUM="sha256sum -c"; elif have shasum; then SUM="shasum -a 256 -c"; else SUM=""; fi
[ -n "$SUM" ] || die "Cannot verify the bundle: neither sha256sum nor shasum is installed."

# shellcheck disable=SC2086
if SUM_OUT=$($SUM checksums.txt 2>&1); then
  N=$(printf '%s\n' "$SUM_OUT" | grep -c ': OK$' || true)
  ok "Bundle files verified" "$N files intact"
else
  bad "Bundle files verified" "the bundle is damaged or was changed"
  printf '%s\n' "$SUM_OUT" | grep -v ': OK$' | head -5 | while IFS= read -r line; do note "$line"; done
  die "Do not install this bundle. Copy it again and compare it with the .sha256 file."
fi

# bundle.conf is written by CopyDock with restricted characters, and checked above.
# shellcheck disable=SC1091
. ./bundle.conf
note "this checks the files are intact; use the .sha256 file to check who made it"
printf '\n'

# ---------------------------------------------------------------- 2. preflight ---------------
printf ' %s%s %s%s  %s\n\n' "$B" "$APP_NAME" "$APP_VERSION" "$Z" "$PLATFORM"

# Operating system
if [ "$(uname -s)" = "Linux" ]; then ok "Linux server"; else bad "Linux server" "this is $(uname -s); bundles install on Linux"; fi

# Docker
DOCKER_OK=0
if ! have docker; then
  bad "Docker installed" "not found. Install Docker Engine, then run this again."
else
  if SERVER=$(docker version --format '{{.Server.Os}}/{{.Server.Arch}}' 2>&1); then
    DOCKER_OK=1
    ok "Docker running" "$(docker version --format 'Engine {{.Server.Version}}' 2>/dev/null || true)"
  else
    case "$SERVER" in
      *ermission*denied*) bad "Docker running" "permission denied. Run with sudo, or add your user to the docker group." ;;
      *) bad "Docker running" "cannot reach the Docker daemon. Is it started? (systemctl start docker)" ;;
    esac
  fi
fi

if [ "$DOCKER_OK" = 1 ]; then
  # Compose
  if CV=$(docker compose version --short 2>/dev/null); then
    CV=$(printf '%s' "$CV" | sed 's/^v//; s/[^0-9.].*$//')
    if awk -v a="$CV" -v b="$MIN_COMPOSE" 'BEGIN {
         n = split(a, x, "."); split(b, y, ".");
         for (i = 1; i <= 3; i++) { if (x[i] + 0 > y[i] + 0) exit 0; if (x[i] + 0 < y[i] + 0) exit 1 }
         exit 0 }'; then
      ok "Docker Compose" "v$CV"
    else
      bad "Docker Compose" "v$CV is too old; this bundle needs v$MIN_COMPOSE or newer"
    fi
  else
    bad "Docker Compose" "the 'docker compose' plugin was not found. Install Docker Compose v2."
  fi

  # CPU platform: what Docker will actually run, not just the CPU
  if [ "$SERVER" = "$PLATFORM" ]; then
    ok "CPU type matches" "$PLATFORM"
  else
    bad "CPU type matches" "this server is $SERVER but the bundle is $PLATFORM. Ask for a $SERVER bundle."
  fi
fi

# Disk space (an estimate: loaded layers are about the size of the image archive)
ARCHIVE_KB=$(( ($(wc -c < "$IMAGES_ARCHIVE") + 1023) / 1024 ))
NEED_KB=$(( ARCHIVE_KB * 3 / 2 + 512 * 1024 ))
DISK_DIR="."
if [ "$DOCKER_OK" = 1 ]; then
  ROOT=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)
  [ -n "$ROOT" ] && [ -d "$ROOT" ] && DISK_DIR=$ROOT
fi
HAVE_KB=$(df -Pk "$DISK_DIR" | awk 'NR == 2 { print $4 }')
if [ "$HAVE_KB" -ge "$NEED_KB" ]; then
  ok "Disk space" "$(human_kb "$HAVE_KB") free, need about $(human_kb "$NEED_KB") (estimate)"
else
  bad "Disk space" "$(human_kb "$HAVE_KB") free, need about $(human_kb "$NEED_KB") (estimate)"
fi

# Is this app already running here? Then its own ports are not a conflict.
RUNNING=""
if [ "$DOCKER_OK" = 1 ]; then
  RUNNING=$(docker ps -q --filter "label=com.docker.compose.project=$APP_NAME" 2>/dev/null || true)
fi

port_in_use() { # listening TCP sockets, read straight from the kernel: no extra tools needed
  hex=$(printf '%04X' "$1")
  for f in /proc/net/tcp /proc/net/tcp6; do
    [ -r "$f" ] || continue
    if awk -v h="$hex" 'NR > 1 && $4 == "0A" { n = split($2, a, ":"); if (a[n] == h) f = 1 } END { exit !f }' "$f"; then
      return 0
    fi
  done
  return 1
}

if [ -z "$REQUIRED_PORTS" ]; then
  ok "Ports" "none needed"
elif [ -n "$RUNNING" ]; then
  ok "Ports" "in use by this app's current deployment"
  WD=$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' \
       "$(printf '%s\n' "$RUNNING" | head -1)" 2>/dev/null || true)
  if [ -n "$WD" ] && [ "$WD" != "$HERE" ]; then
    warn "Existing deployment" "'$APP_NAME' already runs from $WD; this will update it"
  fi
else
  for p in $REQUIRED_PORTS; do
    if port_in_use "$p"; then
      bad "Port $p free" "something is already listening on it. Stop it, or use another port."
    else
      ok "Port $p free"
    fi
  done
fi

printf '\n'
if [ "$FAILS" -gt 0 ]; then
  die "$FAILS check(s) failed. Nothing was changed. Fix the above and run ./install.sh again."
fi
if [ "$MODE" = check ]; then
  printf ' %s✓%s  %sThis server is ready. Run ./install.sh to install.%s\n\n' "$G$B" "$Z" "$G" "$Z"
  exit 0
fi

# ---------------------------------------------------------------- 3. settings ----------------
env_value() { # value of $1 in .env, quotes removed; empty if absent
  [ -f .env ] || return 0
  sed -n "s/^$1=//p" .env | head -1 | sed "s/^'\(.*\)'\$/\1/; s/^\"\(.*\)\"\$/\1/"
}

is_secret_name() { case "$1" in *PASS* | *SECRET* | *KEY* | *TOKEN* | *PWD*) return 0 ;; *) return 1 ;; esac; }

random_value() { head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n'; }

ask() { # $1 = name; sets ANSWER
  if is_secret_name "$1"; then
    printf '    %s%s%s (press Enter to generate a random one): ' "$B" "$1" "$Z" >&2
    stty -echo 2>/dev/null || true
    IFS= read -r ANSWER || ANSWER=""
    stty echo 2>/dev/null || true
    printf '\n' >&2
    if [ -z "$ANSWER" ]; then ANSWER=$(random_value); note "generated and saved to .env"; fi
  else
    printf '    %s%s%s: ' "$B" "$1" "$Z" >&2
    IFS= read -r ANSWER || ANSWER=""
  fi
}

if [ ! -f .env ] && [ -n "$ENV_SRC" ]; then
  [ -f "$ENV_SRC" ] || die "--env-file: $ENV_SRC not found."
  ( umask 077; cp "$ENV_SRC" .env )
fi
if [ ! -f .env ]; then ( umask 077; : > .env ); fi
chmod 600 .env

MISSING=""
for name in $REQUIRED_ENV; do
  [ -n "$(env_value "$name")" ] || MISSING="$MISSING $name"
done

if [ -z "$MISSING" ]; then
  ok "Settings" "$(printf '%s\n' $REQUIRED_ENV | grep -c . || true) values present in .env"
elif [ "$INTERACTIVE" = 0 ]; then
  bad "Settings" "missing in .env:$MISSING"
  die "Fill them in (see config/environment.example) and run again. Nothing else was changed."
else
  printf ' %s●%s  %sSettings%s  %sstored only in %s/.env (mode 600), never shown again%s\n' "$C$B" "$Z" "$B" "$Z" "$D" "$HERE" "$Z"
  for name in $MISSING; do
    ask "$name"
    case "$ANSWER" in
      '') die "$name cannot be empty." ;;
      *"'"* | *"
"*) die "$name cannot contain a single quote or a line break." ;;
    esac
    ( umask 077; grep -v "^$name=" .env > .env.tmp || true; printf "%s='%s'\n" "$name" "$ANSWER" >> .env.tmp; mv .env.tmp .env )
  done
  ANSWER=""
  ok "Settings saved" ".env (mode 600)"
fi
printf '\n'

# ---------------------------------------------------------------- 4. load images -------------
LOG="$HERE/.copydock-compose.log"
docker load -i "$IMAGES_ARCHIVE" >"$LOG" 2>&1 || {
  tail -8 "$LOG" | while IFS= read -r line; do note "$line"; done
  die "Loading the images failed (see above). Nothing was started."
}
for ref in $IMAGES; do
  docker image inspect "$ref" >/dev/null 2>&1 || die "Image $ref is missing after loading. The bundle may be incomplete."
done
ok "Images loaded" "$(printf '%s\n' $IMAGES | grep -c . || true) images, no download"

# ---------------------------------------------------------------- 5. start -------------------
if docker compose --project-directory "$HERE" -f compose.yaml up -d --pull never --no-build \
     --wait --wait-timeout 180 >"$LOG" 2>&1; then
  ok "Application started" "waited for health checks"
else
  bad "Application started" "one or more services failed"
  tail -12 "$LOG" | while IFS= read -r line; do note "$line"; done
  note "Details: docker compose logs <service>   (data is untouched)"
  exit 1
fi

# ---------------------------------------------------------------- 6. verify + hand over ------
printf '\n'
docker compose --project-directory "$HERE" -f compose.yaml ps -a --format '{{.Service}}|{{.State}}|{{.Health}}' |
  sort | while IFS='|' read -r svc state health; do
    detail="$state"; [ -n "$health" ] && detail="$state, $health"
    if [ "$state" = running ]; then ok "$svc" "$detail"; else warn "$svc" "$detail"; fi
  done

http_code() {
  if have curl; then
    curl -s -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:$1/" 2>/dev/null || printf '000'
  elif have wget && wget -q -T 5 -O /dev/null "http://127.0.0.1:$1/" 2>/dev/null; then
    printf '200'
  else
    printf '000'
  fi
}

IP=$(hostname -I 2>/dev/null | awk '{ print $1 }' || true)
[ -n "${IP:-}" ] || IP="<this-server's-address>"
URLS=""
for p in $REQUIRED_PORTS; do
  CODE=000; i=0
  while [ "$i" -lt 5 ] && [ "$CODE" = 000 ]; do CODE=$(http_code "$p"); i=$((i + 1)); [ "$CODE" = 000 ] && sleep 1; done
  if [ "$CODE" != 000 ]; then
    if [ "$p" = 80 ]; then URLS="$URLS http://$IP"; else URLS="$URLS http://$IP:$p"; fi
    ok "Port $p answers" "HTTP $CODE"
  else
    warn "Port $p" "no HTTP answer (fine if this is not a web port)"
  fi
done

printf '\n  %s%s is installed.%s\n' "$G$B" "$APP_NAME" "$Z"
for u in $URLS; do printf '  Open  %s%s%s\n' "$B" "$u" "$Z"; done
printf '\n  %sManage it from this folder:%s  cd %s\n' "$D" "$Z" "$HERE"
printf '  %sStatus%s  docker compose ps\n' "$D" "$Z"
printf '  %sLogs  %s  docker compose logs -f <service>\n' "$D" "$Z"
printf '  %sStop  %s  docker compose stop\n' "$D" "$Z"
printf '\n  %sYour data lives in Docker volumes and survives restarts and re-installs.%s\n' "$D" "$Z"
printf '  %sNever run "docker compose down -v": it deletes that data.%s\n\n' "$D" "$Z"
