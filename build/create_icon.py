"""Render the original application SVG as a multiresolution Windows icon."""
import os
from pathlib import Path
import struct
import sys


def create_icon(source, destination):
    if sys.platform == 'win32':
        dll_handle = os.add_dll_directory(str(Path(sys.prefix)/'Library/bin'))
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(str(source))
    if not renderer.isValid():
        raise ValueError('Logo SVG invalide : ' + str(source))
    sizes = (16, 24, 32, 48, 64, 128, 256)
    images = []
    for size in sizes:
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            renderer.render(painter)
        finally:
            painter.end()
        data = QByteArray()
        buffer = QBuffer(data)
        if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not image.save(buffer, 'PNG'):
            raise RuntimeError('Encodage de l’icône Windows impossible.')
        buffer.close()
        images.append(bytes(data))
    directory, offset = [], 6 + 16*len(sizes)
    for size, payload in zip(sizes, images):
        directory.append(struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(payload), offset))
        offset += len(payload)
    Path(destination).write_bytes(struct.pack('<HHH', 0, 1, len(sizes)) + b''.join(directory) + b''.join(images))
    print('Icône Windows : ' + ', '.join(str(size) for size in sizes) + ' pixels.', flush=True)


if __name__ == '__main__':
    create_icon(Path(sys.argv[1]), Path(sys.argv[2]))
