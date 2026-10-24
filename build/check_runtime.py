"""Fail before packing a runtime that cannot load the actual native libraries."""
import os
from pathlib import Path
import struct
import sys

if sys.platform != 'win32' or sys.version_info[:2] != (3, 12) or struct.calcsize('P') != 8:
    raise SystemExit('Windows x64 et Python 3.12 requis.')
handle = os.add_dll_directory(str(Path(sys.prefix)/'Library/bin'))
from OCC.Core import BRep, V3d
from PySide6 import QtCore, QtWidgets
import numpy

print('Runtime natif x64 prêt :', sys.version.split()[0], 'Qt', QtCore.qVersion())
