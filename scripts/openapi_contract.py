"""Generate, validate, and detect drift in Panorama's canonical OpenAPI contract.

Usage:
    python scripts/openapi_contract.py generate
    python scripts/openapi_contract.py validate
    python scripts/openapi_contract.py check-drift
"""

from __future__ import annotations

import argparse
import filecmp
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANAGE = ROOT / "app" / "manage.py"
SETTINGS = "config.settings.testing"
CANONICAL_FILES = {
    "openapi-json": ROOT / "docs" / "api" / "openapi.json",
    "openapi": ROOT / "docs" / "api" / "openapi.yaml",
}


def generate_file(schema_format: str, output_path: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            str(MANAGE),
            "spectacular",
            "--format",
            schema_format,
            "--validate",
            "--fail-on-warn",
            "--file",
            str(output_path),
            "--settings",
            SETTINGS,
        ],
        check=True,
        cwd=ROOT,
    )


def generate() -> int:
    for schema_format, output_path in CANONICAL_FILES.items():
        generate_file(schema_format, output_path)
    subprocess.run([sys.executable, "scripts/generate_api_collections.py"], check=True, cwd=ROOT)
    subprocess.run([sys.executable, "scripts/generate_api_contract_matrix.py"], check=True, cwd=ROOT)
    return 0


def validate() -> int:
    with tempfile.TemporaryDirectory(prefix="panorama-openapi-") as temporary_directory:
        temporary_path = Path(temporary_directory)
        generate_file("openapi-json", temporary_path / "openapi.json")
        generate_file("openapi", temporary_path / "openapi.yaml")
    subprocess.run([sys.executable, "scripts/validate_api_collections.py"], check=True, cwd=ROOT)
    return 0


def check_drift() -> int:
    with tempfile.TemporaryDirectory(prefix="panorama-openapi-") as temporary_directory:
        temporary_path = Path(temporary_directory)
        generated_files = {
            "openapi-json": temporary_path / "openapi.json",
            "openapi": temporary_path / "openapi.yaml",
        }
        for schema_format, output_path in generated_files.items():
            generate_file(schema_format, output_path)
        drifted = [
            canonical_path
            for schema_format, canonical_path in CANONICAL_FILES.items()
            if not filecmp.cmp(generated_files[schema_format], canonical_path, shallow=False)
        ]
    if drifted:
        relative_paths = ", ".join(str(path.relative_to(ROOT)) for path in drifted)
        print(f"OpenAPI contract drift detected: {relative_paths}. Run `python scripts/openapi_contract.py generate`.")
        return 1
    print("OpenAPI contract drift check passed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "validate", "check-drift"))
    command = parser.parse_args().command
    return {"generate": generate, "validate": validate, "check-drift": check_drift}[command]()


if __name__ == "__main__":
    raise SystemExit(main())
