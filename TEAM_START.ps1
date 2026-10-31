[CmdletBinding()]
param(
    [string]$PythonPath = 'python',
    [ValidateSet('serve','prepare','check','create-example','refresh-fixture-resources')]
    [string]$Action = 'serve',
    [ValidateSet('full','voice-only')][string]$Mode = 'full',
    [string]$Database = '',
    [ValidateRange(1,65535)][int]$Port = 18033,
    [string]$PatientId = 'TEAM-COMMON-001'
)
$ErrorActionPreference = 'Stop'
if (-not $Database) { $Database = Join-Path $PSScriptRoot 'data/team-common-20261102.db' }
$releaseDirectory = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path -LiteralPath (Join-Path $releaseDirectory 'release-manifest.json') -PathType Leaf)) {
    throw 'Run TEAM_START.ps1 inside the source folder of the verified release package.'
}
$previousPythonPath = $env:PYTHONPATH
$previousEncoding = $env:PYTHONIOENCODING
$previousBytecode = $env:PYTHONDONTWRITEBYTECODE
try {
    $env:PYTHONPATH = Join-Path $PSScriptRoot '.solver_deps'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    Push-Location -LiteralPath $PSScriptRoot
    try {
        & $PythonPath -B -m server.team_release_20261030 verify --release $releaseDirectory
        if ($LASTEXITCODE -ne 0) { throw 'Release integrity failed; server not started.' }
        & $PythonPath -B -m server.team_runtime $Action --db $Database --port $Port --mode $Mode --patient-id $PatientId
        if ($LASTEXITCODE -ne 0) { throw 'Runtime check or action failed. Existing databases were preserved.' }
    } finally { Pop-Location }
} finally {
    $env:PYTHONPATH = $previousPythonPath
    $env:PYTHONIOENCODING = $previousEncoding
    $env:PYTHONDONTWRITEBYTECODE = $previousBytecode
}
