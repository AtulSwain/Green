"""Qt application start-up and shutdown."""

from __future__ import annotations

import logging
import sys

from .. import __app_name__, __version__
from ..database.migrations import DatabaseInitError
from .bootstrap import build_services

log = logging.getLogger(__name__)


def run(argv: list[str] | None = None) -> int:
    from PySide6.QtGui import QFont  # noqa: PLC0415
    from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: PLC0415

    from ..ui.main_window import MainWindow  # noqa: PLC0415
    from ..ui.styles import ui_font_family  # noqa: PLC0415

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(__app_name__)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(__app_name__)
    family = ui_font_family()
    if family:
        app.setFont(QFont(family, 10))

    try:
        services = build_services()
    except DatabaseInitError as exc:
        QMessageBox.critical(None, "GREEN cannot start", str(exc))
        return 2
    except OSError as exc:
        QMessageBox.critical(None, "GREEN cannot start", f"Could not prepare the data folder: {exc}")
        return 2

    app.setQuitOnLastWindowClosed(False)  # GREEN may keep running in the tray; quitting is explicit
    window = MainWindow(services)
    window.quit_app_on_close = True
    window.setWindowIcon(window.app_icon)
    app.setWindowIcon(window.app_icon)
    window.show()
    if services.startup_notices:
        QMessageBox.information(window, "GREEN", "\n\n".join(services.startup_notices))
    try:
        code = app.exec()
    finally:
        window.shutdown()
        services.shutdown()
    log.info("GREEN exited with code %s", code)
    return code
