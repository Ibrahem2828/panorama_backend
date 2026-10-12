"""Hardened subprocess runner for document conversion (LibreOffice, poppler, clamscan).

Uploaded documents are parsed by large native programs. If one of them is exploited, the attacker runs with the
privileges of the app/worker container, so this module reduces what that process can see and consume:

* a SCRUBBED environment: only ``PATH``, ``LANG`` and ``HOME`` (a per-job temp dir). ``SECRET_KEY``, database,
  Redis, encryption keys and every other variable of the parent are not inherited;
* a private per-job working directory (also the LibreOffice profile) that is always deleted afterwards;
* POSIX resource limits (``RLIMIT_CPU``, ``RLIMIT_AS``, ``RLIMIT_NOFILE``, ``RLIMIT_FSIZE``), skipped on Windows;
* a hard wall-clock timeout that kills the whole process group (LibreOffice spawns ``soffice.bin``);
* bounded captured output (stdout/stderr are spooled to size-limited files, only a prefix is returned).

Residual risk: none of this provides NETWORK isolation or a filesystem jail. The process still shares the
container's network namespace and can read any file the app user can read (for example mounted media). Full
isolation needs container-level controls (a dedicated no-network, read-only, non-root conversion container or
sandbox). See ``docs/LECTURE_VIEWER_AND_DOCUMENT_PIPELINE.md`` (section "Conversion sandbox and residual risk").
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from django.conf import settings

resource: Any  # the POSIX-only module, or None on Windows (typed Any so it checks the same on every platform)
try:  # pragma: no cover - platform dependent
    import resource
except ImportError:  # Windows
    resource = None

_POSIX = os.name == "posix"
_SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)
_DEFAULT_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ENV_ALLOWLIST = ("PATH", "LANG", "HOME")


class SandboxError(RuntimeError):
    """The sandboxed process could not be run safely."""


class SandboxTimeout(SandboxError):
    """The process exceeded its wall-clock limit and its process group was killed."""


@dataclass(frozen=True)
class SandboxResult:
    returncode: int
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True)
class ResourceLimits:
    cpu_seconds: int
    address_space_bytes: int | None
    open_files: int
    file_size_bytes: int


def default_limits(timeout: int, *, limit_memory: bool = True) -> ResourceLimits:
    mb = 1024 * 1024
    return ResourceLimits(
        cpu_seconds=int(getattr(settings, "DOCUMENT_CONVERSION_CPU_LIMIT", timeout + 10)),
        address_space_bytes=(
            int(getattr(settings, "DOCUMENT_CONVERSION_MEMORY_LIMIT_MB", 2048)) * mb if limit_memory else None
        ),
        open_files=int(getattr(settings, "DOCUMENT_CONVERSION_MAX_OPEN_FILES", 256)),
        file_size_bytes=int(getattr(settings, "DOCUMENT_CONVERSION_MAX_OUTPUT_BYTES", 256 * mb)),
    )


def scrubbed_environment(home: Path) -> dict[str, str]:
    """Allowlisted environment only; nothing else from the parent process is inherited."""
    return {
        "PATH": os.environ.get("PATH") or _DEFAULT_PATH,
        "LANG": os.environ.get("LANG") or "C.UTF-8",
        "HOME": str(home),
    }


def _build_preexec(limits: ResourceLimits) -> Callable[[], None] | None:
    """Return a ``preexec_fn`` applying POSIX rlimits, or ``None`` where unsupported (Windows)."""
    if not _POSIX or resource is None:
        return None

    def apply_limits() -> None:  # pragma: no cover - runs in the forked child
        def cap(kind: int, value: int) -> None:
            try:
                _, hard = resource.getrlimit(kind)
                if hard != resource.RLIM_INFINITY:
                    value = min(value, hard)
                resource.setrlimit(kind, (value, value))
            except (ValueError, OSError):
                pass

        cap(resource.RLIMIT_CPU, limits.cpu_seconds)
        if limits.address_space_bytes:
            cap(resource.RLIMIT_AS, limits.address_space_bytes)
        cap(resource.RLIMIT_NOFILE, limits.open_files)
        cap(resource.RLIMIT_FSIZE, limits.file_size_bytes)

    return apply_limits


@contextmanager
def conversion_job() -> Iterator[Path]:
    """Private per-job directory (HOME, cwd, LibreOffice profile). Removed on exit, even on error."""
    job_dir = Path(tempfile.mkdtemp(prefix="panorama-job-"))
    try:
        os.chmod(job_dir, 0o700)
        yield job_dir
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


def _kill_group(process: subprocess.Popen) -> None:
    try:
        if _POSIX:
            cast(Any, os).killpg(process.pid, _SIGKILL)  # POSIX-only; absent from the Windows stubs
        else:
            process.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _read_capped(path: Path, cap: int) -> bytes:
    try:
        with path.open("rb") as handle:
            return handle.read(cap)
    except OSError:
        return b""


def run_sandboxed(
    command: Sequence[str],
    *,
    job_dir: Path,
    timeout: int | None = None,
    limit_memory: bool = True,
) -> SandboxResult:
    """Run ``command`` with a scrubbed env, rlimits, a process-group kill timeout and capped output."""
    timeout = int(timeout if timeout is not None else getattr(settings, "DOCUMENT_CONVERSION_TIME_LIMIT", 180))
    output_cap = int(getattr(settings, "DOCUMENT_CONVERSION_MAX_CAPTURED_BYTES", 64 * 1024))
    limits = default_limits(timeout, limit_memory=limit_memory)
    stdout_path = job_dir / ".stdout"
    stderr_path = job_dir / ".stderr"

    env: dict[str, str] = scrubbed_environment(job_dir)
    start_new_session = False
    preexec: Callable[[], object] | None = None
    creationflags = 0
    if _POSIX:
        start_new_session = True  # own process group so the whole tree can be killed
        preexec = _build_preexec(limits)
    elif hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

    with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
        process = subprocess.Popen(  # noqa: S603
            list(command),
            shell=False,
            cwd=str(job_dir),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            start_new_session=start_new_session,
            preexec_fn=preexec,
            creationflags=creationflags,
        )
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            _kill_group(process)
            process.wait()
            raise SandboxTimeout("Conversion process exceeded its time limit.") from exc
        finally:
            _kill_group(process)  # reap stragglers such as soffice.bin that outlive the launcher
    return SandboxResult(
        returncode=returncode,
        stdout=_read_capped(stdout_path, output_cap),
        stderr=_read_capped(stderr_path, output_cap),
    )
