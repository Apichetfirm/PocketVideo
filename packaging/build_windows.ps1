# PowerShell Reproducible Build & Packaging Script for Windows 10/11 x64
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "=== [1/6] Fetching & Verifying Redistributable FFmpeg/FFprobe (SHA-256) ==="
python packaging/fetch_ffmpeg_windows.py

Write-Host "=== [2/6] Running Automated Unit & Integration Test Suite ==="
$env:PYTHONPATH = "$RepoRoot\src"
python -m unittest discover -s tests -v

Write-Host "=== [3/6] Building Standalone Windows Application with PyInstaller ==="
pyinstaller --noconfirm --clean packaging/pocketvideo.spec

Write-Host "=== [4/6] Running Post-Build Executable Verification ==="
$VersionRun = Start-Process -FilePath "$RepoRoot\dist\PocketVideo\PocketVideo.exe" -ArgumentList '--version' -Wait -PassThru
if ($VersionRun.ExitCode -ne 0) { throw "Built PocketVideo.exe failed its version smoke test: $($VersionRun.ExitCode)" }

Write-Host "=== [5/6] Creating Portable ZIP Distribution ==="
New-Item -ItemType Directory -Force -Path "$RepoRoot\dist\release" | Out-Null
$PortableZip = "$RepoRoot\dist\release\PocketVideo-1.0.0-win64-portable.zip"
if (Test-Path $PortableZip) { Remove-Item $PortableZip }
Compress-Archive -Path "$RepoRoot\dist\PocketVideo\*" -DestinationPath $PortableZip

Write-Host "=== [6/6] Compiling Inno Setup Installer & Generating SHA-256 Checksums ==="
$IsccCommand = Get-Command iscc -ErrorAction SilentlyContinue
$IsccPath = if ($IsccCommand) { $IsccCommand.Source } else { $null }
if (-not $IsccPath) {
    $IsccCandidate = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
    if (Test-Path $IsccCandidate) { $IsccPath = $IsccCandidate }
}
if (-not $IsccPath) {
    throw "Inno Setup 6 compiler not found. Install Inno Setup and rerun the build; installer creation is required."
}
& $IsccPath "$RepoRoot\packaging\installer.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed to compile the installer." }
$Installer = "$RepoRoot\dist\installer\PocketVideo-Setup-1.0.0-win64.exe"
if (-not (Test-Path $Installer)) { throw "Installer executable was not produced: $Installer" }
Copy-Item $Installer "$RepoRoot\dist\release\" -Force

foreach ($Package in @($PortableZip, "$RepoRoot\dist\release\PocketVideo-Setup-1.0.0-win64.exe")) {
    if (-not (Test-Path $Package)) { throw "Required release package not found: $Package" }
    if ((Get-Item $Package).Length -le 0) { throw "Release package is empty: $Package" }
}

$ChecksumFile = "$RepoRoot\dist\release\SHA256SUMS.txt"
Get-ChildItem "$RepoRoot\dist\release\*" -Exclude "SHA256SUMS.txt" | ForEach-Object {
    $Hash = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower()
    "$Hash  $($_.Name)"
} | Set-Content -Path $ChecksumFile -Encoding UTF8

Write-Host "=== Build Complete! Artifacts in dist\release\ ==="
Get-Content $ChecksumFile
