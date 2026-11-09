"""Guards run.sh - the first thing a new user runs.

Deliberately shallow: it syntax-checks the script, checks every documented
option is actually handled, and that --help runs without a Python install.
Nothing here starts a server or touches the network.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "run.sh"

# Options the help text promises. Kept explicit rather than parsed out of the
# help, so removing one from the script fails here instead of quietly
# disappearing from the docs.
DOCUMENTED_OPTIONS = ["--port", "--host", "--reload", "--lan", "--no-install", "--check", "--help"]


def test_the_script_exists_and_is_executable():
    assert SCRIPT.is_file()
    assert SCRIPT.stat().st_mode & 0o111, "run.sh must be executable in git"


def test_it_is_valid_bash():
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not available")
    result = subprocess.run([bash, "-n", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("option", DOCUMENTED_OPTIONS)
def test_every_documented_option_is_handled(option):
    body = SCRIPT.read_text()
    # Each option appears as a case arm, e.g. "--port)" or "--port=*)"
    assert re.search(rf"(?m)^\s+{re.escape(option)}[)|]|{re.escape(option)}=?\*\)", body) or \
           re.search(rf"{re.escape(option)}\)", body), f"{option} is documented but not handled"


def test_it_starts_from_its_own_directory():
    """Otherwise ./run.sh from elsewhere puts .venv, cache/ and dossier/ in
    whatever the caller's cwd happened to be."""
    assert 'cd "$(dirname "$0")"' in SCRIPT.read_text()


def test_it_does_not_disable_the_localhost_default():
    """--lan must be an explicit choice, and must carry a warning."""
    body = SCRIPT.read_text()
    assert "HOST=127.0.0.1" in body, "the default bind must stay localhost"
    assert "no authentication" in body.lower(), "--lan must warn about missing auth"


def test_it_never_takes_a_key_on_the_command_line():
    """A key in argv lands in shell history and in `ps` output. The page's
    panel verifies the key instead; the env var is the scriptable route."""
    body = SCRIPT.read_text()
    assert "--token" not in body
    assert "HIKERAPI_TOKEN" in body  # documented as the alternative


def test_help_works_without_starting_anything():
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not available")
    result = subprocess.run([bash, str(SCRIPT), "--help"], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    assert "./run.sh" in result.stdout
    assert "--check" in result.stdout
