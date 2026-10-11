param(
    [int]$Port = 8765,
    [switch]$Demo,
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$panelPython = Join-Path $PSScriptRoot 'pywechat-main\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $panelPython)) {
    throw 'Missing Python environment. See README.md for installation instructions.'
}
$panelUrl = "http://127.0.0.1:$Port"
$panelExisting = $null
try { $panelExisting = Invoke-RestMethod -Uri "$panelUrl/api/status" -TimeoutSec 2 } catch {}
if ($panelExisting) {
    if ($panelExisting.app -ne 'wechat-panel') { throw "Port $Port is used by another service." }
    if ([bool]$panelExisting.demo -ne [bool]$Demo) { throw 'The running panel uses a different mode. Choose another port.' }
    Write-Output "Panel is already running: $panelUrl"
    if (-not $NoBrowser) { Start-Process $panelUrl }
    exit 0
}
$panelArgs = @('-m', 'wechat_panel', '--port', "$Port")
if ($Demo) { $panelArgs += '--demo' }
if ($NoBrowser) { $panelArgs += '--no-browser' }
Push-Location -LiteralPath $PSScriptRoot
try { & $panelPython @panelArgs }
finally { Pop-Location }
