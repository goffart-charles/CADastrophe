"""Start the installed application with its bundled native runtime."""
import os
from pathlib import Path
import sys


def main():
    if sys.platform == 'win32':
        dll_handle = os.add_dll_directory(str(Path(sys.prefix)/'Library/bin'))
    from cadastrophe.app import run
    return run()


if __name__ == '__main__':
    raise SystemExit(main())
