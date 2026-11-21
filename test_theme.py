import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from app import MainWindow, STYLE
from theme import install_light_theme
from update_ui import UpdateDialog


def test_dark_system_palette_cannot_make_update_dialog_or_dropdown_unreadable():
    app = QApplication.instance() or QApplication([])
    dark = QPalette()
    dark.setColor(QPalette.ColorRole.Window, QColor("#111111"))
    dark.setColor(QPalette.ColorRole.Base, QColor("#222222"))
    dark.setColor(QPalette.ColorRole.Text, QColor("#eeeeee"))
    app.setPalette(dark)
    install_light_theme(app)
    app.setStyleSheet(STYLE)
    window = MainWindow()
    dialog = UpdateDialog(window)
    window.show()
    dialog.show()
    app.processEvents()
    try:
        # Offscreen Qt reports Unknown; on Windows the explicit override is Light.
        assert app.styleHints().colorScheme() != Qt.ColorScheme.Dark
        assert dialog.palette().color(QPalette.ColorRole.Window).lightness() > 200
        assert dialog.notes.palette().color(QPalette.ColorRole.Base).lightness() > 200
        assert dialog.notes.palette().color(QPalette.ColorRole.Text).lightness() < 100
        view = window.image_mode.view()
        assert view.palette().color(QPalette.ColorRole.Base).lightness() > 200
        assert view.palette().color(QPalette.ColorRole.Text).lightness() < 100
        assert not window.windowIcon().isNull()
    finally:
        dialog.close()
        window.close()
