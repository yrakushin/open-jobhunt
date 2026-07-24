# Open JobHunt — установка на Windows (PowerShell)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -e .
& .\.venv\Scripts\jobhunt.exe init
& .\.venv\Scripts\jobhunt.exe setup

Write-Host ""
Write-Host "Готово. Дальше:" -ForegroundColor Green
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  jobhunt login"
Write-Host "  jobhunt ui"
Write-Host ""
Write-Host "Если окно Chromium не открывается: jobhunt ui --browser" -ForegroundColor Cyan
