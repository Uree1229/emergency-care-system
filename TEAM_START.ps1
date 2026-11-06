[CmdletBinding()]
param(
    [string]$PythonPath = '',
    [ValidateSet('serve','prepare','check','create-example','refresh-fixture-resources')]
    [string]$Action = 'serve',
    [ValidateSet('full','voice-only')][string]$Mode = 'full',
    [string]$Database = '',
    [ValidateRange(1,65535)][int]$Port = 18033,
    [string]$PatientId = 'TEAM-COMMON-001'
)
$ErrorActionPreference = 'Stop'

# .solver_deps holds CPython 3.12 (cp312, 64-bit Windows) packages, so full mode needs Python 3.12.
# Without -PythonPath, try the launcher's 3.12 first, then the python on PATH. TEAM_VERIFY.ps1 uses the same rule.
function Get-TeamPythonVersion($Candidate) {
    try {
        $prefix = @($Candidate.Args)
        $out = & $Candidate.Exe @prefix -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { return [version]([string]($out | Select-Object -Last 1)).Trim() }
    } catch {}
    return $null
}
function Resolve-TeamPython([string]$Requested) {
    if ($Requested) { $candidates = @(@{ Exe = $Requested; Args = @() }) }
    else { $candidates = @(@{ Exe = 'py'; Args = @('-3.12') }, @{ Exe = 'python'; Args = @() }) }
    $newer = $null; $newerVersion = $null; $found = @()
    foreach ($candidate in $candidates) {
        $version = Get-TeamPythonVersion $candidate
        if (-not $version) { continue }
        $found += ('{0} {1} = {2}' -f $candidate.Exe, ($candidate.Args -join ' '), $version).Replace('  ', ' ')
        if ($version.Major -eq 3 -and $version.Minor -eq 12) { return $candidate }
        if ($version -gt [version]'3.12' -and -not $newer) { $newer = $candidate; $newerVersion = $version }
    }
    if ($newer) {
        Write-Warning ('Using Python {0} ({1}). .solver_deps holds CPython 3.12 packages, so full mode may fail. Install Python 3.12 or pass -PythonPath.' -f $newerVersion, $newer.Exe)
        return $newer
    }
    if ($found) { throw ('Python 3.12 is required. Found: {0}. Install Python 3.12 or pass -PythonPath.' -f ($found -join '; ')) }
    throw 'Python was not found. Install Python 3.12 or pass -PythonPath.'
}

if (-not $Database) { $Database = Join-Path $PSScriptRoot 'data/team-common-20261102.db' }
$releaseDirectory = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path -LiteralPath (Join-Path $releaseDirectory 'release-manifest.json') -PathType Leaf)) {
    throw 'Run TEAM_START.ps1 inside the source folder of the verified release package.'
}
$python = Resolve-TeamPython $PythonPath
$pythonArgs = @($python.Args)
$previousPythonPath = $env:PYTHONPATH
$previousEncoding = $env:PYTHONIOENCODING
$previousBytecode = $env:PYTHONDONTWRITEBYTECODE
try {
    $env:PYTHONPATH = Join-Path $PSScriptRoot '.solver_deps'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    Push-Location -LiteralPath $PSScriptRoot
    try {
        & $python.Exe @pythonArgs -B -m server.team_release_20261030 verify --release $releaseDirectory
        if ($LASTEXITCODE -ne 0) { throw 'Release integrity failed; server not started.' }
        & $python.Exe @pythonArgs -B -m server.team_runtime $Action --db $Database --port $Port --mode $Mode --patient-id $PatientId
        if ($LASTEXITCODE -ne 0) { throw 'Runtime check or action failed. Existing databases were preserved.' }
    } finally { Pop-Location }
} finally {
    $env:PYTHONPATH = $previousPythonPath
    $env:PYTHONIOENCODING = $previousEncoding
    $env:PYTHONDONTWRITEBYTECODE = $previousBytecode
}
