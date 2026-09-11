"""Owned subprocesses: cancellable pipes, process-tree termination and parent guards."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time

from scutio_data._runtime.timeouts import add_timing, check_cancelled, observe_event, remaining

_PARENT_PID = "SCUTIO_WORKER_PARENT_PID"


def install_worker_guard():
    """A managed POSIX worker also exits if its creating parent disappears abruptly."""
    raw = os.environ.pop(_PARENT_PID, "")
    if os.name == "nt" or not raw.isdigit():
        return
    parent = int(raw)
    # Only a process-group leader created by managed_run may kill its own group.
    if os.getpgrp() != os.getpid():
        return

    def watch():
        while os.getppid() == parent:
            time.sleep(0.1)
        os.killpg(os.getpgrp(), signal.SIGKILL)

    threading.Thread(target=watch, name="scutio-parent-guard", daemon=True).start()


class _WindowsJob:
    """Kernel-owned tree lifetime; children start suspended until safely assigned."""

    def __init__(self, process):
        import ctypes
        from ctypes import wintypes

        class IoCounters(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_ulonglong)
                for name in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            ]

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.kernel.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.TerminateJobObject.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            limits = ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not self.kernel.SetInformationJobObject(
                self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
                raise ctypes.WinError(ctypes.get_last_error())
            # Popen closes the initial thread handle; resume the suspended process atomically.
            resume = ctypes.WinDLL("ntdll").NtResumeProcess
            resume.argtypes = [wintypes.HANDLE]
            resume.restype = ctypes.c_long
            if resume(int(process._handle)) != 0:
                raise OSError("could not resume managed Windows worker")
        except BaseException:
            self.close()
            raise

    def terminate(self):
        if self.handle:
            self.kernel.TerminateJobObject(self.handle, 1)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _terminate_tree(process, job):
    if job is not None:
        job.terminate()
    elif os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.poll() is None:
        # Only reachable when job assignment failed before the suspended worker ran.
        process.kill()
    process.wait()


def managed_run(
    args,
    *,
    input=None,
    text=True,
    encoding="utf-8",
    errors="replace",
    timeout=None,
    env=None,
    cwd=None,
    check=False,
    capture_output=True,
    stage="worker",
):
    """Run one owned tree and always reap it before returning or propagating control errors.

    Only pipe-based input/output is supported. No shell and no compatibility path to
    subprocess.run: tests should replace this worker boundary or its network connector.
    """
    if not capture_output:
        raise ValueError("managed workers require captured output")
    check_cancelled()
    seconds = remaining(stage, maximum=timeout) if timeout is not None else remaining(stage)
    deadline = time.monotonic() + seconds
    environment = dict(os.environ if env is None else env)
    environment[_PARENT_PID] = str(os.getpid())
    options = {"creationflags": 0x00000004} if os.name == "nt" else {"start_new_session": True}
    started = time.monotonic()
    process = None
    job = None
    observe_event(stage)
    try:
        process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=text,
            encoding=encoding if text else None,
            errors=errors if text else None,
            env=environment,
            cwd=cwd,
            **options,
        )
        if os.name == "nt":
            job = _WindowsJob(process)
        first = True
        while True:
            check_cancelled()
            left = deadline - time.monotonic()
            if left <= 0:
                observe_event("timeout", timeout_stage=stage)
                raise subprocess.TimeoutExpired(args, seconds)
            try:
                stdout, stderr = process.communicate(
                    input=input if first else None, timeout=min(0.05, left)
                )
                break
            except subprocess.TimeoutExpired:
                first = False
        check_cancelled()
        result = subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
        if check:
            result.check_returncode()
        return result
    finally:
        if process is not None:
            try:
                # A finished leader is not evidence that its descendants have finished.
                _terminate_tree(process, job)
            finally:
                if job is not None:
                    job.close()
                for pipe in (process.stdin, process.stdout, process.stderr):
                    if pipe is not None:
                        pipe.close()
        add_timing(stage + "_seconds", time.monotonic() - started)
