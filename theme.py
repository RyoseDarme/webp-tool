"""An explicit light palette, including popup windows under Windows dark mode."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette


def install_light_theme(app):
    app.setStyle("Fusion")
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    palette = QPalette()
    colors = {
        "Window": "#f5f7fa", "WindowText": "#243744", "Base": "#ffffff",
        "AlternateBase": "#f4f7fa", "Text": "#243744", "Button": "#ffffff",
        "ButtonText": "#243744", "BrightText": "#ffffff", "Highlight": "#0a8674",
        "HighlightedText": "#ffffff", "ToolTipBase": "#ffffff", "ToolTipText": "#243744",
        "PlaceholderText": "#647785", "Link": "#087f70", "Light": "#ffffff",
        "Midlight": "#edf1f5", "Mid": "#c4d0da", "Dark": "#8c9caa", "Shadow": "#536370",
    }
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled):
        for name, color in colors.items():
            palette.setColor(group, getattr(QPalette.ColorRole, name), QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#7d8b97"))
    app.setPalette(palette)


POPUP_STYLE = """
QDialog, QMessageBox, QFileDialog { background-color: #f5f7fa; color: #243744; }
QTextEdit, QPlainTextEdit { background-color: #ffffff; color: #243744;
    border: 1px solid #d3dee7; border-radius: 7px; padding: 9px;
    selection-background-color: #0a8674; selection-color: #ffffff; }
QLineEdit { color: #243744; selection-background-color: #0a8674; selection-color: #ffffff; }
QComboBox QAbstractItemView { background-color: #ffffff; color: #243744;
    selection-background-color: #dcefe8; selection-color: #174b42;
    border: 1px solid #b7cbc4; outline: none; padding: 4px; }
QComboBox QAbstractItemView::item { min-height: 32px; padding: 4px 8px; }
QMenu { background-color: #ffffff; color: #243744; border: 1px solid #d3dee7; }
QMenu::item:selected { background-color: #dcefe8; color: #174b42; }
QCheckBox::indicator { width: 15px; height: 15px; border: 1px solid #a4b4bf;
    background: #ffffff; border-radius: 3px; }
QCheckBox::indicator:checked { background: #0a8674; border-color: #0a8674; }
QPushButton:focus, QComboBox:focus, QLineEdit:focus { border-color: #0a8674; }
"""
