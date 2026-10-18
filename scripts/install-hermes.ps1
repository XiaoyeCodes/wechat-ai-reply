# Independent, pinned runtime. Does not replace an existing global Hermes installation.
param([string]$Proxy = '')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$runtimeRoot = Join-Path $projectRoot '.runtime'
$version = '0.22.2'
$source = Join-Path $runtimeRoot "hermes-agent-$version"
$toolsRoot = Join-Path $runtimeRoot 'hermes-tools'
$dataRoot = Join-Path $projectRoot '.panel-data/hermes-bootstrap'
New-Item -ItemType Directory -Path $runtimeRoot,$toolsRoot,$dataRoot -Force | Out-Null
if ($Proxy) { $env:HTTPS_PROXY=$Proxy; $env:HTTP_PROXY=$Proxy }
$env:HERMES_HOME=$dataRoot
$env:HERMES_RUNTIME_DIR=$toolsRoot
$env:PYTHONUTF8='1'
$env:UV_DEFAULT_INDEX='https://pypi.org/simple'
$env:UV_INDEX_URL='https://pypi.org/simple'
function Download-Artifact($Url,$Target) {
    $downloadArgs=@('--location','--fail','--connect-timeout','20','--max-time','600','--silent','--show-error',$Url,'--output',$Target)
    if($Proxy){$downloadArgs=@('--proxy',$Proxy)+$downloadArgs}
    & curl.exe @downloadArgs
    if($LASTEXITCODE -ne 0){throw "Download failed: $Url"}
}
if(-not (Test-Path -LiteralPath (Join-Path $source 'pm/lock.json'))){
    $archive=Join-Path $runtimeRoot "hermes-v$version.zip"
    Download-Artifact "https://codeload.github.com/NousResearch/hermes-agent/zip/refs/tags/v$version" $archive
    Expand-Archive -LiteralPath $archive -DestinationPath $runtimeRoot -Force
}
$pins=Get-Content -LiteralPath (Join-Path $source 'pm/lock.json') -Raw | ConvertFrom-Json
$architecture=[System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString().ToLowerInvariant()
$target="win32-$architecture"
$uvPin=$pins.packages.uv
$uvArtifact=$uvPin.artifacts.$target
if(-not $uvArtifact){throw "Unsupported architecture: $architecture"}
$uvFolder=Join-Path $toolsRoot "uv-$($uvPin.version)-$target"
$uv=Join-Path $uvFolder 'uv.exe'
if(-not (Test-Path -LiteralPath $uv)){
    $archive=Join-Path $runtimeRoot "uv-$($uvPin.version).zip"
    Download-Artifact $uvArtifact.url $archive
    if((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $uvArtifact.sha256){throw 'Hermes uv archive checksum mismatch'}
    New-Item -ItemType Directory -Path $uvFolder -Force | Out-Null
    Expand-Archive -LiteralPath $archive -DestinationPath $uvFolder -Force
    if(-not (Test-Path -LiteralPath $uv)){throw 'Unexpected uv archive layout'}
}
$pythonVersion=($pins.packages.python.version -split '\+')[0]
& $uv python install --no-bin --no-registry $pythonVersion
if($LASTEXITCODE -ne 0){throw 'Bootstrap Python installation failed'}
$bootstrap=(& $uv python find $pythonVersion).Trim()
$env:PYTHONPATH=$source
Push-Location -LiteralPath $source
try {
    # Runs need the runtime environment only; upstream activation also installs its test suite.
    & $bootstrap -m pm.cli install
    if($LASTEXITCODE -ne 0){throw 'Hermes runtime installation failed'}
} finally { Pop-Location }
$python=Join-Path $toolsRoot "python-$($pins.packages.python.version)-$target/python.exe"
if(-not (Test-Path -LiteralPath $python)){throw 'Pinned Hermes Python was not installed'}
$manifest=@{version=$version;source=$source;python=$python;tools=$toolsRoot;home=$dataRoot}
$manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runtimeRoot 'hermes.json') -Encoding utf8
Write-Output "Hermes $version installed. Add a managed Hermes Agent in Relay and check the engine connection."
