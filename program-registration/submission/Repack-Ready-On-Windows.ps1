param([string]$SourceZip = "$PSScriptRoot\EdgeFlow_ready_source.zip")
$ErrorActionPreference = 'Stop'
$stage = Join-Path $env:TEMP ("EdgeFlow-" + [guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $stage | Out-Null
Expand-Archive -LiteralPath $SourceZip -DestinationPath $stage
$destination = Join-Path $PSScriptRoot 'EdgeFlow_ready_source_windows.zip'
if (Test-Path $destination) { throw "Output already exists: $destination" }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($stage, $destination)
Remove-Item -LiteralPath $stage -Recurse
Write-Output ("SIZE_BYTES=" + (Get-Item -LiteralPath $destination).Length)
