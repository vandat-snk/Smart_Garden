param([string]$ListenAddress = '127.0.0.1', [int]$Port = 8000)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
& .\.venv\Scripts\python.exe scripts/local_db.py start
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& .\.venv\Scripts\python.exe -m uvicorn backend.main:create_app --factory --host $ListenAddress --port $Port
