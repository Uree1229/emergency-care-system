[CmdletBinding()]
param([string]$PythonPath = '')
$ErrorActionPreference = 'Stop'

# Same Python rule as TEAM_START.ps1: without -PythonPath, try the launcher's 3.12 first, then the python on PATH.
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

$releaseDirectory = Split-Path -Parent $PSScriptRoot
$python = Resolve-TeamPython $PythonPath
$pythonArgs = @($python.Args)
Push-Location -LiteralPath $PSScriptRoot
try {
    & $python.Exe @pythonArgs -B -m server.team_release_20261030 verify --release $releaseDirectory
    if ($LASTEXITCODE -ne 0) { throw 'Release integrity failed.' }
} finally { Pop-Location }
