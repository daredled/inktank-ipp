# Install (or update) inktank as a Windows service. Run from an elevated
# PowerShell or cmd:
#
#   powershell -ExecutionPolicy Bypass -File install-service.ps1 [-Port 8631]
#   powershell -ExecutionPolicy Bypass -File install-service.ps1 -Uninstall
#
# Layout under C:\Program Files\inktank (writable by administrators only, since
# the service runs as LocalSystem):
#   app\      copy of the project (pyproject.toml, uv.lock, inktank\)
#   python\   uv-managed Python used by the service
#   venv\     virtual environment with the dependencies
#   inktank-service.exe / .xml   WinSW wrapper and its configuration
#   logs\     service output
#
# Rerun the script to deploy a new version of the code.
param(
    [int]$Port = 8631,
    [switch]$Uninstall,
    [string]$InstallDir = "$env:ProgramFiles\inktank"
)
$ErrorActionPreference = "Stop"

$ServiceId = "inktank"
$WinSWUrl = "https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe"
$WinSWSha256 = "05B82D46AD331CC16BDC00DE5C6332C1EF818DF8CEEFCD49C726553209B3A0DA"
$RepoDir = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Wrapper = Join-Path $InstallDir "inktank-service.exe"

$identity = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "run this script as administrator"
}

function Remove-InktankService {
    if (Get-Service $ServiceId -ErrorAction SilentlyContinue) {
        Write-Output "stopping and removing the existing service"
        & $Wrapper stop | Out-Null
        & $Wrapper uninstall | Out-Null
    }
}

if ($Uninstall) {
    Remove-InktankService
    Write-Output "service removed; files remain in $InstallDir"
    return
}

$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) { $uv = Join-Path $env:USERPROFILE ".local\bin\uv.exe" }
if (-not (Test-Path $uv)) { throw "uv not found; install it with: winget install astral-sh.uv" }

New-Item -ItemType Directory -Force -Path $InstallDir, "$InstallDir\logs" | Out-Null
if (Test-Path $Wrapper) { Remove-InktankService }

# 1. Copy the code (only what the service needs).
Write-Output "copying code from $RepoDir"
$app = Join-Path $InstallDir "app"
if (Test-Path $app) { Remove-Item -Recurse -Force $app }
New-Item -ItemType Directory -Path $app | Out-Null
Copy-Item (Join-Path $RepoDir "pyproject.toml"), (Join-Path $RepoDir "uv.lock"),
          (Join-Path $RepoDir ".python-version"), (Join-Path $RepoDir "README.md"),
          (Join-Path $RepoDir "LICENSE"), (Join-Path $RepoDir "THIRD_PARTY_NOTICES.md") $app
Copy-Item -Recurse (Join-Path $RepoDir "inktank") $app
Get-ChildItem -Recurse -Directory -Filter __pycache__ $app | Remove-Item -Recurse -Force

# 2. Python and dependencies, all inside InstallDir.
Write-Output "creating the virtual environment"
$env:UV_PYTHON_INSTALL_DIR = Join-Path $InstallDir "python"
$env:UV_PROJECT_ENVIRONMENT = Join-Path $InstallDir "venv"
$env:UV_CACHE_DIR = Join-Path $env:TEMP "inktank-uv-cache"
Push-Location $app
try {
    # uv writes progress to stderr; Windows PowerShell 5.1 would turn that
    # into terminating errors under "Stop".
    $ErrorActionPreference = "Continue"
    # --reinstall-package: the version stays 0.1.0, so uv would keep the old copy.
    & $uv sync --frozen --no-dev --no-editable --managed-python --reinstall-package inktank
    if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }
} finally {
    $ErrorActionPreference = "Stop"
    Pop-Location
}
Remove-Item -Recurse -Force $env:UV_CACHE_DIR -ErrorAction SilentlyContinue

# 3. WinSW, pinned by hash.
if (-not (Test-Path $Wrapper) -or (Get-FileHash $Wrapper).Hash -ne $WinSWSha256) {
    Write-Output "downloading WinSW"
    Invoke-WebRequest -UseBasicParsing $WinSWUrl -OutFile "$Wrapper.download"
    if ((Get-FileHash "$Wrapper.download").Hash -ne $WinSWSha256) {
        Remove-Item "$Wrapper.download"
        throw "WinSW hash mismatch"
    }
    Move-Item -Force "$Wrapper.download" $Wrapper
}

# 4. Service configuration.
$exe = Join-Path $InstallDir "venv\Scripts\inktank.exe"
@"
<service>
  <id>$ServiceId</id>
  <name>inktank IPP print server</name>
  <description>Shares the HP Ink Tank 315 on the network over IPP Everywhere and eSCL (inktank).</description>
  <executable>$exe</executable>
  <arguments>serve --port $Port</arguments>
  <workingdirectory>$InstallDir</workingdirectory>
  <env name="PYTHONUNBUFFERED" value="1"/>
  <env name="PYTHONIOENCODING" value="utf-8"/>
  <startmode>Automatic</startmode>
  <delayedAutoStart>true</delayedAutoStart>
  <onfailure action="restart" delay="10 sec"/>
  <resetfailure>1 hour</resetfailure>
  <stoptimeout>15 sec</stoptimeout>
  <logpath>$InstallDir\logs</logpath>
  <log mode="roll-by-size">
    <sizeThreshold>10240</sizeThreshold>
    <keepFiles>5</keepFiles>
  </log>
</service>
"@ | Set-Content -Encoding UTF8 (Join-Path $InstallDir "inktank-service.xml")

# 5. Install and start.
$ErrorActionPreference = "Continue"
& $Wrapper install
& $Wrapper start
$ErrorActionPreference = "Stop"
Start-Sleep 5
Get-Service $ServiceId | Format-List Name, DisplayName, Status, StartType
Write-Output "logs: $InstallDir\logs"
