param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$panelUrl = "http://127.0.0.1:$Port"
$panelStatus = Invoke-RestMethod -Uri "$panelUrl/api/status" -TimeoutSec 2
if ($panelStatus.app -ne 'wechat-panel') { throw 'This port is not a WeChat panel.' }
Invoke-RestMethod -Uri "$panelUrl/api/shutdown" -Method Post -Headers @{'X-Panel-Request'='1'} | Out-Null
Write-Output 'The panel is shutting down. Listening will stop; the WeChat app remains open.'
