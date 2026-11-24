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
        sock.write(b"quit" if "--quit" in sys.argv else b"show")
        sock.flush()
        sock.waitForBytesWritten(400)
        return 0
    QLocalServer.removeServer(APP_NAME)
    server = QLocalServer()
    server.listen(APP_NAME)

    ensure_extension()
    ctl = Controller()
    from .ui import style
    style.apply_theme(ctl.settings["theme"] or "light")     # the saved theme, before any window is built
    win = MainWindow(ctl)
    def on_conn():
        c = server.nextPendingConnection()

        def read():
            # "quit" (python run.py --quit): close cleanly, every docked Cent window goes back to the desktop first
            if bytes(c.readAll().data()).startswith(b"quit"):
                log("quit requested by a second launch (--quit)")
                win.quit()
            else:
                win.show_normal()
        c.readyRead.connect(read)
        if c.bytesAvailable():
            read()
    server.newConnection.connect(on_conn)
    if "--minimized" in sys.argv:
        win.hide()
    else:
        win.show()
    log("started")
    return app.exec()
