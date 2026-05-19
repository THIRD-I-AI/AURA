# Run once after cloning: .\scripts\install-hooks.ps1
# Installs the pre-push hook so tests run before every push.

$hookSrc = Join-Path $PSScriptRoot "..\\.github\\hooks\\pre-push"
$hookDst = Join-Path $PSScriptRoot "..\\.git\\hooks\\pre-push"

Copy-Item -Path $hookSrc -Destination $hookDst -Force

# Git hooks must be executable on Unix; on Windows this is handled by Git for Windows.
if ($IsLinux -or $IsMacOS) {
    chmod +x $hookDst
}

Write-Host "pre-push hook installed at: $hookDst"
Write-Host "From now on, 'git push' will run tests and block direct pushes to main."
