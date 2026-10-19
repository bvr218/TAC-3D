"""TAC 3D - aplicacion de escritorio: importar, validar, procesar y ver tomografias en 3D."""
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QLockFile, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

import PySide6.QtWebEngineWidgets  # noqa: F401  (debe cargarse antes de crear la QApplication)

import config


def excepthook(kind, value, tb):
    txt = "".join(traceback.format_exception(kind, value, tb))
    with open(os.path.join(config.LOGS, "errores.log"), "a", encoding="utf-8") as f:
        f.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}]\n{txt}")
    if QApplication.instance():
        QMessageBox.critical(None, config.APP_NAME, f"Error inesperado:\n{value}\n\nDetalles en {config.LOGS}\\errores.log")


def main():
    sys.excepthook = excepthook
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--ignore-gpu-blocklist")
    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_NAME)
    ico = os.path.join(config.APP_RES, "tac3d.ico")
    if os.path.exists(ico):
        app.setWindowIcon(QIcon(ico))

    lock = QLockFile(os.path.join(config.DATA, "app.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, config.APP_NAME, "TAC 3D ya esta abierto.")
        return 0

    from servidor import Server
    from trabajos import Runner
    from ventana import MainWindow

    cfg = config.Config()
    cfg.save()
    win = MainWindow(cfg, Server(cfg), Runner(cfg))
    win.show()
    args = [a for a in sys.argv[1:] if os.path.exists(a)]
    if args:
        win.add_study(args[0])
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
