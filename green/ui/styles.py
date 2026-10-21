"""Central design tokens and the Qt stylesheet built from them.

All colours, spacing and typography live here; widgets refer to object names
and dynamic properties instead of hard-coding style values.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    name: str
    bg: str
    surface: str
    surface_alt: str
    sidebar: str
    border: str
    text: str
    text_muted: str
    accent: str  # background for primary buttons (white text)
    accent_hover: str
    accent_fg: str  # accent used as text / indicator colour
    accent_soft: str  # subtle accent tint for selection
    user_bubble: str
    danger: str
    warning: str
    success: str
    code_bg: str


DARK = Theme(
    name="dark",
    bg="#0f1112",
    surface="#16191b",
    surface_alt="#1d2124",
    sidebar="#121415",
    border="#2a2f33",
    text="#e7e9ea",
    text_muted="#9ba4a9",
    accent="#25805a",
    accent_hover="#2c9468",
    accent_fg="#5ec597",
    accent_soft="#1b3229",
    user_bubble="#1d2a25",
    danger="#e07070",
    warning="#d9ad55",
    success="#5ec597",
    code_bg="#0b0d0e",
)

LIGHT = Theme(
    name="light",
    bg="#f5f6f6",
    surface="#ffffff",
    surface_alt="#eef0f0",
    sidebar="#eceeee",
    border="#d5d9db",
    text="#1a1e21",
    text_muted="#59636a",
    accent="#1d7650",
    accent_hover="#186543",
    accent_fg="#1d7650",
    accent_soft="#d9ece3",
    user_bubble="#e3f0ea",
    danger="#b3261e",
    warning="#8a6100",
    success="#1d7650",
    code_bg="#f0f2f2",
)

SPACING = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24}
RADIUS = 6
FONT_FAMILY = '"Segoe UI Variable Text", "Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif'
MONO_FAMILY = '"Cascadia Mono", "Consolas", "DejaVu Sans Mono", monospace'


def theme_for(name: str) -> Theme:
    return LIGHT if name == "light" else DARK


def ui_font_family() -> str:
    return "Segoe UI" if sys.platform == "win32" else ""


def build_stylesheet(t: Theme, font_pt: int = 10) -> str:
    s = SPACING
    return f"""
* {{ font-family: {FONT_FAMILY}; font-size: {font_pt}pt; }}
QMainWindow, QDialog, QWidget#Page, QStackedWidget {{ background: {t.bg}; color: {t.text}; }}
QWidget {{ color: {t.text}; }}
QToolTip {{ background: {t.surface_alt}; color: {t.text}; border: 1px solid {t.border}; padding: {s['xs']}px; }}

QFrame#Sidebar {{ background: {t.sidebar}; border-right: 1px solid {t.border}; }}
QLabel#Brand {{ font-size: {font_pt + 6}pt; font-weight: 700; letter-spacing: 3px; color: {t.accent_fg}; }}
QLabel#BrandSub {{ color: {t.text_muted}; font-size: {font_pt - 1}pt; }}
QLabel#SectionLabel {{ color: {t.text_muted}; font-size: {font_pt - 1}pt; font-weight: 600;
    letter-spacing: 1px; padding-top: {s['sm']}px; }}
QLabel#PageTitle {{ font-size: {font_pt + 5}pt; font-weight: 600; }}
QLabel#Muted, QLabel[muted="true"] {{ color: {t.text_muted}; }}
QLabel#ErrorText {{ color: {t.danger}; }}
QLabel#WarningText {{ color: {t.warning}; }}
QLabel#Mono {{ font-family: {MONO_FAMILY}; }}

QPushButton {{ background: {t.surface_alt}; border: 1px solid {t.border}; border-radius: {RADIUS}px;
    padding: {s['xs'] + 2}px {s['md']}px; color: {t.text}; }}
QPushButton:hover {{ border-color: {t.accent_fg}; }}
QPushButton:pressed {{ background: {t.accent_soft}; }}
QPushButton:disabled {{ color: {t.text_muted}; border-color: {t.border}; background: {t.surface}; }}
QPushButton:focus {{ outline: none; border: 1px solid {t.accent_fg}; }}
QPushButton#PrimaryButton {{ background: {t.accent}; border-color: {t.accent}; color: #ffffff; font-weight: 600; }}
QPushButton#PrimaryButton:hover {{ background: {t.accent_hover}; }}
QPushButton#PrimaryButton:disabled {{ background: {t.surface_alt}; border-color: {t.border}; color: {t.text_muted}; }}
QPushButton#DangerButton {{ color: {t.danger}; }}
QPushButton#DangerButton:hover {{ border-color: {t.danger}; }}
QPushButton#NavButton {{ background: transparent; border: none; text-align: left; padding: {s['sm']}px {s['md']}px;
    border-radius: {RADIUS}px; color: {t.text_muted}; }}
QPushButton#NavButton:hover {{ background: {t.surface_alt}; color: {t.text}; }}
QPushButton#NavButton:checked {{ background: {t.accent_soft}; color: {t.text}; font-weight: 600; }}
QPushButton#NavButton:focus {{ border: 1px solid {t.accent_fg}; }}
QPushButton#LinkButton {{ background: transparent; border: none; color: {t.text_muted}; padding: 2px {s['xs']}px; }}
QPushButton#LinkButton:hover {{ color: {t.accent_fg}; }}
QPushButton#LinkButton:focus {{ border: 1px solid {t.accent_fg}; }}
QPushButton#MicButton[recording="true"] {{ background: {t.danger}; border-color: {t.danger}; color: #ffffff; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateTimeEdit {{
    background: {t.surface}; border: 1px solid {t.border}; border-radius: {RADIUS}px; padding: {s['xs'] + 1}px {s['sm']}px;
    selection-background-color: {t.accent}; selection-color: #ffffff; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QComboBox:focus, QDateTimeEdit:focus {{ border-color: {t.accent_fg}; }}
QLineEdit:disabled, QPlainTextEdit:disabled {{ color: {t.text_muted}; }}
QPlainTextEdit#Composer {{ background: {t.surface}; padding: {s['sm']}px; }}
QPlainTextEdit#CodeView, QPlainTextEdit#DiffView {{ font-family: {MONO_FAMILY}; background: {t.code_bg}; }}
QComboBox QAbstractItemView {{ background: {t.surface}; border: 1px solid {t.border}; selection-background-color: {t.accent_soft}; }}

QListWidget, QTreeWidget, QTableWidget {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: {RADIUS}px;
    outline: none; alternate-background-color: {t.surface_alt}; gridline-color: {t.border}; }}
QListWidget#ConversationList {{ background: transparent; border: none; }}
QListWidget::item, QTreeWidget::item {{ padding: {s['sm'] - 2}px {s['sm']}px; border-radius: {RADIUS - 2}px; }}
QListWidget::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected {{ background: {t.accent_soft}; color: {t.text}; }}
QListWidget::item:hover, QTreeWidget::item:hover {{ background: {t.surface_alt}; }}
QHeaderView::section {{ background: {t.surface_alt}; color: {t.text_muted}; border: none; border-bottom: 1px solid {t.border};
    padding: {s['xs']}px {s['sm']}px; }}

QCheckBox {{ spacing: {s['sm']}px; }}
QCheckBox::indicator, QTableWidget::indicator, QListWidget::indicator {{ width: 14px; height: 14px;
    border: 1px solid {t.text_muted}; border-radius: 3px; background: {t.surface}; }}
QCheckBox::indicator:checked, QTableWidget::indicator:checked, QListWidget::indicator:checked {{
    background: {t.accent_fg}; border-color: {t.accent_fg}; }}
QCheckBox::indicator:focus {{ border-color: {t.accent_fg}; }}
QCheckBox:focus {{ color: {t.accent_fg}; }}
QGroupBox {{ border: 1px solid {t.border}; border-radius: {RADIUS}px; margin-top: {s['lg']}px; padding: {s['md']}px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: {s['sm']}px; padding: 0 {s['xs']}px; color: {t.text_muted}; }}
QTabWidget::pane {{ border: 1px solid {t.border}; border-radius: {RADIUS}px; }}
QTabBar::tab {{ background: transparent; padding: {s['sm']}px {s['md']}px; color: {t.text_muted}; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {t.text}; border-bottom: 2px solid {t.accent_fg}; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget#MessageContainer {{ background: {t.bg}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {t.border}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {t.text_muted}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {t.border}; border-radius: 4px; min-width: 30px; }}

QFrame#MessageUser {{ background: {t.user_bubble}; border-radius: {RADIUS + 2}px; }}
QFrame#MessageAssistant {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: {RADIUS + 2}px; }}
QLabel#MessageRole {{ color: {t.text_muted}; font-weight: 600; font-size: {font_pt - 1}pt; }}
QLabel#MessageBody {{ background: transparent; }}
QLabel#MessageMeta {{ color: {t.text_muted}; font-size: {font_pt - 1}pt; }}
QLabel#MessageMeta[state="error"] {{ color: {t.danger}; }}
QLabel#MessageMeta[state="cancelled"] {{ color: {t.warning}; }}

QPushButton#QuickAction {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: {RADIUS + 2}px;
    padding: {s['md']}px; text-align: left; color: {t.text}; }}
QPushButton#QuickAction:hover {{ border-color: {t.accent_fg}; background: {t.surface_alt}; }}
QFrame#CodeBlock {{ background: {t.code_bg}; border: 1px solid {t.border}; border-radius: {RADIUS}px; }}
QLabel#CodeLanguage {{ color: {t.text_muted}; font-size: {font_pt - 1}pt; }}
QLabel#CodeText {{ font-family: {MONO_FAMILY}; background: transparent; }}
QProgressBar#LevelMeter {{ border: none; border-radius: 4px; background: {t.surface_alt}; height: 8px; }}
QProgressBar#LevelMeter::chunk {{ background: {t.accent_fg}; border-radius: 4px; }}
QLineEdit#SidebarSearch {{ background: {t.surface}; }}
QSlider {{ min-height: 20px; max-height: 20px; background: transparent; }}
QSlider::groove:horizontal {{ height: 4px; background: {t.border}; border-radius: 2px; margin: 0; }}
QSlider::sub-page:horizontal {{ height: 4px; background: {t.accent_fg}; border-radius: 2px; }}
QSlider::add-page:horizontal {{ height: 4px; background: {t.border}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {t.text}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
QSlider::handle:horizontal:hover {{ background: {t.accent_fg}; }}

QLabel#ToolStep {{ color: {t.text_muted}; font-size: {font_pt - 1}pt; padding: 1px 0; }}
QLabel#ToolStep[state="running"] {{ color: {t.text}; }}
QLabel#ToolStep[state="waiting"] {{ color: {t.warning}; }}
QLabel#ToolStep[state="ok"] {{ color: {t.success}; }}
QLabel#ToolStep[state="failed"] {{ color: {t.danger}; }}

QFrame#Banner {{ border-radius: {RADIUS}px; border: 1px solid {t.border}; background: {t.surface}; }}
QFrame#Banner[kind="error"] {{ border-color: {t.danger}; }}
QFrame#Banner[kind="warning"] {{ border-color: {t.warning}; }}
QFrame#Banner[kind="info"] {{ border-color: {t.accent_fg}; }}
QFrame#Card {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: {RADIUS}px; }}
QFrame#ComposerBar {{ background: {t.surface}; border-top: 1px solid {t.border}; }}

QLabel#StatusDot {{ border-radius: 5px; min-width: 10px; max-width: 10px; min-height: 10px; max-height: 10px; background: {t.text_muted}; }}
QLabel#StatusDot[state="ok"] {{ background: {t.success}; }}
QLabel#StatusDot[state="warning"] {{ background: {t.warning}; }}
QLabel#StatusDot[state="error"] {{ background: {t.danger}; }}
QStatusBar {{ background: {t.sidebar}; border-top: 1px solid {t.border}; color: {t.text_muted}; }}
QStatusBar QLabel {{ color: {t.text_muted}; }}
QProgressBar {{ border: 1px solid {t.border}; border-radius: {RADIUS}px; background: {t.surface}; text-align: center; height: 14px; }}
QProgressBar::chunk {{ background: {t.accent}; border-radius: {RADIUS - 1}px; }}
QMenu {{ background: {t.surface}; border: 1px solid {t.border}; padding: {s['xs']}px; }}
QMenu::item {{ padding: {s['xs'] + 2}px {s['lg']}px; border-radius: {RADIUS - 2}px; }}
QMenu::item:selected {{ background: {t.accent_soft}; }}
"""


def repolish(widget) -> None:
    """Re-apply the stylesheet after changing a dynamic property."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
