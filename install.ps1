<#
Recipe Library installer for Windows 10/11 (PowerShell).

  .\install.ps1              install into .\.venv, set up config + library, pull model
  .\install.ps1 -Service     ...and start at login (Task Scheduler, hidden window)
  .\install.ps1 -Firewall    open TCP 8000, 8631 and UDP 5353 in Windows Firewall (asks for admin)
  .\install.ps1 -NoModel     skip the Ollama model download
  .\install.ps1 -NoBrowser   skip the Chromium download

If scripts are blocked:  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
#>
param([switch]$Service, [switch]$Firewall, [switch]$NoModel, [switch]$NoBrowser)
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Here

function Say($m)  { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "!!  $m" -ForegroundColor Yellow }
function Have($c) { return [bool](Get-Command $c -ErrorAction SilentlyContinue) }

# ---------------------------------------------------------------- uv + python
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
if (-not (Have uv)) {
  Say "Installing uv (Python manager, into $env:USERPROFILE\.local\bin)"
  Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
  $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
if (-not (Have uv)) { throw "uv did not install; see https://docs.astral.sh/uv/" }

Say "Creating the Python 3.12 environment in $Here\.venv"
uv venv --python 3.12 --allow-existing .venv
$Py = "$Here\.venv\Scripts\python.exe"
$Recipes = "$Here\.venv\Scripts\recipes.exe"

Say "Installing Recipe Library and its dependencies"
uv pip install --python $Py -e ".[ocr]"

# ---------------------------------------------------------------- browser
if (-not $NoBrowser) {
  Say "Downloading Chromium for URL capture"
  & "$Here\.venv\Scripts\playwright.exe" install chromium
}

# ---------------------------------------------------------------- ollama
if (-not (Have ollama)) {
  Say "Ollama (local model runner) is not installed"
  if (Have winget) {
    $ans = Read-Host "Install it now with winget? [Y/n]"
    if ($ans -ne "n" -and $ans -ne "N") {
      winget install --id Ollama.Ollama -e --accept-package-agreements --accept-source-agreements
      $env:Path = "$env:LOCALAPPDATA\Programs\Ollama;$env:Path"
    }
  } else {
    Warn "Download it from https://ollama.com/download, run it, then: ollama pull qwen3:8b"
  }
}
if (-not $NoModel -and (Have ollama)) {
  Say "Pulling the recipe model (qwen3:8b, about 5 GB, one time)"
  try { ollama pull qwen3:8b } catch { Warn "Model pull failed; run 'ollama pull qwen3:8b' later (is the Ollama app running?)" }
}

# ---------------------------------------------------------------- config + library
Say "Writing the config file and creating $env:USERPROFILE\RecipeLibrary"
& $Recipes init
try { & $Recipes doctor } catch { }

# ---------------------------------------------------------------- firewall
if ($Firewall) {
  Say "Opening the web, printer and discovery ports in Windows Firewall (admin prompt)"
  $rules = @'
New-NetFirewallRule -DisplayName "Recipe Library web" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private | Out-Null
New-NetFirewallRule -DisplayName "Recipe Library printer" -Direction Inbound -Protocol TCP -LocalPort 8631 -Action Allow -Profile Private | Out-Null
New-NetFirewallRule -DisplayName "Recipe Library discovery" -Direction Inbound -Protocol UDP -LocalPort 5353 -Action Allow -Profile Private | Out-Null
'@
  Start-Process powershell -Verb RunAs -Wait -ArgumentList "-NoProfile -Command $rules"
} else {
  Warn "Other devices need Windows Firewall to allow TCP 8000, 8631 and UDP 5353 on the Private profile."
  Warn "Re-run with -Firewall to add the rules, and make sure your Wi-Fi network is set to 'Private'."
}

# ---------------------------------------------------------------- service (Task Scheduler)
if ($Service) {
  Say "Registering a Task Scheduler job that starts the server at login (hidden window)"
  $cmd = "Start-Process -WindowStyle Hidden -FilePath '$Recipes' -ArgumentList 'serve' -WorkingDirectory '$Here'"
  $action  = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -Command `"$cmd`""
  $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
  $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
  Unregister-ScheduledTask -TaskName "Recipe Library" -Confirm:$false -ErrorAction SilentlyContinue
  Register-ScheduledTask -TaskName "Recipe Library" -Action $action -Trigger $trigger -Settings $settings -Description "Recipe Library home server" | Out-Null
  Start-ScheduledTask -TaskName "Recipe Library"
  Write-Host "    stop:    Stop-ScheduledTask -TaskName 'Recipe Library'   (or Task Scheduler app)"
}

# ---------------------------------------------------------------- done
$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } | Select-Object -First 1).IPAddress
if (-not $ip) { $ip = "localhost" }
Say "Installed."
if ($Service) {
  Write-Host "    The server is running:  http://${ip}:8000"
} else {
  Write-Host "    Start it with:          $Recipes serve"
  Write-Host "    then open:              http://${ip}:8000"
  Write-Host "    (re-run with -Service to start it at login automatically)"
}
Write-Host "    Printer for other devices: 'Recipe Library'  (ipp://${ip}:8631/ipp/print)"
Write-Host "    Config: $(& $Py -c 'from recipelib.config import config_path; print(config_path())')"
