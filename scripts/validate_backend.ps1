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
    $global:LASTEXITCODE = 0
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Validation step failed: $Name"
    }
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

    Invoke-ValidationStep "Python syntax check" {
        & $Python -c "from pathlib import Path; [compile(path.read_text(encoding='utf-8'), str(path), 'exec') for path in sorted(Path('app').rglob('*.py')) if '__pycache__' not in path.parts]; print('Python syntax check passed')"
    }

    Invoke-ValidationStep "Django system check" {
        & $Python "app\manage.py" check --settings config.settings.testing
    }

    Invoke-ValidationStep "Django migration check" {
        & $Python "app\manage.py" makemigrations --check --dry-run --settings config.settings.testing
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
        Set-ValidationDefault "FIELD_ENCRYPTION_KEY" "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=" $deployEnv
        Set-ValidationDefault "EMAIL_HOST" "smtp.example.test" $deployEnv
        Set-ValidationDefault "EMAIL_HOST_USER" "validation@example.test" $deployEnv
        Set-ValidationDefault "EMAIL_HOST_PASSWORD" "validation-only-password" $deployEnv
        Set-ValidationDefault "STORAGE_BACKEND" "local" $deployEnv
        Set-ValidationDefault "SECURE_SSL_REDIRECT" "True" $deployEnv
        Set-ValidationDefault "SESSION_COOKIE_SECURE" "True" $deployEnv
        Set-ValidationDefault "CSRF_COOKIE_SECURE" "True" $deployEnv
        Set-ValidationDefault "SECURE_HSTS_SECONDS" "31536000" $deployEnv
        Set-ValidationDefault "SECURE_HSTS_INCLUDE_SUBDOMAINS" "True" $deployEnv
        Set-ValidationDefault "SECURE_HSTS_PRELOAD" "True" $deployEnv

        Invoke-ValidationStep "Django deploy check" {
            & $Python "app\manage.py" check --deploy --settings config.settings.production
        }
    }

    Invoke-ValidationStep "Canonical OpenAPI validation" {
        & $Python "scripts\openapi_contract.py" validate
    }

    Invoke-ValidationStep "Canonical OpenAPI drift check" {
        & $Python "scripts\openapi_contract.py" check-drift
    }

    Invoke-ValidationStep "Ruff" {
        & $Python -m ruff check .
        & $Python -m ruff format --check .
    }

    Invoke-ValidationStep "Mypy" {
        & $Python -m mypy app
    }

    Invoke-ValidationStep "Bandit" {
        & $Python -m bandit -q -r app -x '*/migrations/*,*/tests/*' --severity-level medium
    }

    Invoke-ValidationStep "focused pytest: WebSocket hardening" {
        & $Python -m pytest "app\apps\chat\tests_handshake_tickets.py"
    }

    Invoke-ValidationStep "pytest" {
        & $Python -m pytest
    }
}
finally {
    [Environment]::SetEnvironmentVariable("PYTHONPATH", $PreviousPythonPath, "Process")
}
