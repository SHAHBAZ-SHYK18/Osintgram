#!/usr/bin/env bash
# Start Osintgram on this machine: one command, from a fresh clone to a
# running page. Safe to run again - it reuses the environment it made the
# first time, and only reinstalls when requirements.txt has changed.
#
#   ./run.sh                 # http://127.0.0.1:8000
#   ./run.sh --port 9000     # somewhere else
#   ./run.sh --reload        # development: restart on code changes
#   ./run.sh --lan           # reachable from your network - read the warning
#   ./run.sh --no-install    # just start it (offline, or already set up)
#   ./run.sh --check         # install/verify only, then exit
#
# The key is NOT asked for here on purpose: the page has a key panel that
# verifies it against HikerAPI before accepting it. You can also export
# HIKERAPI_TOKEN, which wins over anything typed in the page.
set -euo pipefail

cd "$(dirname "$0")"

HOST=127.0.0.1
PORT=8000
RELOAD=""
DO_INSTALL=1
CHECK_ONLY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --port)      PORT="${2:?--port needs a number}"; shift 2 ;;
    --port=*)    PORT="${1#*=}"; shift ;;
    --host)      HOST="${2:?--host needs an address}"; shift 2 ;;
    --host=*)    HOST="${1#*=}"; shift ;;
    --reload)    RELOAD="--reload"; shift ;;
    --lan)       HOST=0.0.0.0; LAN=1; shift ;;
    --no-install) DO_INSTALL=0; shift ;;
    --check)     CHECK_ONLY=1; shift ;;
    -h|--help)   awk 'NR>1 && /^set -euo/{exit} NR>1{sub(/^# ?/,""); print}' "$0"; exit 0 ;;
    *)           echo "Unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
done

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

# ---- 1. A Python that has wheels for everything ----------------------------
# 3.10 is the floor, but newer is not automatically better: prebuilt wheels
# lag behind brand-new CPython releases, and without one pip tries to compile
# from source - which needs a C compiler. On Python 3.15 there is no PyYAML
# wheel yet, so the install dies with "Microsoft Visual C++ 14.0 is required"
# (see doc/web-ui.md, Troubleshooting). Prefer a version known to be covered,
# and say so plainly rather than failing later.
PY=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3.14 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$candidate"; break
  fi
done
[ -n "$PY" ] || die "Python 3.10+ not found. Install it from python.org and run again."
say "Using $($PY --version) ($(command -v "$PY"))"

if "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 15) else 1)' 2>/dev/null; then
  warn "Python $($PY -c 'import platform; print(platform.python_version())') is very new and some"
  warn "dependencies have no prebuilt wheel for it yet - pip may try to compile them,"
  warn "which needs Microsoft Visual C++ Build Tools. If the install fails, use 3.12/3.13."
fi

# 3.15+ needs the interpreter's own version of "python -m venv" too, so the
# venv is created from $PY either way; nothing else to branch on here.

# ---- 2. Virtual environment ------------------------------------------------
VENV=.venv
if [ ! -x "$VENV/bin/python" ]; then
  say "Creating the virtual environment in $VENV/"
  "$PY" -m venv "$VENV" || die "Could not create a virtual environment. On Debian/Ubuntu: apt install python3-venv"
fi
VPY="$VENV/bin/python"

# A venv made elsewhere (moved folder, different machine) has a broken
# interpreter symlink - rebuild it rather than failing with a confusing error.
if ! "$VPY" -c 'import sys' >/dev/null 2>&1; then
  warn "The existing $VENV looks broken (moved folder?) - recreating it."
  rm -rf "$VENV"
  "$PY" -m venv "$VENV" || die "Could not recreate the virtual environment."
  VPY="$VENV/bin/python"
fi

# ---- 3. Dependencies -------------------------------------------------------
# Installed once; reinstalled when requirements.txt changes. The stamp holds
# the file's checksum, so edits and `git pull` are both noticed.
STAMP="$VENV/.requirements.sha256"
WANT="$(cksum < requirements.txt | awk '{print $1"-"$2}')"

if [ "$DO_INSTALL" = 1 ]; then
  if [ ! -f "$STAMP" ] || [ "$(cat "$STAMP")" != "$WANT" ]; then
    say "Installing dependencies (one time, a few minutes)"
    "$VPY" -m pip install --upgrade pip --quiet || warn "Could not upgrade pip; continuing."
    if ! "$VPY" -m pip install -r requirements.txt; then
      die "Dependency install failed. Check your network (or proxy), then run again."
    fi
    printf '%s' "$WANT" > "$STAMP"
  else
    say "Dependencies already installed"
  fi
else
  say "Skipping install (--no-install)"
fi

# Verify rather than assume: a half-finished install is what produces the
# "Cannot reach HikerAPI" / import errors that look like something else.
say "Checking imports"
"$VPY" - <<'PY' || die "Dependencies are incomplete. Re-run without --no-install."
import importlib.util, sys
missing = [m for m in ("fastapi", "uvicorn", "hikerapi", "geopy") if not importlib.util.find_spec(m)]
if missing:
    print("missing:", ", ".join(missing), file=sys.stderr)
    raise SystemExit(1)
PY

# ---- 4. Config and data directories ---------------------------------------
# config/credentials.ini is git-ignored and holds the HikerAPI key; create it
# from the template so the file exists (empty) rather than 404-ing the reader.
if [ ! -f config/credentials.ini ] && [ -f config/credentials.ini.example ]; then
  cp config/credentials.ini.example config/credentials.ini
  chmod 600 config/credentials.ini
  say "Created config/credentials.ini (empty - the page asks for the key)"
fi
mkdir -p cache dossier

if [ "$CHECK_ONLY" = 1 ]; then
  say "Environment is ready. Start it with: ./run.sh"
  exit 0
fi

# ---- 5. Start ---------------------------------------------------------------
URL_HOST="$HOST"
[ "$HOST" = "0.0.0.0" ] && URL_HOST=127.0.0.1

echo
if [ -n "${LAN:-}" ]; then
  warn "Listening on 0.0.0.0:$PORT - reachable from your network."
  warn "This app has NO authentication and collects other people's data."
  warn "Anyone who can reach this port can spend your HikerAPI credit."
  warn "Use it on a trusted network only, and stop it when you're done."
fi
say "Osintgram → http://$URL_HOST:$PORT"
say "Press Ctrl-C to stop."
echo

exec "$VPY" -m uvicorn src.web.app:app --host "$HOST" --port "$PORT" $RELOAD
