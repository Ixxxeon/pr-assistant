#!/usr/bin/env python3
"""locked.py <lock-file> <command...>: run the command holding an exclusive lock.
Parallel reviews of PRs from one repository share its clone; git fetch, clone and
worktree add must not run there concurrently."""
import fcntl
import os
import subprocess
import sys

os.makedirs(os.path.dirname(sys.argv[1]), exist_ok=True)
with open(sys.argv[1], "w") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    sys.exit(subprocess.call(sys.argv[2:]))
