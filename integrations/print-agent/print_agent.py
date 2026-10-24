"""Panorama silent print agent.

Runs on the computer that is connected to the shop printer. It polls the Panorama server for print jobs that staff sent from the
dashboard (status "queued"), downloads each print-ready PDF (pages per sheet, copies and two-sided are already applied by the server),
prints it with SumatraPDF without any dialog, and reports "printing" / "done" / "failed" back, so the dashboard shows what happened.

Standard library only. Configuration: config.json next to this file (see config.example.json and README.md).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

LOG = logging.getLogger("panorama-print-agent")
REQUEST_TIMEOUT = 30
PRINT_TIMEOUT = 180


@dataclass(frozen=True)
class Config:
    api_base: str
    identifier: str
    password: str
    printer: str
    sumatra_path: str
    poll_seconds: int = 5
    allow_insecure_http: bool = False

    @classmethod
    def load(cls, path: Path) -> Config:
        raw = json.loads(path.read_text(encoding="utf-8"))
        config = cls(
            api_base=str(raw["api_base"]).rstrip("/"),
            identifier=str(raw["identifier"]),
            password=str(raw["password"]),
            printer=str(raw["printer"]),
            sumatra_path=str(raw.get("sumatra_path", r"C:\Program Files\SumatraPDF\SumatraPDF.exe")),
            poll_seconds=max(2, int(raw.get("poll_seconds", 5))),
            allow_insecure_http=bool(raw.get("allow_insecure_http", False)),
        )
        config.validate()
        return config

    def validate(self) -> None:
        # The staff password and every document travel to this address: it must be HTTPS (plain http only for a local test).
        if not self.api_base.startswith("https://") and not (
            self.allow_insecure_http and self.api_base.startswith("http://")
        ):
            raise ValueError(
                "api_base must start with https:// (set allow_insecure_http only for a local test server)."
            )
        if not self.printer:
            raise ValueError("printer is required: use the exact Windows printer name.")


def sumatra_settings(options: dict) -> str:
    """The SumatraPDF ``-print-settings`` string that reproduces the order's printer options.

    The file already contains every copy in the right order, so ``copies`` is never repeated here (``1x``).
    """

    parts: list[str] = []
    if options.get("double_sided"):
        parts.append("duplexshort" if options.get("duplex_flip") == "short_edge" else "duplexlong")
    else:
        parts.append("simplex")
    parts.append("color" if options.get("color_mode") == "color" else "monochrome")
    paper = str(options.get("paper_size", "")).upper()
    if paper in {"A3", "A4", "A5"}:
        parts.append(f"paper={paper}")
    parts.append("fit")  # the sheets are already the right size; never let the printer rescale them
    return ",".join(parts)


def sumatra_command(config: Config, pdf_path: str, options: dict) -> list[str]:
    return [
        config.sumatra_path,
        "-print-to",
        config.printer,
        "-print-settings",
        sumatra_settings(options),
        "-silent",
        "-exit-when-done",
        pdf_path,
    ]


class ApiError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class Client:
    """A tiny authenticated client for the Panorama dashboard API (logs in again when the token expires)."""

    def __init__(self, config: Config, opener=urllib.request.urlopen):
        self.config = config
        self._open = opener
        self._token: str | None = None

    def _request(self, method: str, path: str, body: dict | None = None, raw: bool = False, auth: bool = True):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Accept": "application/json", "User-Agent": "panorama-print-agent/1.0"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if auth:
            headers["Authorization"] = f"Bearer {self._token}"
            if method != "GET":
                headers["Idempotency-Key"] = f"agent-{time.time_ns()}"
        request = urllib.request.Request(self.config.api_base + path, data=data, method=method, headers=headers)
        try:
            with self._open(request, timeout=REQUEST_TIMEOUT) as response:
                payload = response.read()
        except urllib.error.HTTPError as error:
            raise ApiError(error.code, error.read()[:300].decode("utf-8", "replace")) from error
        except urllib.error.URLError as error:
            raise ApiError(0, str(error.reason)) from error
        return payload if raw else json.loads(payload.decode("utf-8"))

    def login(self) -> None:
        reply = self._request(
            "POST",
            "/api/v1/auth/login/",
            {"identifier": self.config.identifier, "password": self.config.password},
            auth=False,
        )
        self._token = reply["data"]["access"]
        LOG.info("signed in")

    def call(self, method: str, path: str, body: dict | None = None, raw: bool = False):
        if self._token is None:
            self.login()
        try:
            return self._request(method, path, body, raw)
        except ApiError as error:
            if error.status != 401:
                raise
            self.login()  # expired or replaced session: sign in again once and retry
            return self._request(method, path, body, raw)

    def queued_jobs(self) -> list[dict]:
        reply = self.call(
            "GET", "/api/v1/dashboard/printing/print-jobs/?status=queued&ordering=created_at&page_size=20"
        )
        data = reply.get("data", reply)
        return data["results"] if isinstance(data, dict) and "results" in data else data

    def set_status(self, job_id: int, status: str, error: str = "", printer: str = "") -> None:
        body = {"status": status}
        if error:
            body["error"] = error[:250]
        if printer:
            body["printer_name"] = printer
        self.call("PATCH", f"/api/v1/dashboard/printing/print-jobs/{job_id}/status/", body)

    def download(self, job_id: int) -> bytes:
        return self.call("GET", f"/api/v1/dashboard/printing/print-jobs/{job_id}/file/", raw=True)


def print_job(client: Client, config: Config, job: dict, run=subprocess.run) -> bool:
    """Print one job. Returns True when it was handed to the printer. Never raises: a failure is reported to the dashboard."""

    job_id = int(job["id"])
    try:
        client.set_status(job_id, "printing", printer=config.printer)
        pdf = client.download(job_id)
        if not pdf.startswith(b"%PDF"):
            raise RuntimeError("the server did not return a PDF")
        with tempfile.TemporaryDirectory(prefix="panorama-print-") as folder:
            path = os.path.join(folder, f"job-{job_id}.pdf")
            Path(path).write_bytes(pdf)
            result = run(
                sumatra_command(config, path, job.get("options") or {}),
                capture_output=True,
                timeout=PRINT_TIMEOUT,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(f"SumatraPDF exited with code {result.returncode}")
        client.set_status(job_id, "done")
        LOG.info("job %s printed", job_id)
        return True
    except Exception as error:  # noqa: BLE001 - every failure must reach the dashboard instead of stopping the agent
        LOG.error("job %s failed: %s", job_id, error)
        try:
            client.set_status(job_id, "failed", error=str(error))
        except Exception:  # noqa: BLE001
            LOG.exception("could not report the failure of job %s", job_id)
        return False


def run_forever(config: Config, once: bool = False) -> None:
    client = Client(config)
    while True:
        try:
            for job in client.queued_jobs():
                print_job(client, config, job)
        except ApiError as error:
            LOG.warning("server not reachable or refused: %s", error)
        except Exception:  # noqa: BLE001
            LOG.exception("unexpected error while polling")
        if once:
            return
        time.sleep(config.poll_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Panorama silent print agent")
    parser.add_argument("--config", default=str(Path(__file__).with_name("config.json")))
    parser.add_argument("--once", action="store_true", help="process the queue once and exit")
    parser.add_argument("--log", default=str(Path(__file__).with_name("agent.log")))
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(args.log, encoding="utf-8")],
    )
    try:
        config = Config.load(Path(args.config))
    except (OSError, KeyError, ValueError) as error:
        LOG.error("configuration problem: %s", error)
        return 2
    LOG.info("watching %s for jobs, printer %s", urllib.parse.urlsplit(config.api_base).netloc, config.printer)
    run_forever(config, once=args.once)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
