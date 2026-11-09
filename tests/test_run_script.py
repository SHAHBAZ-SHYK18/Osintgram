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


# ---- Windows launcher ------------------------------------------------------

PS1 = Path(__file__).resolve().parent.parent / "run.ps1"


def test_the_windows_launcher_exists():
    assert PS1.is_file()
    body = PS1.read_text()
    assert "param(" in body
    assert "uvicorn" in body


def test_the_windows_launcher_offers_the_same_options_as_run_sh():
    body = PS1.read_text()
    for option in ("$Port", "$Reload", "$Check", "$NoInstall", "$BindHost"):
        assert option in body, f"{option} missing from run.ps1"


def test_the_windows_launcher_defaults_to_localhost_and_warns_otherwise():
    body = PS1.read_text()
    assert '$BindHost = "127.0.0.1"' in body
    assert "no authentication" in body.lower()


def test_the_windows_launcher_prefers_a_python_with_wheels():
    """PyYAML has no 3.15 wheel; picking the newest Python would send pip off
    to compile it and demand Visual C++ Build Tools."""
    body = PS1.read_text()
    assert '"3.13", "3.12", "3.11", "3.10"' in body or "3.12" in body
    assert "Visual C++" in body


def test_both_launchers_use_the_venv_interpreter_not_a_bare_command():
    """`uvicorn` on PATH is the classic Windows failure: the venv isn't active.
    Both scripts must invoke it through the venv's own python."""
    assert '-m", "uvicorn"' in PS1.read_text() or '"-m", "uvicorn"' in PS1.read_text()
    sh = SCRIPT.read_text()
    assert '"$VPY" -m uvicorn' in sh


# ---- requirements must stay installable as Python moves on -----------------

REQUIREMENTS = Path(__file__).resolve().parent.parent / "requirements.txt"


def test_optional_uvicorn_extras_are_gated_on_the_python_version():
    """uvicorn[standard] pulls PyYAML, which has no wheel for 3.15+ - an
    unguarded extra turns a routine install into a compiler error."""
    body = REQUIREMENTS.read_text()
    assert re.search(r'uvicorn\[standard\].*python_version\s*<\s*"3\.15"', body)
    assert re.search(r'(?m)^uvicorn>=[\d.]+;\s*python_version\s*>=\s*"3\.15"', body)


def test_the_tools_that_fail_without_a_compiler_are_truly_optional():
    """If the app ever imports pyyaml itself, the gate above stops being safe."""
    root = Path(__file__).resolve().parent.parent
    imports = [
        line for path in (root / "src").rglob("*.py")
        for line in path.read_text().splitlines()
        if re.match(r"\s*(import yaml|from yaml)", line)
    ]
    assert imports == [], "src/ now imports yaml - it is no longer an optional extra"
