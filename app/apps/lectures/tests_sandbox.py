from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from apps.lectures import sandbox
from apps.lectures.document_pipeline import DocumentPipelineError, convert_to_pdf
from apps.lectures.sandbox import (
    ResourceLimits,
    SandboxTimeout,
    conversion_job,
    run_sandboxed,
    scrubbed_environment,
)

SECRETS = {
    "SECRET_KEY": "s3cret",
    "DATABASE_URL": "postgres://u:p@h/db",
    "REDIS_URL": "redis://:p@h/0",
    "FIELD_ENCRYPTION_KEY": "k",
    "EMAIL_HOST_PASSWORD": "pw",
}


def _fake_popen(returncode=0, wait_side_effect=None):
    process = MagicMock()
    process.pid = 4242
    process.wait.side_effect = wait_side_effect or (lambda timeout=None: returncode)
    return process


def test_environment_is_scrubbed_to_allowlist(tmp_path):
    with patch.dict(os.environ, SECRETS | {"PATH": "/usr/bin", "LANG": "en_US.UTF-8"}):
        env = scrubbed_environment(tmp_path)
    assert set(env) == {"PATH", "LANG", "HOME"}
    assert env["HOME"] == str(tmp_path)
    assert not set(SECRETS) & set(env)


def test_run_sandboxed_passes_scrubbed_env_cwd_and_no_shell():
    with (
        patch.dict(os.environ, SECRETS),
        conversion_job() as job,
        patch.object(sandbox.subprocess, "Popen", return_value=_fake_popen()) as popen,
    ):
        result = run_sandboxed(["tool", "--x"], job_dir=job, timeout=30)
    kwargs = popen.call_args.kwargs
    assert popen.call_args.args[0] == ["tool", "--x"]
    assert kwargs["shell"] is False
    assert kwargs["cwd"] == str(job)
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert set(kwargs["env"]) == {"PATH", "LANG", "HOME"}
    assert not set(SECRETS) & set(kwargs["env"])
    assert kwargs["env"]["HOME"] == str(job)
    assert result.returncode == 0


def test_resource_limits_are_applied_through_preexec_on_posix():
    fake_resource = MagicMock()
    fake_resource.RLIMIT_CPU, fake_resource.RLIMIT_AS = 0, 9
    fake_resource.RLIMIT_NOFILE, fake_resource.RLIMIT_FSIZE = 7, 1
    fake_resource.RLIM_INFINITY = -1
    fake_resource.getrlimit.return_value = (-1, -1)
    limits = ResourceLimits(cpu_seconds=40, address_space_bytes=1000, open_files=64, file_size_bytes=2000)
    with patch.object(sandbox, "_POSIX", True), patch.object(sandbox, "resource", fake_resource):
        preexec = sandbox._build_preexec(limits)
        assert callable(preexec)
        preexec()
    applied = {call.args[0]: call.args[1] for call in fake_resource.setrlimit.call_args_list}
    assert applied == {0: (40, 40), 9: (1000, 1000), 7: (64, 64), 1: (2000, 2000)}


def test_resource_limits_are_skipped_where_unsupported():
    limits = ResourceLimits(cpu_seconds=1, address_space_bytes=1, open_files=1, file_size_bytes=1)
    with patch.object(sandbox, "_POSIX", False):
        assert sandbox._build_preexec(limits) is None
    with patch.object(sandbox, "_POSIX", True), patch.object(sandbox, "resource", None):
        assert sandbox._build_preexec(limits) is None


def test_posix_launch_uses_new_session_and_preexec(settings):
    settings.DOCUMENT_CONVERSION_MEMORY_LIMIT_MB = 512
    with patch.object(sandbox, "_POSIX", True), patch.object(sandbox, "_build_preexec", return_value=lambda: None):
        with conversion_job() as job, patch.object(sandbox.subprocess, "Popen", return_value=_fake_popen()) as popen:
            with patch.object(sandbox.os, "killpg", create=True):
                run_sandboxed(["tool"], job_dir=job, timeout=5)
    kwargs = popen.call_args.kwargs
    assert kwargs["start_new_session"] is True
    assert callable(kwargs["preexec_fn"])


def test_default_limits_follow_settings(settings):
    settings.DOCUMENT_CONVERSION_MEMORY_LIMIT_MB = 100
    settings.DOCUMENT_CONVERSION_MAX_OPEN_FILES = 32
    settings.DOCUMENT_CONVERSION_MAX_OUTPUT_BYTES = 5000
    limits = sandbox.default_limits(60)
    assert limits.cpu_seconds == 70
    assert limits.address_space_bytes == 100 * 1024 * 1024
    assert limits.open_files == 32
    assert limits.file_size_bytes == 5000
    assert sandbox.default_limits(60, limit_memory=False).address_space_bytes is None


def test_timeout_kills_the_process_group_and_raises():
    calls = []

    def wait(timeout=None):
        if timeout is not None:
            raise subprocess.TimeoutExpired(cmd="tool", timeout=timeout)
        return -9

    process = _fake_popen(wait_side_effect=wait)
    with conversion_job() as job, patch.object(sandbox.subprocess, "Popen", return_value=process):
        with patch.object(sandbox, "_kill_group", side_effect=lambda proc: calls.append(proc)):
            with pytest.raises(SandboxTimeout):
                run_sandboxed(["tool"], job_dir=job, timeout=7)
    assert process.wait.call_args_list[0].kwargs == {"timeout": 7}
    assert calls and calls[0] is process


def test_kill_group_uses_killpg_on_posix():
    process = SimpleNamespace(pid=123, kill=MagicMock())
    with patch.object(sandbox, "_POSIX", True), patch.object(sandbox.os, "killpg", create=True) as killpg:
        sandbox._kill_group(cast("subprocess.Popen[bytes]", process))
    killpg.assert_called_once_with(123, sandbox._SIGKILL)


def test_captured_output_is_capped(settings):
    settings.DOCUMENT_CONVERSION_MAX_CAPTURED_BYTES = 10

    def popen(command, stdout, stderr, **kwargs):
        stdout.write(b"A" * 1000)
        stderr.write(b"B" * 1000)
        return _fake_popen(returncode=3)

    with conversion_job() as job, patch.object(sandbox.subprocess, "Popen", side_effect=popen):
        result = run_sandboxed(["tool"], job_dir=job, timeout=5)
    assert result.returncode == 3
    assert result.stdout == b"A" * 10
    assert result.stderr == b"B" * 10


def test_job_directory_is_deleted_even_on_error():
    with pytest.raises(RuntimeError):
        with conversion_job() as job:
            (job / "profile").mkdir()
            (job / "profile" / "x").write_text("data")
            seen = job
            raise RuntimeError("boom")
    assert not Path(seen).exists()


def test_convert_to_pdf_runs_in_sandbox_cleans_profile_and_maps_timeout(tmp_path, settings):
    source = tmp_path / "input.docx"
    source.write_bytes(b"x")
    jobs = []

    def fake_run(command, *, job_dir, timeout, limit_memory=True):
        jobs.append(job_dir)
        assert any(part.startswith("-env:UserInstallation=") for part in command)
        assert job_dir.name in next(p for p in command if p.startswith("-env:UserInstallation="))
        raise SandboxTimeout("slow")

    settings.DOCUMENT_CONVERSION_TIME_LIMIT = 11
    with patch("apps.lectures.document_pipeline.shutil.which", return_value="soffice"):
        with patch("apps.lectures.document_pipeline.run_sandboxed", side_effect=fake_run):
            with pytest.raises(DocumentPipelineError):
                convert_to_pdf(source, ".docx", tmp_path)
    assert jobs and not jobs[0].exists()


def test_convert_to_pdf_rejects_oversized_output(tmp_path, settings):
    source = tmp_path / "input.docx"
    source.write_bytes(b"x")
    settings.DOCUMENT_CONVERSION_MAX_OUTPUT_BYTES = 4

    def fake_run(command, **kwargs):
        out = Path(command[command.index("--outdir") + 1])
        (out / "input.pdf").write_bytes(b"%PDF-too-big")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    with patch("apps.lectures.document_pipeline.shutil.which", return_value="soffice"):
        with patch("apps.lectures.document_pipeline.run_sandboxed", side_effect=fake_run):
            with pytest.raises(DocumentPipelineError):
                convert_to_pdf(source, ".docx", tmp_path)
