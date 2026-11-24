"""Small Linux Landlock sandbox for AppWorld agent-code workers."""

from __future__ import annotations

import ctypes
import os
from collections.abc import Iterable
from pathlib import Path

CREATE_RULESET = 444
ADD_RULE = 445
RESTRICT_SELF = 446
CREATE_VERSION = 1
RULE_PATH_BENEATH = 1
PR_SET_NO_NEW_PRIVS = 38

EXECUTE = 1 << 0
WRITE_FILE = 1 << 1
READ_FILE = 1 << 2
READ_DIR = 1 << 3
REMOVE_DIR = 1 << 4
REMOVE_FILE = 1 << 5
MAKE_CHAR = 1 << 6
MAKE_DIR = 1 << 7
MAKE_REG = 1 << 8
MAKE_SOCK = 1 << 9
MAKE_FIFO = 1 << 10
MAKE_BLOCK = 1 << 11
MAKE_SYM = 1 << 12
REFER = 1 << 13
TRUNCATE = 1 << 14

READ_ACCESS = EXECUTE | READ_FILE | READ_DIR
FILE_ACCESS = EXECUTE | WRITE_FILE | READ_FILE | TRUNCATE
BASE_WRITE_ACCESS = (
    READ_ACCESS
    | WRITE_FILE
    | REMOVE_DIR
    | REMOVE_FILE
    | MAKE_CHAR
    | MAKE_DIR
    | MAKE_REG
    | MAKE_SOCK
    | MAKE_FIFO
    | MAKE_BLOCK
    | MAKE_SYM
)


class RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class PathBeneathAttr(ctypes.Structure):
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]
    _pack_ = 1


def restrict_filesystem(
    read_paths: Iterable[str | Path],
    write_paths: Iterable[str | Path],
    *,
    denied_path: str | Path,
) -> int:
    """Allow only explicit paths and prove that denied_path cannot be opened."""
    libc = ctypes.CDLL(None, use_errno=True)
    abi = libc.syscall(CREATE_RULESET, 0, 0, CREATE_VERSION)
    if abi < 1:
        raise RuntimeError("Linux Landlock is unavailable")
    if abi >= 3:
        handled = (1 << 15) - 1
    elif abi == 2:
        handled = (1 << 14) - 1
    else:
        handled = (1 << 13) - 1
    ruleset = RulesetAttr(handled_access_fs=handled)
    ruleset_fd = libc.syscall(
        CREATE_RULESET, ctypes.byref(ruleset), ctypes.sizeof(ruleset), 0
    )
    if ruleset_fd < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    try:
        for path in read_paths:
            _add_rule(libc, ruleset_fd, Path(path), READ_ACCESS, handled)
        write_access = BASE_WRITE_ACCESS
        if abi >= 2:
            write_access |= REFER
        if abi >= 3:
            write_access |= TRUNCATE
        for path in write_paths:
            _add_rule(libc, ruleset_fd, Path(path), write_access, handled)
        if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
        if libc.syscall(RESTRICT_SELF, ruleset_fd, 0) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
    finally:
        os.close(ruleset_fd)
    denied = Path(denied_path)
    try:
        descriptor = os.open(denied, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except PermissionError:
        return abi
    else:
        os.close(descriptor)
        raise RuntimeError("Landlock private-data self-test failed")


def _add_rule(libc, ruleset_fd: int, path: Path, access: int, handled: int) -> None:
    access &= handled
    if not path.exists():
        return
    if not path.is_dir():
        access &= FILE_ACCESS
    flags = getattr(os, "O_PATH", 0) | getattr(os, "O_CLOEXEC", 0)
    parent_fd = os.open(path, flags)
    try:
        rule = PathBeneathAttr(allowed_access=access, parent_fd=parent_fd)
        result = libc.syscall(
            ADD_RULE,
            ruleset_fd,
            RULE_PATH_BENEATH,
            ctypes.byref(rule),
            0,
        )
        if result != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(path))
    finally:
        os.close(parent_fd)
