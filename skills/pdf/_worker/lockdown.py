"""Limits and filters the PDF worker puts on itself before it opens anything.

Run first thing in the worker process (``main.py``), so the parsers only ever run inside them:

- address space, CPU time, size of what it may write, open files, no core dumps (``resource``);
- no network: a seccomp filter makes ``socket`` and ``socketpair`` fail, and ``socket`` is also
  made unusable from Python;
- no programs: the same filter refuses ``execve``, ``execveat`` and ``ptrace``.

The filter is installed with ``PR_SET_NO_NEW_PRIVS``, which any process may do, so it needs no
privilege and no container capability. Where it can't be installed (not Linux, an architecture
this file has no numbers for), ``apply`` raises ``LockdownUnavailable`` and the worker does not
parse: it fails closed.
"""

from __future__ import annotations

import ctypes
import errno
import os
import platform
import struct
import sys
from dataclasses import dataclass


class LockdownUnavailable(Exception):
    """The sandbox can't be set up here, so nothing may be parsed."""


@dataclass(frozen=True)
class Limits:
    """What the worker may use (bytes, seconds)."""

    address_space: int
    cpu_seconds: int
    output_bytes: int


# Syscall numbers by architecture: (audit arch id, {name: number}). Only the calls refused.
_X86_64 = (
    0xC000003E,
    {
        "socket": 41,
        "socketpair": 53,
        "fork": 57,
        "vfork": 58,
        "execve": 59,
        "ptrace": 101,
        "execveat": 322,
    },
)
_AARCH64 = (
    0xC00000B7,
    {"socket": 198, "socketpair": 199, "execve": 221, "ptrace": 117, "execveat": 281},
)
_ARCHITECTURES = {"x86_64": _X86_64, "amd64": _X86_64, "aarch64": _AARCH64, "arm64": _AARCH64}

_PR_SET_NO_NEW_PRIVS = 38
_PR_SET_SECCOMP = 22
_SECCOMP_MODE_FILTER = 2
_SECCOMP_RET_ALLOW = 0x7FFF0000
_SECCOMP_RET_ERRNO = 0x00050000
_SECCOMP_RET_KILL_PROCESS = 0x80000000

# BPF instruction codes.
_BPF_LD_W_ABS = 0x20
_BPF_JEQ_K = 0x15
_BPF_RET_K = 0x06


def _instruction(code: int, jt: int, jf: int, k: int) -> bytes:
    return struct.pack("HBBI", code, jt, jf, k)


def _program(arch: int, numbers: list[int]) -> bytes:
    """The BPF program: kill on a foreign architecture, EPERM for a refused call, else allow."""
    program = [
        _instruction(_BPF_LD_W_ABS, 0, 0, 4),  # seccomp_data.arch
        _instruction(_BPF_JEQ_K, 1, 0, arch),
        _instruction(_BPF_RET_K, 0, 0, _SECCOMP_RET_KILL_PROCESS),
        _instruction(_BPF_LD_W_ABS, 0, 0, 0),  # seccomp_data.nr
    ]
    for index, number in enumerate(numbers):
        remaining = len(numbers) - index - 1
        # Equal: jump over the remaining comparisons and the allow, to the EPERM return.
        program.append(_instruction(_BPF_JEQ_K, remaining + 1, 0, number))
    program.append(_instruction(_BPF_RET_K, 0, 0, _SECCOMP_RET_ALLOW))
    program.append(_instruction(_BPF_RET_K, 0, 0, _SECCOMP_RET_ERRNO | errno.EPERM))
    return b"".join(program)


def _install_seccomp() -> None:
    if not sys.platform.startswith("linux"):
        raise LockdownUnavailable("not Linux")
    entry = _ARCHITECTURES.get(platform.machine().lower())
    if entry is None:
        raise LockdownUnavailable("unknown architecture")
    arch, calls = entry
    program = _program(arch, sorted(calls.values()))
    count = len(program) // 8
    buffer = ctypes.create_string_buffer(program, len(program))

    class SockFprog(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.c_void_p)]

    fprog = SockFprog(count, ctypes.cast(buffer, ctypes.c_void_p))
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise LockdownUnavailable("no_new_privs refused")
    if libc.prctl(_PR_SET_SECCOMP, _SECCOMP_MODE_FILTER, ctypes.addressof(fprog), 0, 0) != 0:
        raise LockdownUnavailable("seccomp refused")


def _block_python_sockets() -> None:
    """Defence in depth: ``socket`` can't be created from Python either."""
    import socket

    def refuse(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise PermissionError(errno.EPERM, "network access is not allowed here")

    socket.socket = refuse  # type: ignore[assignment,misc]
    socket.create_connection = refuse  # type: ignore[assignment]
    socket.getaddrinfo = refuse  # type: ignore[assignment]


def apply(limits: Limits) -> None:
    """Put every limit and filter on this process. Raises ``LockdownUnavailable`` if it can't."""
    try:
        import resource
    except ImportError as exc:  # not POSIX
        raise LockdownUnavailable("no resource module") from exc
    try:
        for name, value in (
            ("RLIMIT_AS", limits.address_space),
            ("RLIMIT_CPU", limits.cpu_seconds),
            ("RLIMIT_FSIZE", limits.output_bytes),
            ("RLIMIT_NOFILE", 64),
            ("RLIMIT_CORE", 0),
        ):
            resource.setrlimit(getattr(resource, name), (value, value))
    except (ValueError, OSError) as exc:
        raise LockdownUnavailable("a resource limit was refused") from exc
    _install_seccomp()
    _block_python_sockets()
    os.environ.clear()  # nothing in the environment is needed, or should be readable


def selftest() -> dict[str, bool]:
    """What the filter refuses, tried from inside: used by the tests and the release check."""
    import socket

    outcome = {}
    try:
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        outcome["python_socket_refused"] = False
    except PermissionError:
        outcome["python_socket_refused"] = True
    libc = ctypes.CDLL(None, use_errno=True)
    fd = libc.socket(2, 1, 0)  # AF_INET, SOCK_STREAM, straight through libc
    outcome["libc_socket_refused"] = fd < 0 and ctypes.get_errno() == errno.EPERM
    pid_result = libc.execve(b"/bin/true", None, None)
    outcome["exec_refused"] = pid_result < 0 and ctypes.get_errno() == errno.EPERM
    return outcome
