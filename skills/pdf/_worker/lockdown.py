"""Limits and filters the PDF worker puts on itself before it opens anything.

Run first thing in the worker process (``main.py``), so the parsers only ever run inside them:

- address space, CPU time, size of what it may write, open files, no core dumps (``resource``);
- no network: a seccomp filter makes ``socket`` and ``socketpair`` fail, so do the three
  ``io_uring`` calls (which can open sockets on their own), and ``socket`` is also made unusable
  from Python;
- no programs and no new processes: the same filter refuses ``execve``, ``execveat``,
  ``ptrace``, the calls that read another process's memory, ``fork``/``vfork`` and every
  ``clone`` that isn't a thread; a call made in the 32-bit-on-64-bit (x32) numbering kills the
  process, since it would walk around the numbers above;
- no reach into the server: the worker is the server's child, so the filter also refuses the calls
  that send it a signal (``kill``, ``tgkill`` only on the worker's own pid; ``tkill``, the queued
  signals, ``pidfd_*`` never) and the calls that change another process (``prlimit64`` and the
  scheduling and memory-policy calls only on the worker itself; ``setpriority``, ``ioprio_set``
  never): none of them is covered by Landlock or by ``ptrace`` rules;
- a first place in the queue for the kernel's out-of-memory killer (``oom_score_adj`` 1000), so
  that if the machine runs out of memory the worker dies, not the server;
- no files beyond what the parsers were loaded from (``confine_filesystem``): after the parser
  modules are imported, a Landlock ruleset leaves the worker able to read the Python installation
  and the system libraries and nothing else, and to write nowhere.

The seccomp filter is installed with ``PR_SET_NO_NEW_PRIVS``, which any process may do, so it needs
no privilege and no container capability. Where it can't be installed (not Linux, an architecture
this file has no numbers for), ``apply`` raises ``LockdownUnavailable`` and the worker does not
parse: it fails closed. Landlock needs Linux 5.13 and a kernel with it enabled; where it is
missing the worker still parses (every other limit above holds) and says so (``Confinement``), and
the release check (``python -m skills.pdf``) reports it.
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


@dataclass(frozen=True)
class _Architecture:
    """The syscall numbers the filter needs on one CPU architecture.

    Attributes:
        audit: The audit architecture id (``seccomp_data.arch``).
        refused: Calls refused with EPERM, whatever their arguments.
        own_process_only: Calls whose first argument is a pid, refused with EPERM unless it is 0
            (the caller) or the worker's own pid.
        clone, clone3: The numbers of those two calls.
        has_x32: Whether the x32 numbering (a bit in the call number) exists here.
        kill, tgkill: Allowed only on the worker's own pid.
    """

    audit: int
    refused: dict[str, int]
    own_process_only: dict[str, int]
    clone: int
    clone3: int
    has_x32: bool
    kill: int
    tgkill: int


_X86_64 = _Architecture(
    audit=0xC000003E,
    refused={
        "socket": 41,
        "socketpair": 53,
        "fork": 57,
        "vfork": 58,
        "execve": 59,
        "ptrace": 101,
        "process_vm_readv": 310,
        "process_vm_writev": 311,
        "execveat": 322,
        "tkill": 200,
        "rt_sigqueueinfo": 129,
        "rt_tgsigqueueinfo": 297,
        "pidfd_send_signal": 424,
        "pidfd_open": 434,
        "pidfd_getfd": 438,
        "io_uring_setup": 425,
        "io_uring_enter": 426,
        "io_uring_register": 427,
        "setpriority": 141,
        "ioprio_set": 251,
    },
    own_process_only={
        "prlimit64": 302,
        "sched_setparam": 142,
        "sched_setscheduler": 144,
        "sched_setaffinity": 203,
        "sched_setattr": 314,
        "migrate_pages": 256,
        "move_pages": 279,
    },
    clone=56,
    clone3=435,
    has_x32=True,
    kill=62,
    tgkill=234,
)
_AARCH64 = _Architecture(
    audit=0xC00000B7,
    refused={
        "socket": 198,
        "socketpair": 199,
        "execve": 221,
        "ptrace": 117,
        "process_vm_readv": 270,
        "process_vm_writev": 271,
        "execveat": 281,
        "tkill": 130,
        "rt_sigqueueinfo": 138,
        "rt_tgsigqueueinfo": 240,
        "pidfd_send_signal": 424,
        "pidfd_open": 434,
        "pidfd_getfd": 438,
        "io_uring_setup": 425,
        "io_uring_enter": 426,
        "io_uring_register": 427,
        "setpriority": 140,
        "ioprio_set": 30,
    },
    own_process_only={
        "prlimit64": 261,
        "sched_setparam": 118,
        "sched_setscheduler": 119,
        "sched_setaffinity": 122,
        "sched_setattr": 274,
        "migrate_pages": 238,
        "move_pages": 239,
    },
    clone=220,
    clone3=435,
    has_x32=False,
    kill=129,
    tgkill=131,
)
_ARCHITECTURES = {"x86_64": _X86_64, "amd64": _X86_64, "aarch64": _AARCH64, "arm64": _AARCH64}

_PR_SET_NO_NEW_PRIVS = 38
_PR_SET_SECCOMP = 22
_SECCOMP_MODE_FILTER = 2
_SECCOMP_RET_ALLOW = 0x7FFF0000
_SECCOMP_RET_ERRNO = 0x00050000
_SECCOMP_RET_KILL_PROCESS = 0x80000000
_X32_BIT = 0x40000000
_CLONE_THREAD = 0x00010000

# BPF instruction codes.
_BPF_LD_W_ABS = 0x20
_BPF_JEQ_K = 0x15
_BPF_JSET_K = 0x45
_BPF_JGE_K = 0x35
_BPF_RET_K = 0x06
_ARG0_LOW_WORD = 16  # seccomp_data.args[0], the low 32 bits (little endian)


class _Assembler:
    """A tiny BPF assembler with labels, so jump offsets aren't counted by hand."""

    def __init__(self) -> None:
        self._ops: list[tuple[int, str | None, str | None, int]] = []
        self._labels: dict[str, int] = {}

    def label(self, name: str) -> None:
        self._labels[name] = len(self._ops)

    def op(self, code: int, k: int, jt: str | None = None, jf: str | None = None) -> None:
        self._ops.append((code, jt, jf, k))

    def assemble(self) -> bytes:
        out = []
        for index, (code, jt, jf, k) in enumerate(self._ops):
            true = self._labels[jt] - index - 1 if jt else 0
            false = self._labels[jf] - index - 1 if jf else 0
            out.append(struct.pack("HBBI", code, true, false, k))
        return b"".join(out)


def _program(entry: _Architecture) -> bytes:
    """The BPF program for one architecture.

    Foreign architecture: kill. An x32 number (x86-64 only): kill. A refused call: EPERM.
    ``kill`` and ``tgkill``: allowed only on this process's own pid (a signal to the server, or to
    a group, is refused). A call in ``own_process_only`` (``prlimit64``, the scheduling calls):
    allowed only with pid 0 or this process's own pid, so it can't change the server.
    ``clone3``: ENOSYS (libc then falls back to ``clone``, whose flags can be read). ``clone``:
    allowed only with ``CLONE_THREAD``, so a thread starts and a process doesn't. Anything else:
    allowed.
    """
    asm = _Assembler()
    asm.op(_BPF_LD_W_ABS, 4)  # seccomp_data.arch
    asm.op(_BPF_JEQ_K, entry.audit, jt="nr", jf="kill")
    asm.label("nr")
    asm.op(_BPF_LD_W_ABS, 0)  # seccomp_data.nr
    if entry.has_x32:
        asm.op(_BPF_JGE_K, _X32_BIT, jt="kill")
    for number in sorted(entry.refused.values()):
        asm.op(_BPF_JEQ_K, number, jt="eperm")
    asm.op(_BPF_JEQ_K, entry.clone3, jt="enosys")
    asm.op(_BPF_JEQ_K, entry.clone, jt="clone")
    asm.op(_BPF_JEQ_K, entry.kill, jt="own_pid")
    asm.op(_BPF_JEQ_K, entry.tgkill, jt="own_pid")
    for number in sorted(entry.own_process_only.values()):
        asm.op(_BPF_JEQ_K, number, jt="self_or_zero")
    asm.op(_BPF_RET_K, _SECCOMP_RET_ALLOW)
    asm.label("own_pid")
    asm.op(_BPF_LD_W_ABS, _ARG0_LOW_WORD)
    asm.op(_BPF_JEQ_K, os.getpid(), jt="allow", jf="eperm")
    asm.label("self_or_zero")
    asm.op(_BPF_LD_W_ABS, _ARG0_LOW_WORD)
    asm.op(_BPF_JEQ_K, 0, jt="allow")
    asm.op(_BPF_JEQ_K, os.getpid(), jt="allow", jf="eperm")
    asm.label("clone")
    asm.op(_BPF_LD_W_ABS, _ARG0_LOW_WORD)
    asm.op(_BPF_JSET_K, _CLONE_THREAD, jt="allow", jf="eperm")
    asm.label("allow")
    asm.op(_BPF_RET_K, _SECCOMP_RET_ALLOW)
    asm.label("kill")
    asm.op(_BPF_RET_K, _SECCOMP_RET_KILL_PROCESS)
    asm.label("eperm")
    asm.op(_BPF_RET_K, _SECCOMP_RET_ERRNO | errno.EPERM)
    asm.label("enosys")
    asm.op(_BPF_RET_K, _SECCOMP_RET_ERRNO | errno.ENOSYS)
    return asm.assemble()


def _install_seccomp() -> None:
    if not sys.platform.startswith("linux"):
        raise LockdownUnavailable("not Linux")
    entry = _ARCHITECTURES.get(platform.machine().lower())
    if entry is None:
        raise LockdownUnavailable("unknown architecture")
    program = _program(entry)
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


# Raising a process's own oom_score_adj needs no privilege; lowering it does. 1000 is the highest:
# the kernel's out-of-memory killer picks the process with the highest score, so if the machine
# (or the container's memory limit) runs out, it stops the worker before the server.
_OOM_SCORE_ADJ = b"1000"
# Kept open: after ``confine_filesystem`` the file can't be opened any more, but the self-test can
# still read back, from this descriptor, what the kernel holds.
_oom_score_fd: int | None = None


def prefer_to_be_killed_first() -> None:
    """Set this process's ``oom_score_adj`` to 1000. Raises ``LockdownUnavailable`` if it can't."""
    global _oom_score_fd
    try:
        fd = os.open("/proc/self/oom_score_adj", os.O_RDWR | os.O_CLOEXEC)
        os.write(fd, _OOM_SCORE_ADJ)
    except OSError as exc:
        raise LockdownUnavailable("oom_score_adj can't be set") from exc
    _oom_score_fd = fd


def apply(limits: Limits) -> None:
    """Put every limit and the seccomp filter on this process.

    Raises ``LockdownUnavailable`` if it can't. The filesystem is confined later, by
    ``confine_filesystem``, once the parsers are imported.
    """
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
    prefer_to_be_killed_first()
    _install_seccomp()
    _block_python_sockets()
    os.environ.clear()  # nothing in the environment is needed, or should be readable


# -- The filesystem ----------------------------------------------------------------------------

_SYS_LANDLOCK_CREATE_RULESET = 444
_SYS_LANDLOCK_ADD_RULE = 445
_SYS_LANDLOCK_RESTRICT_SELF = 446
_LANDLOCK_CREATE_RULESET_VERSION = 1
_LANDLOCK_RULE_PATH_BENEATH = 1
# ABI 1 access rights, then the ones later versions add.
_FS_EXECUTE = 1 << 0
_FS_READ_FILE = 1 << 2
_FS_READ_DIR = 1 << 3
_FS_ALL_V1 = (1 << 13) - 1
_FS_REFER = 1 << 13
_FS_TRUNCATE = 1 << 14
_FS_IOCTL_DEV = 1 << 15

# Where the worker may read after its imports: the Python installation and the system libraries.
_SYSTEM_LIBRARY_DIRS = ("/lib", "/lib64", "/usr/lib", "/usr/lib64", "/usr/local/lib")


@dataclass(frozen=True)
class Confinement:
    """Whether the filesystem is confined, and why not when it isn't."""

    confined: bool
    reason: str = ""


def _readable_directories() -> list[str]:
    import site

    candidates = [sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix]
    candidates += [p for p in sys.path if p]
    try:
        candidates += site.getsitepackages()
    except Exception:  # noqa: BLE001
        pass
    candidates += list(_SYSTEM_LIBRARY_DIRS)
    seen: list[str] = []
    for path in candidates:
        real = os.path.realpath(path)
        if os.path.isdir(real) and real not in seen and real != "/":
            seen.append(real)
    return seen


def confine_filesystem() -> Confinement:
    """Leave this process able to read the Python installation and the system libraries only.

    Landlock (Linux 5.13+): every file right it knows is handled, and only reading (and
    executing, for libraries) is allowed, beneath the directories of ``_readable_directories``.
    So the worker can't open another person's upload for reading, can't create, write, truncate or
    delete anything, and can't make a directory; what it already has open (stdin, stdout) keeps
    working. Call it after the parser modules are imported and the files the run still needs are
    open. Never raises: where Landlock isn't there, it says so.
    """
    if not sys.platform.startswith("linux"):
        return Confinement(False, "not Linux")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    version = libc.syscall(_SYS_LANDLOCK_CREATE_RULESET, None, 0, _LANDLOCK_CREATE_RULESET_VERSION)
    if version < 1:
        return Confinement(False, "Landlock is not available")
    handled = _FS_ALL_V1
    if version >= 2:
        handled |= _FS_REFER
    if version >= 3:
        handled |= _FS_TRUNCATE
    if version >= 5:
        handled |= _FS_IOCTL_DEV
    attr = struct.pack("Q", handled)
    ruleset = libc.syscall(_SYS_LANDLOCK_CREATE_RULESET, attr, len(attr), 0)
    if ruleset < 0:
        return Confinement(False, "Landlock refused the ruleset")
    try:
        allowed = _FS_EXECUTE | _FS_READ_FILE | _FS_READ_DIR
        for directory in _readable_directories():
            try:
                fd = os.open(directory, os.O_PATH | os.O_CLOEXEC)
            except OSError:
                continue
            try:
                rule = struct.pack("Qi", allowed, fd)
                libc.syscall(_SYS_LANDLOCK_ADD_RULE, ruleset, _LANDLOCK_RULE_PATH_BENEATH, rule, 0)
            finally:
                os.close(fd)
        if libc.syscall(_SYS_LANDLOCK_RESTRICT_SELF, ruleset, 0) != 0:
            return Confinement(False, "Landlock refused to restrict the process")
    finally:
        os.close(ruleset)
    return Confinement(True)


# The numbers the self-test calls, written out a second time on purpose: the filter's own tables
# aren't trusted to check themselves (a wrong number in them would be just as wrong here).
_SELFTEST_CALLS = {
    "x86_64": {
        "tkill": 200,
        "tgkill": 234,
        "rt_sigqueueinfo": 129,
        "rt_tgsigqueueinfo": 297,
        "pidfd_send_signal": 424,
        "prlimit64": 302,
        "setpriority": 141,
        "ioprio_set": 251,
        "sched_setparam": 142,
        "sched_setscheduler": 144,
        "sched_setaffinity": 203,
        "sched_setattr": 314,
        "migrate_pages": 256,
        "move_pages": 279,
    },
    "aarch64": {
        "tkill": 130,
        "tgkill": 131,
        "rt_sigqueueinfo": 138,
        "rt_tgsigqueueinfo": 240,
        "pidfd_send_signal": 424,
        "prlimit64": 261,
        "setpriority": 140,
        "ioprio_set": 30,
        "sched_setparam": 118,
        "sched_setscheduler": 119,
        "sched_setaffinity": 122,
        "sched_setattr": 274,
        "migrate_pages": 238,
        "move_pages": 239,
    },
}
_SELFTEST_ARCHITECTURE = {
    "x86_64": "x86_64",
    "amd64": "x86_64",
    "aarch64": "aarch64",
    "arm64": "aarch64",
}


def selftest_other_processes(libc: ctypes.CDLL) -> dict[str, bool]:
    """Try every call that could reach the server (this process's parent) on it. ``True`` = refused.

    Each call is made so that the kernel, if the filter let it through, does nothing: signal 0
    (a permission check), an invalid ``which`` or policy (``EINVAL``), a read-only ``prlimit64``,
    an empty node list. A refusal is ``EPERM`` from the filter. Then the same calls on this
    process itself (pid 0, or its own pid) must not be refused, so the filter isn't simply
    refusing the whole group.
    """
    machine = _SELFTEST_ARCHITECTURE.get(platform.machine().lower())
    if machine is None:
        return {"other_process_calls_refused": False}
    numbers = _SELFTEST_CALLS[machine]
    parent, me = os.getppid(), os.getpid()
    info = ctypes.create_string_buffer(128)
    struct.pack_into("iii", info, 0, 0, 0, -1)  # si_signo 0, si_errno 0, si_code SI_QUEUE
    old_limit = (ctypes.c_uint64 * 2)()
    info_at, old_at = ctypes.addressof(info), ctypes.addressof(old_limit)

    def call(name: str, *args: int) -> tuple[int, int]:
        result = libc.syscall(numbers[name], *(ctypes.c_long(a) for a in args))
        return result, ctypes.get_errno()

    on_parent = {
        "tkill": (parent, 0),
        "tgkill": (parent, parent, 0),
        "rt_sigqueueinfo": (parent, 0, info_at),
        "rt_tgsigqueueinfo": (parent, parent, 0, info_at),
        "pidfd_send_signal": (-1, 0, 0, 0),  # the kernel would say EBADF
        "prlimit64": (parent, 0, 0, old_at),  # RLIMIT_CPU, reading only
        "setpriority": (99, parent, 0),  # 99 is no "which"
        "ioprio_set": (99, parent, 0),
        "sched_setparam": (parent, 0),
        "sched_setscheduler": (parent, -1, 0),
        "sched_setaffinity": (parent, 0, 0),
        "sched_setattr": (parent, 0, 0),
        "migrate_pages": (parent, 0, 0, 0),
        "move_pages": (parent, 0, 0, 0, 0, 0),
    }
    outcome = {}
    for name, args in on_parent.items():
        result, error = call(name, *args)
        outcome[f"{name}_refused"] = result < 0 and error == errno.EPERM
    # The same calls on this process: the ones with a pid in front, with pid 0 and the own pid.
    own = {
        "prlimit64": lambda who: (who, 0, 0, old_at),
        "sched_setparam": lambda who: (who, 0),
        "sched_setscheduler": lambda who: (who, -1, 0),
        "sched_setaffinity": lambda who: (who, 0, 0),
        "sched_setattr": lambda who: (who, 0, 0),
        "migrate_pages": lambda who: (who, 0, 0, 0),
        "move_pages": lambda who: (who, 0, 0, 0, 0, 0),
    }
    outcome["own_process_calls_allowed"] = all(
        not (result < 0 and error == errno.EPERM)
        for name, args in own.items()
        for who in (0, me)
        for result, error in [call(name, *args(who))]
    )
    outcome["prlimit64_reads_own_limits"] = call("prlimit64", me, 0, 0, old_at)[0] == 0
    outcome["tgkill_to_self_works"] = call("tgkill", me, me, 0)[0] == 0
    return outcome


def selftest() -> dict[str, bool]:
    """What the sandbox must refuse, tried from inside: used by the tests and the release check.

    Every value is ``True`` when the thing was refused (or, for ``threads_work``, when threads
    still start), so one test is "all of them".
    """
    import socket
    import threading

    outcome = {}
    try:
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        outcome["python_socket_refused"] = False
    except PermissionError:
        outcome["python_socket_refused"] = True
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    fd = libc.socket(2, 1, 0)  # AF_INET, SOCK_STREAM, straight through libc
    outcome["libc_socket_refused"] = fd < 0 and ctypes.get_errno() == errno.EPERM
    pid_result = libc.execve(b"/bin/true", None, None)
    outcome["exec_refused"] = pid_result < 0 and ctypes.get_errno() == errno.EPERM
    params = ctypes.create_string_buffer(120)
    ring = libc.syscall(425, 1, params)  # io_uring_setup
    outcome["io_uring_refused"] = ring < 0 and ctypes.get_errno() == errno.EPERM
    try:
        child = os.fork()
        if child == 0:
            os._exit(0)
        os.waitpid(child, 0)
        outcome["fork_refused"] = False
    except OSError as exc:
        outcome["fork_refused"] = exc.errno == errno.EPERM
    vm_call = 310 if platform.machine().lower() in ("x86_64", "amd64") else 270  # process_vm_readv
    reading = libc.syscall(vm_call, os.getppid(), None, 0, None, 0, 0)
    outcome["process_vm_readv_refused"] = reading < 0 and ctypes.get_errno() == errno.EPERM
    # The server is this process's parent: no signal, no handle on it, nothing taken from it.
    try:
        os.kill(os.getppid(), 0)
        outcome["signal_to_parent_refused"] = False
    except PermissionError:
        outcome["signal_to_parent_refused"] = True
    pidfd = libc.syscall(434, os.getppid(), 0)  # pidfd_open
    outcome["pidfd_open_refused"] = pidfd < 0 and ctypes.get_errno() == errno.EPERM
    taken = libc.syscall(438, 0, 0, 0)  # pidfd_getfd
    outcome["pidfd_getfd_refused"] = taken < 0 and ctypes.get_errno() == errno.EPERM
    try:
        os.kill(os.getpid(), 0)
        outcome["signal_to_self_works"] = True
    except OSError:
        outcome["signal_to_self_works"] = False
    outcome.update(selftest_other_processes(libc))
    try:
        held = os.pread(_oom_score_fd, 16, 0).strip() if _oom_score_fd is not None else b""
    except OSError:
        held = b""
    outcome["oom_score_adj_is_1000"] = held == _OOM_SCORE_ADJ  # what the kernel holds
    started = []
    thread = threading.Thread(target=lambda: started.append(True))
    thread.start()
    thread.join(5)
    outcome["threads_work"] = started == [True]
    return outcome


def selftest_filesystem(folder: str) -> dict[str, bool]:
    """What the confined filesystem must refuse (``True`` = refused), tried from inside."""
    outcome = {}
    for name, test in (
        ("write_outside_refused", lambda: open(os.path.join(folder, "x"), "wb")),
        ("create_in_tmp_refused", lambda: open("/tmp/pdf-worker-probe", "wb")),
        ("read_etc_refused", lambda: open("/etc/passwd", "rb")),
        ("mkdir_refused", lambda: os.mkdir(os.path.join(folder, "d"))),
        ("read_cwd_listing_refused", lambda: os.listdir(folder)),
    ):
        try:
            handle = test()
            if hasattr(handle, "close"):
                handle.close()
            outcome[name] = False
        except OSError as exc:
            outcome[name] = exc.errno in (errno.EACCES, errno.EPERM)
    return outcome


def try_x32() -> None:
    """Make a call in the x32 numbering. The filter kills the process for it (x86-64 only)."""
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    libc.syscall(_X32_BIT | 39)  # getpid
