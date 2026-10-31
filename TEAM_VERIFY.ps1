[CmdletBinding()]
param([string]$PythonPath = 'python')
$ErrorActionPreference = 'Stop'
$releaseDirectory = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $PSScriptRoot
try {
    & $PythonPath -B -m server.team_release_20261030 verify --release $releaseDirectory
    if ($LASTEXITCODE -ne 0) { throw 'Release integrity failed.' }
} finally { Pop-Location }
