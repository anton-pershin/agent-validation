"""Linux child-subreaper used to contain solution and provisioning processes."""

from __future__ import annotations

import ctypes
import os
import signal
import struct
import subprocess
import sys

_PR_SET_CHILD_SUBREAPER = 36


def _restore_child_signals() -> None:
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.SIG_DFL)


def main() -> None:
    status_fd = int(os.environ["AGENT_VALIDATION_STATUS_FD"])
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.restype = ctypes.c_int
    if libc.prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        os.write(status_fd, struct.pack("i", -200))
        return
    try:
        child = subprocess.Popen(sys.argv[1:], preexec_fn=_restore_child_signals)
    except OSError:
        os.write(status_fd, struct.pack("i", -127))
        child = None
    for descriptor in (0, 1, 2):
        try:
            os.close(descriptor)
        except OSError:
            pass
    returncode = child.wait() if child is not None else -127
    os.write(status_fd, struct.pack("i", returncode))
    os.close(status_fd)
    while True:
        signal.pause()


if __name__ == "__main__":
    main()
