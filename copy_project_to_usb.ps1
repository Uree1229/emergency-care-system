$ErrorActionPreference = 'Stop'
$taskSource = Join-Path $PSScriptRoot '.usb_export/Capstone-20260923'
$taskManifest = Get-Content -LiteralPath (Join-Path $taskSource 'TRANSFER_MANIFEST.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$taskDestination = 'D:\Capstone-20260923'
$taskIndex = 2
while (Test-Path -LiteralPath $taskDestination) {
    $taskDestination = 'D:\Capstone-20260923-' + $taskIndex
    $taskIndex++
}
New-Item -ItemType Directory -Path $taskDestination | Out-Null
foreach ($taskEntry in $taskManifest) {
    $taskFrom = Join-Path $taskSource $taskEntry.path
    $taskTo = Join-Path $taskDestination $taskEntry.path
    $taskParent = Split-Path -Parent $taskTo
    if (-not (Test-Path -LiteralPath $taskParent)) { New-Item -ItemType Directory -Path $taskParent | Out-Null }
    Copy-Item -LiteralPath $taskFrom -Destination $taskTo
    if ((Get-FileHash -LiteralPath $taskTo -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskEntry.sha256) {
        throw ('Hash mismatch: ' + $taskEntry.path)
    }
}
Copy-Item -LiteralPath (Join-Path $taskSource 'TRANSFER_MANIFEST.json') -Destination $taskDestination
$taskManifestHash = (Get-FileHash -LiteralPath (Join-Path $taskSource 'TRANSFER_MANIFEST.json') -Algorithm SHA256).Hash
if ((Get-FileHash -LiteralPath (Join-Path $taskDestination 'TRANSFER_MANIFEST.json') -Algorithm SHA256).Hash -ne $taskManifestHash) {
    throw 'Manifest hash mismatch'
}
[pscustomobject]@{Destination=$taskDestination;VerifiedFiles=$taskManifest.Count + 1;Bytes=($taskManifest | Measure-Object -Property bytes -Sum).Sum;SourcePreserved=$true} | ConvertTo-Json
