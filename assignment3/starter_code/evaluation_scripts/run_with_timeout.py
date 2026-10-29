"""Portable command timeout for macOS hosts (no GNU timeout dependency)."""
import os
import signal
import subprocess
import sys


def main():
    proc = subprocess.Popen(sys.argv[2:], start_new_session=True)
    try:
        return proc.wait(timeout=float(sys.argv[1]))
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        return 124


if __name__ == "__main__":
    sys.exit(main())
