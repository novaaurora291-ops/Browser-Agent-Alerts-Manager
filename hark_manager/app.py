import sys

from PySide6.QtCore import Qt
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from .config import APP_NAME, APP_TITLE, ensure_extension, log
from .core import Controller
from .ui.main_window import MainWindow


def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setQuitOnLastWindowClosed(False)
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Hark.BrowserManager")
    except Exception:
        pass

    # single instance: a second launch just brings the first window up (prevents duplicate alerts)
    sock = QLocalSocket()
    sock.connectToServer(APP_NAME)
    if sock.waitForConnected(400):
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(400)
        return 0
    QLocalServer.removeServer(APP_NAME)
    server = QLocalServer()
    server.listen(APP_NAME)

    ensure_extension()
    ctl = Controller()
    win = MainWindow(ctl)
    server.newConnection.connect(lambda: (server.nextPendingConnection(), win.show_normal()))
    if "--minimized" in sys.argv:
        win.hide()
    else:
        win.show()
    log("started")
    app.aboutToQuit.connect(lambda: log("about to quit"))
    rc = app.exec()
    log(f"exit rc={rc}")
    return rc
