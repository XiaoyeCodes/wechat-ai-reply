param()
$ErrorActionPreference = 'Stop'
$revision = '596fcf3d63eb4d96e067f6dc94742b5f74a230ea'
$archiveHash = '71C9041E27FD137DA15F5BDBAF2449C4E481C44C051AB7785A98DAA513FF3212'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtime = Join-Path $projectRoot '.runtime\vibe-trading'
$source = Join-Path $runtime 'source'
$sourceRevision = Join-Path $source '.relay-revision'
$python = Join-Path $runtime 'venv\Scripts\python.exe'
$manifest = Join-Path $projectRoot '.runtime\vibe-trading.json'
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw 'Install uv first: https://docs.astral.sh/uv/getting-started/installation/' }
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
if (Test-Path -LiteralPath $source) {
    if (-not (Test-Path -LiteralPath $sourceRevision) -or (Get-Content -LiteralPath $sourceRevision -Raw).Trim() -ne $revision) {
        throw 'Existing source revision is unknown or different. Rename .runtime/vibe-trading/source to preserve it, then rerun this installer.'
    }
} else {
    $archive = Join-Path $runtime 'upstream.zip'
    Invoke-WebRequest -Uri "https://codeload.github.com/HKUDS/Vibe-Trading/zip/$revision" -OutFile $archive -TimeoutSec 180
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $archiveHash) { throw 'Upstream archive checksum mismatch.' }
    $staging = Join-Path $runtime 'download'
    Expand-Archive -LiteralPath $archive -DestinationPath $staging -Force
    Copy-Item -LiteralPath (Join-Path $staging "Vibe-Trading-$revision") -Destination $source -Recurse
    Set-Content -LiteralPath $sourceRevision -Value $revision -Encoding utf8
}
if (-not (Test-Path -LiteralPath $python)) {
    & uv venv --python 3.12 (Join-Path $runtime 'venv')
    if ($LASTEXITCODE -ne 0) { throw 'Vibe-Trading environment creation failed.' }
}
& uv pip install --python $python --require-hashes -r (Join-Path $source 'desktop\electron\requirements-windows-lock.txt')
if ($LASTEXITCODE -ne 0) { throw 'Vibe-Trading dependency installation failed.' }
# Source is imported explicitly by the worker; no editable installation or shared Python packages.
@{revision=$revision;source=$source;python=$python;archive_sha256=$archiveHash} | ConvertTo-Json | Set-Content -LiteralPath $manifest -Encoding utf8
Write-Output "Vibe-Trading installed: $revision. Select the Vibe-Trading agent source in Relay and check the engine."
