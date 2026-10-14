"""The worker's seccomp program, run in Python on both architectures' call numbers.

The kernel runs the filter on every system call the worker makes. These tests run the same
program through a small interpreter (the five instruction kinds it uses), so the arm64 numbers
are exercised on an x86 machine too, and so what the filter does with a pid can be checked for
every call that takes one, without making any of the calls.
"""

from __future__ import annotations

import errno
import os
import struct

import pytest

from skills.pdf._worker import lockdown

ALLOW = 0x7FFF0000
EPERM = 0x00050000 | errno.EPERM
ENOSYS = 0x00050000 | errno.ENOSYS
KILL = 0x80000000
X32 = 0x40000000

ARCHITECTURES = {"x86_64": lockdown._X86_64, "aarch64": lockdown._AARCH64}
# The calls that act on another process by its pid (round 3, R2-M3 and R3-M2).
SIGNAL_AND_HANDLE_CALLS = (
    "tkill",
    "rt_sigqueueinfo",
    "rt_tgsigqueueinfo",
    "pidfd_send_signal",
    "pidfd_open",
    "pidfd_getfd",
)
PID_FIRST_CALLS = (
    "prlimit64",
    "sched_setparam",
    "sched_setscheduler",
    "sched_setaffinity",
    "sched_setattr",
    "migrate_pages",
    "move_pages",
)
NEVER_CALLS = ("setpriority", "ioprio_set")


# Written out from the kernel's own tables (arch/x86/entry/syscalls/syscall_64.tbl and
# include/uapi/asm-generic/unistd.h, which arm64 uses), not from lockdown.py, so a wrong number in
# the filter's tables is caught here and not repeated.
KERNEL_NUMBERS = {
    "x86_64": {
        "socket": 41, "socketpair": 53, "fork": 57, "vfork": 58, "execve": 59, "ptrace": 101,
        "process_vm_readv": 310, "process_vm_writev": 311, "execveat": 322,
        "tkill": 200, "rt_sigqueueinfo": 129, "rt_tgsigqueueinfo": 297,
        "pidfd_send_signal": 424, "pidfd_open": 434, "pidfd_getfd": 438,
        "io_uring_setup": 425, "io_uring_enter": 426, "io_uring_register": 427,
        "setpriority": 141, "ioprio_set": 251,
        "prlimit64": 302, "sched_setparam": 142, "sched_setscheduler": 144,
        "sched_setaffinity": 203, "sched_setattr": 314, "migrate_pages": 256, "move_pages": 279,
        "clone": 56, "clone3": 435, "kill": 62, "tgkill": 234,
    },
    "aarch64": {
        "socket": 198, "socketpair": 199, "execve": 221, "ptrace": 117,
        "process_vm_readv": 270, "process_vm_writev": 271, "execveat": 281,
        "tkill": 130, "rt_sigqueueinfo": 138, "rt_tgsigqueueinfo": 240,
        "pidfd_send_signal": 424, "pidfd_open": 434, "pidfd_getfd": 438,
        "io_uring_setup": 425, "io_uring_enter": 426, "io_uring_register": 427,
        "setpriority": 140, "ioprio_set": 30,
        "prlimit64": 261, "sched_setparam": 118, "sched_setscheduler": 119,
        "sched_setaffinity": 122, "sched_setattr": 274, "migrate_pages": 238, "move_pages": 239,
        "clone": 220, "clone3": 435, "kill": 129, "tgkill": 131,
    },
}  # fmt: skip


def run_filter(program: bytes, arch: int, number: int, *args: int) -> int:
    """What the kernel would do with this call: the program's return value."""
    data = struct.pack("<IIQ6Q", number, arch, 0, *(list(args) + [0] * 6)[:6])
    accumulator = 0
    instructions = [struct.unpack_from("HBBI", program, i) for i in range(0, len(program), 8)]
    pc = 0
    while True:
        code, jump_true, jump_false, k = instructions[pc]
        pc += 1
        if code == lockdown._BPF_LD_W_ABS:
            accumulator = struct.unpack_from("<I", data, k)[0]
        elif code == lockdown._BPF_RET_K:
            return k
        elif code == lockdown._BPF_JEQ_K:
            pc += jump_true if accumulator == k else jump_false
        elif code == lockdown._BPF_JGE_K:
            pc += jump_true if accumulator >= k else jump_false
        elif code == lockdown._BPF_JSET_K:
            pc += jump_true if accumulator & k else jump_false
        else:
            raise AssertionError(f"an instruction the interpreter doesn't know: {code:#x}")


@pytest.fixture(params=sorted(ARCHITECTURES))
def machine(request: pytest.FixtureRequest) -> tuple[lockdown._Architecture, bytes]:
    entry = ARCHITECTURES[request.param]
    return entry, lockdown._program(entry)


@pytest.mark.parametrize("name", sorted(KERNEL_NUMBERS))
def test_the_filters_numbers_are_the_kernels(name: str) -> None:
    entry = ARCHITECTURES[name]
    ours = {
        **entry.refused,
        **entry.own_process_only,
        "clone": entry.clone,
        "clone3": entry.clone3,
        "kill": entry.kill,
        "tgkill": entry.tgkill,
    }
    assert ours == KERNEL_NUMBERS[name]


def test_every_call_number_in_a_table_is_used_once(machine) -> None:  # noqa: ANN001
    entry, _ = machine
    numbers = [*entry.refused.values(), *entry.own_process_only.values()]
    numbers += [entry.clone, entry.clone3, entry.kill, entry.tgkill]
    assert len(numbers) == len(set(numbers))


def test_the_tables_name_every_call_the_reviews_asked_for(machine) -> None:  # noqa: ANN001
    entry, _ = machine
    assert set(SIGNAL_AND_HANDLE_CALLS + NEVER_CALLS) <= set(entry.refused)
    assert set(entry.own_process_only) == set(PID_FIRST_CALLS)


def test_a_foreign_architecture_and_an_x32_number_kill_the_worker(machine) -> None:  # noqa: ANN001
    entry, program = machine
    assert run_filter(program, 0x12345678, 39) == KILL
    if entry.has_x32:
        assert run_filter(program, entry.audit, X32 | 39) == KILL
        assert run_filter(program, entry.audit, X32 | entry.refused["socket"]) == KILL


def test_signals_and_handles_on_any_process_are_refused(machine) -> None:  # noqa: ANN001
    entry, program = machine
    for name in SIGNAL_AND_HANDLE_CALLS + NEVER_CALLS:
        for target in (0, 1, os.getpid(), os.getppid()):
            assert run_filter(program, entry.audit, entry.refused[name], target, target) == EPERM, (
                name
            )


def test_kill_and_tgkill_are_allowed_only_on_the_workers_own_pid(machine) -> None:  # noqa: ANN001
    entry, program = machine
    me = os.getpid()
    for number in (entry.kill, entry.tgkill):
        assert run_filter(program, entry.audit, number, me, me, 15) == ALLOW
        for other in (0, 1, os.getppid(), me + 1, 0xFFFFFFFF):  # 0xFFFFFFFF is -1: everyone
            assert run_filter(program, entry.audit, number, other, other, 15) == EPERM


def test_calls_that_change_a_process_are_allowed_only_on_pid_zero_or_the_workers_own(
    machine,  # noqa: ANN001
) -> None:
    entry, program = machine
    me = os.getpid()
    for name, number in entry.own_process_only.items():
        for own in (0, me):
            assert run_filter(program, entry.audit, number, own, 0, 0) == ALLOW, (name, own)
        for other in (1, os.getppid(), me + 1, 0xFFFFFFFF):
            assert run_filter(program, entry.audit, number, other, 0, 0) == EPERM, (name, other)


def test_only_the_low_32_bits_of_a_pid_count_as_the_kernel_reads_a_pid_t(machine) -> None:  # noqa: ANN001
    entry, program = machine
    me = os.getpid()
    number = entry.own_process_only["prlimit64"]
    # The kernel truncates the argument to an int: the high half is ignored, here and there.
    assert run_filter(program, entry.audit, number, (7 << 32) | me) == ALLOW
    assert run_filter(program, entry.audit, number, (me << 32) | os.getppid()) == EPERM


def test_threads_still_start_but_processes_do_not(machine) -> None:  # noqa: ANN001
    entry, program = machine
    thread_flags = 0x00010000 | 0x00000100  # CLONE_THREAD | CLONE_VM
    assert run_filter(program, entry.audit, entry.clone, thread_flags) == ALLOW
    assert run_filter(program, entry.audit, entry.clone, 0x00000100) == EPERM
    assert run_filter(program, entry.audit, entry.clone3) == ENOSYS


def test_an_ordinary_call_is_allowed(machine) -> None:  # noqa: ANN001
    entry, program = machine
    assert run_filter(program, entry.audit, 0, 3, 0, 0) == ALLOW  # read (x86-64) / io_setup
