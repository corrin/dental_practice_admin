"""Windows child processes whose descendants cannot outlive their owner."""
from __future__ import annotations

import subprocess
import sys
from typing import Any

_JOB: Any = None


def own_process_tree() -> None:
    """Keep a kill-on-close Job Object handle for this worker's lifetime."""
    global _JOB
    if sys.platform != "win32" or _JOB is not None:
        return
    import win32api
    import win32job
    _JOB = win32job.CreateJobObject(None, "")
    info = win32job.QueryInformationJobObject(_JOB, win32job.JobObjectExtendedLimitInformation)
    info["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(_JOB, win32job.JobObjectExtendedLimitInformation, info)
    win32job.AssignProcessToJobObject(_JOB, win32api.GetCurrentProcess())
    # The OS closes this handle at exit; Python teardown would kill us before setting exit status.
    _JOB = _JOB.Detach()


if __name__ == "__main__":
    own_process_tree()
    raise SystemExit(subprocess.call(sys.argv[1:]))
