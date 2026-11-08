#!/bin/bash
# Install a CopyDock bundle on a server with NO network at all.
#
# It starts a second Docker daemon (docker:dind) with `--network none`: no internet, no LAN, only
# loopback. That daemon starts with no images. The bundle is copied in and ./install.sh is run
# there. If anything tried to download an image, it would fail.
#
#   scripts/offline-test.sh path/to/sample-1.0-linux-amd64.tar.gz
#
# Needs: Docker, and the docker:dind image already pulled (docker pull docker:dind).
# Leaves nothing behind: the container and its volumes are removed at the end.

set -eu

BUNDLE=${1:?usage: offline-test.sh <bundle.tar.gz>}
NAME=copydock-offline
PASSWORD='Offline-Test_pw-9$x'
FAILED=0
LOG=$(mktemp)

say()  { printf '\n\033[1m== %s\033[0m\n' "$1"; }
pass() { printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAILED=1; }
inner() { docker exec "$NAME" "$@"; }

cleanup() { docker rm -f -v "$NAME" >/dev/null 2>&1 || true; rm -f "$LOG"; }
trap cleanup EXIT
cleanup

say "Start an isolated Docker (no network)"
docker run -d --privileged --network none -e DOCKER_TLS_CERTDIR= --name "$NAME" docker:dind >/dev/null
i=0
until inner docker info >/dev/null 2>&1; do
  i=$((i + 1)); [ "$i" -le 60 ] || { echo "inner Docker did not start"; docker logs "$NAME" | tail; exit 1; }
  sleep 1
done
pass "inner Docker is running"

say "Prove it is offline"
# The kernel lists placeholder devices (gre0, tunl0...) and Docker adds docker0; none of them is a
# way out. What matters: no default route, so nothing can leave this machine.
if [ "$(inner ip route | grep -c '^default')" = 0 ]; then
  pass "no default route: traffic has nowhere to go"
else
  fail "a default route exists: $(inner ip route | grep '^default')"
fi
if inner wget -q -T 4 -O /dev/null https://registry-1.docker.io/v2/ 2>/dev/null; then
  fail "the registry is reachable"
else
  pass "the Docker registry cannot be reached"
fi
if inner docker pull -q alpine >/dev/null 2>&1; then
  fail "docker pull worked"
else
  pass "docker pull fails here"
fi
COUNT=$(inner docker image ls -q | wc -l | tr -d ' ')
[ "$COUNT" = 0 ] && pass "the inner Docker starts with 0 images" || fail "inner Docker already has $COUNT images"

say "Copy the bundle in and unpack it"
docker cp "$BUNDLE" "$NAME":/root/bundle.tar.gz
inner sh -c 'cd /root && tar xzf bundle.tar.gz && ls -d */' | sed 's/^/  /'
DIR=/root/$(basename "$BUNDLE" .tar.gz)
inner test -x "$DIR/install.sh" && pass "install.sh is executable after unpacking" || fail "install.sh is not executable"

say "install.sh --check"
inner sh -c "cd $DIR && ./install.sh --check" 2>&1 | sed 's/^/  /' || fail "--check failed"

say "install.sh (non-interactive)"
inner sh -c "cd $DIR && printf \"POSTGRES_PASSWORD='%s'\n\" '$PASSWORD' > seed.env && ./install.sh --non-interactive --env-file ./seed.env && rm seed.env" 2>&1 \
  | tee $LOG | sed 's/^/  /' || fail "install failed"

say "Checks"
grep -q "Application started" $LOG && pass "installer reported success" || fail "no success line"
if grep -qi "pulling" $LOG; then fail "installer output mentions pulling"; else pass "no pull appears in the output"; fi
STATES=$(inner docker compose -f "$DIR/compose.yaml" --project-directory "$DIR" ps --format '{{.Service}}={{.State}}' | sort | tr '\n' ' ')
echo "  services: $STATES"
[ "$STATES" = "backend=running caddy=running db=running frontend=running " ] && pass "all 4 services running" || fail "unexpected service states"
BODY=$(inner wget -q -T 5 -O - http://127.0.0.1/api/health 2>/dev/null || true)
[ "$BODY" = '{"status": "ok"}' ] && pass "GET /api/health through Caddy: $BODY" || fail "API did not answer: '$BODY'"
PAGE=$(inner wget -q -T 5 -O - http://127.0.0.1/ 2>/dev/null | grep -o '<title>.*</title>' || true)
[ -n "$PAGE" ] && pass "GET / serves the frontend: $PAGE" || fail "frontend did not answer"
[ "$(inner docker image ls -q | wc -l | tr -d ' ')" -ge 4 ] && pass "images came from the tar (now $(inner docker image ls -q | wc -l | tr -d ' ') present)" || fail "images missing"

say "Secrets"
MODE=$(inner stat -c %a "$DIR/.env")
[ "$MODE" = 600 ] && pass ".env is mode 600" || fail ".env mode is $MODE"
if grep -q 'Offline-Test_pw' $LOG; then fail "password appears in installer output"; else pass "password never appears in installer output"; fi
LEAKS=$(inner sh -c "grep -rl 'Offline-Test_pw' $DIR 2>/dev/null | grep -v '/\\.env\$'" || true)
if [ -n "$LEAKS" ]; then fail "password found outside .env: $LEAKS"; else pass "password is in no bundle file except .env"; fi
# Guard against a check that passes because grep itself failed: it must find the password in .env.
inner sh -c "grep -q 'Offline-Test_pw' $DIR/.env" && pass "(sanity) the search does find it in .env" || fail "(sanity) search could not read .env"

say "Data survives stop, start and re-install"
DB=$(inner docker compose -f "$DIR/compose.yaml" --project-directory "$DIR" ps -q db)
inner docker exec "$DB" psql -U postgres -qc "create table keep(v text); insert into keep values ('survives');"
inner sh -c "cd $DIR && docker compose down >/dev/null 2>&1"
pass "app stopped and containers removed (volumes kept)"
inner sh -c "cd $DIR && ./install.sh --non-interactive" >/dev/null 2>&1 && pass "re-install succeeded" || fail "re-install failed"
DB=$(inner docker compose -f "$DIR/compose.yaml" --project-directory "$DIR" ps -q db)
ROW=$(inner docker exec "$DB" psql -U postgres -Atc 'select v from keep;' 2>/dev/null || true)
[ "$ROW" = survives ] && pass "data still there after re-install: '$ROW'" || fail "data lost: '$ROW'"

say "Result"
if [ "$FAILED" = 0 ]; then printf '  \033[32mALL CHECKS PASSED\033[0m\n'; else printf '  \033[31mSOME CHECKS FAILED\033[0m\n'; exit 1; fi
