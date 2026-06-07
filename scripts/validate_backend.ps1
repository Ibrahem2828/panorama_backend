[CmdletBinding()]
param(
    [switch]$DeployCheck
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Python = if (Test-Path $VenvPython) { $VenvPython } else { "python" }
$PreviousPythonPath = [Environment]::GetEnvironmentVariable("PYTHONPATH", "Process")

function Invoke-ValidationStep {
    param(
        [string]$Name,
        [scriptblock]$Command
    )

    Write-Host ""
    Write-Host "== $Name =="
    & $Command
}

function Set-ValidationDefault {
    param(
        [string]$Name,
        [string]$Value,
        [hashtable]$PreviousValues
    )

    $PreviousValues[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($PreviousValues[$Name])) {
        [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
    }
}

try {
    Set-Location $RepoRoot
    [Environment]::SetEnvironmentVariable("PYTHONPATH", (Join-Path $RepoRoot "app"), "Process")

    Invoke-ValidationStep "Django system check" {
        & $Python "app\manage.py" check
    }

    Invoke-ValidationStep "Django migration check" {
        & $Python "app\manage.py" makemigrations --check --dry-run
    }

    if ($DeployCheck) {
        $deployEnv = @{}
        Set-ValidationDefault "SECRET_KEY" "validation-only-secret-key-not-for-runtime-please-replace-1234567890" $deployEnv
        Set-ValidationDefault "DEBUG" "False" $deployEnv
        Set-ValidationDefault "ALLOWED_HOSTS" "api.example.com" $deployEnv
        Set-ValidationDefault "CSRF_TRUSTED_ORIGINS" "https://api.example.com,https://dashboard.example.com" $deployEnv
        Set-ValidationDefault "CORS_ALLOWED_ORIGINS" "https://dashboard.example.com" $deployEnv
        Set-ValidationDefault "DATABASE_URL" "postgres://user:pass@localhost:5432/panorama" $deployEnv
        Set-ValidationDefault "REDIS_URL" "redis://localhost:6379/0" $deployEnv
        Set-ValidationDefault "SECURE_SSL_REDIRECT" "True" $deployEnv
        Set-ValidationDefault "SESSION_COOKIE_SECURE" "True" $deployEnv
        Set-ValidationDefault "CSRF_COOKIE_SECURE" "True" $deployEnv
        Set-ValidationDefault "SECURE_HSTS_SECONDS" "31536000" $deployEnv
        Set-ValidationDefault "SECURE_HSTS_INCLUDE_SUBDOMAINS" "True" $deployEnv
        Set-ValidationDefault "SECURE_HSTS_PRELOAD" "False" $deployEnv

        Invoke-ValidationStep "Django deploy check" {
            & $Python "app\manage.py" check --deploy --settings config.settings.production
        }
    }

    Invoke-ValidationStep "API collection JSON validation" {
        & $Python -c "import json, pathlib; [json.loads(pathlib.Path(p).read_text(encoding='utf-8')) for p in ('docs/api/mobile_api_collection.json', 'docs/api/dashboard_api_collection.json')]; print('API collections are valid JSON')"
    }

    Invoke-ValidationStep "pytest" {
        & $Python -m pytest
    }
}
finally {
    [Environment]::SetEnvironmentVariable("PYTHONPATH", $PreviousPythonPath, "Process")
}
