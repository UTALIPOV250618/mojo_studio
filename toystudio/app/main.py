"""ToyStudio entry point: single instance, dark theme by default."""

from __future__ import annotations

import getpass
import logging
import sys

from PySide6.QtCore import Qt
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from toystudio.app.shell import Shell
from toystudio.app.theme.apply import apply_theme, load_fonts

log = logging.getLogger(__name__)


def _server_name() -> str:
    return f"ToyStudio-{getpass.getuser()}"


def _signal_running_instance(name: str) -> bool:
    sock = QLocalSocket()
    sock.connectToServer(name)
    if not sock.waitForConnected(300):
        return False
    sock.write(b"raise")
    sock.flush()
    sock.waitForBytesWritten(300)
    sock.disconnectFromServer()
    return True


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    app = QApplication(sys.argv)
    name = _server_name()
    if _signal_running_instance(name):
        return 0
    QLocalServer.removeServer(name)  # stale socket from a crashed run
    server = QLocalServer()
    server.listen(name)
    load_fonts()
    apply_theme(app, "dark")
    win = Shell()

    def raise_window() -> None:
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.close()
        win.setWindowState(win.windowState() & ~Qt.WindowState.WindowMinimized)
        win.show()
        win.raise_()
        win.activateWindow()

    server.newConnection.connect(raise_window)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
