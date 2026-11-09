# Start Osintgram on Windows: one command, from a fresh folder to a running page.
#
#   .\run.ps1                  # http://127.0.0.1:8000
#   .\run.ps1 -Port 9000       # somewhere else
#   .\run.ps1 -Reload          # development: restart on code changes
#   .\run.ps1 -Check           # set up and verify, then exit
#   .\run.ps1 -NoInstall       # skip the dependency step
#
# If PowerShell refuses to run it ("running scripts is disabled"), either use
#   powershell -ExecutionPolicy Bypass -File .\run.ps1
# or allow local scripts once:
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
#
# The HikerAPI key is NOT asked for here: the page has a key panel that
# verifies it before accepting it. You can also set HIKERAPI_TOKEN first,
# which wins over anything typed in the page.
[CmdletBinding()]
param(
    [int]$Port = 8000,
    [string]$BindHost = "127.0.0.1",
    [switch]$Reload,
    [switch]$Check,
    [switch]$NoInstall
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Say  ($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Warn ($m) { Write-Host "[!] $m" -ForegroundColor Yellow }
function Die  ($m) { Write-Host "[x] $m" -ForegroundColor Red; exit 1 }

# ---- 1. A Python that has wheels for everything -----------------------------
# 3.10 is the floor, but newest is not best: PyYAML has no wheel for 3.15 yet,
# so pip falls back to compiling it and demands Visual C++ Build Tools.
$known = @("3.13", "3.12", "3.11", "3.10", "3.14")
$python = $null
$versions = @()
foreach ($v in $known) {
    try {
        $out = & py "-$v" -c "import sys; print(sys.version)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { $versions += $v }
    } catch { }
}
if ($versions.Count -eq 0) {
    # No launcher-matched versions: fall back to whatever "python" is, if new enough.
    try {
        $out = & python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { $versions += "default" }
    } catch { }
}
if ($versions.Count -eq 0) {
    Die "Python 3.10+ not found. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then run this again."
}
$chosen = $versions[0]
if ($chosen -eq "default") { $pyCmd = @("python") } else { $pyCmd = @("py", "-$chosen") }

$ver = & $pyCmd[0] $pyCmd[1..($pyCmd.Length-1)] -c "import platform; print(platform.python_version())"
Say "Using Python $ver"

if ([version]$ver -ge [version]"3.15") {
    Warn "Python $ver is very new and PyYAML has no prebuilt wheel for it yet."
    Warn "The install will try to compile it and need Microsoft Visual C++ Build Tools."
    Warn "If this fails, install Python 3.12 or 3.13 and run this again."
}

# ---- 2. Virtual environment -------------------------------------------------
$venv = ".venv"
$vpy  = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $vpy)) {
    Say "Creating the virtual environment in .venv\"
    & $pyCmd[0] $pyCmd[1..($pyCmd.Length-1)] -m venv $venv
    if ($LASTEXITCODE -ne 0) { Die "Could not create the virtual environment." }
}

# ---- 3. Dependencies --------------------------------------------------------
$req   = "requirements.txt"
$stamp = Join-Path $venv ".requirements.sha256"
$want  = (Get-FileHash $req -Algorithm SHA256).Hash

if (-not $NoInstall) {
    $have = if (Test-Path $stamp) { (Get-Content $stamp -Raw).Trim() } else { "" }
    if ($have -ne $want) {
        Say "Installing dependencies (one time, a few minutes)"
        & $vpy -m pip install --upgrade pip --quiet
        & $vpy -m pip install -r $req
        if ($LASTEXITCODE -ne 0) {
            Die "Dependency install failed. If you saw 'Microsoft Visual C++ 14.0 is required', this is the Python-version problem above: install Python 3.12 or 3.13 and run again."
        }
        Set-Content -Path $stamp -Value $want -NoNewline
    } else {
        Say "Dependencies already installed"
    }
} else {
    Say "Skipping install (-NoInstall)"
}

Say "Checking imports"
& $vpy -c "import importlib.util as u, sys; m=[p for p in ('fastapi','uvicorn','hikerapi','geopy') if not u.find_spec(p)]; print('missing: '+', '.join(m), file=sys.stderr) if m else None; sys.exit(1 if m else 0)"
if ($LASTEXITCODE -ne 0) { Die "Dependencies are incomplete. Run again without -NoInstall." }

# ---- 4. Config and data directories ----------------------------------------
$example = Join-Path "config" "credentials.ini.example"
$creds   = Join-Path "config" "credentials.ini"
if ((Test-Path $example) -and -not (Test-Path $creds)) {
    Copy-Item $example $creds
    Say "Created config\credentials.ini (empty - the page asks for the key)"
}
foreach ($d in @("cache", "dossier")) { if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d | Out-Null } }

if ($Check) { Say "Environment is ready. Start it with: .\run.ps1"; exit 0 }

# ---- 5. Start ---------------------------------------------------------------
$urlHost = if ($BindHost -eq "0.0.0.0") { "127.0.0.1" } else { $BindHost }
Write-Host ""
if ($BindHost -eq "0.0.0.0") {
    Warn "Listening on 0.0.0.0 - reachable from your network."
    Warn "This app has NO authentication and collects other people's data."
    Warn "Anyone who can reach this port can spend your HikerAPI credit."
}
Say "Osintgram -> http://${urlHost}:$Port"
Say "Press Ctrl-C to stop."
Write-Host ""

$uvArgs = @("-m", "uvicorn", "src.web.app:app", "--host", $BindHost, "--port", "$Port")
if ($Reload) { $uvArgs += "--reload" }
& $vpy @uvArgs
