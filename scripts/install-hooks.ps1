# Run once after cloning: .\scripts\install-hooks.ps1
# Installs every git hook from .github/hooks into .git/hooks.
#   - pre-push:      runs tests + blocks direct pushes to main
#   - post-checkout: reminds you to restart the vite dev server when a
#                    branch switch changes frontend/ (stale HMR breaks the app)

$srcDir = Join-Path $PSScriptRoot "..\.github\hooks"
$dstDir = Join-Path $PSScriptRoot "..\.git\hooks"

Get-ChildItem -Path $srcDir -File | ForEach-Object {
    $dst = Join-Path $dstDir $_.Name
    Copy-Item -Path $_.FullName -Destination $dst -Force
    # Git hooks must be executable on Unix; Git for Windows handles this itself.
    if ($IsLinux -or $IsMacOS) { chmod +x $dst }
    Write-Host "installed hook: $($_.Name)"
}

Write-Host ""
Write-Host "Done. 'git push' now runs tests + blocks direct main pushes."
Write-Host "Branch switches that touch frontend/ will remind you to restart the dev server."
