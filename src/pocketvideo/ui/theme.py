"""Cohesive Dark and Light QSS design system for PocketVideo."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThemePalette:
    name: str
    bg_primary: str
    bg_secondary: str
    bg_card: str
    bg_card_selected: str
    border_subtle: str
    border_accent: str
    text_primary: str
    text_secondary: str
    text_muted: str
    accent_primary: str
    accent_hover: str
    success: str
    warning: str
    danger: str


DARK_PALETTE = ThemePalette(
    name="dark",
    bg_primary="#0F172A",
    bg_secondary="#1E293B",
    bg_card="#1E293B",
    bg_card_selected="#172554",
    border_subtle="#334155",
    border_accent="#38BDF8",
    text_primary="#F8FAFC",
    text_secondary="#CBD5E1",
    text_muted="#94A3B8",
    accent_primary="#0EA5E9",
    accent_hover="#0284C7",
    success="#10B981",
    warning="#F59E0B",
    danger="#EF4444",
)

LIGHT_PALETTE = ThemePalette(
    name="light",
    bg_primary="#F8FAFC",
    bg_secondary="#F1F5F9",
    bg_card="#FFFFFF",
    bg_card_selected="#EFF6FF",
    border_subtle="#CBD5E1",
    border_accent="#0284C7",
    text_primary="#0F172A",
    text_secondary="#334155",
    text_muted="#64748B",
    accent_primary="#0284C7",
    accent_hover="#0369A1",
    success="#059669",
    warning="#D97706",
    danger="#DC2626",
)


def build_stylesheet(theme_name: str = "dark") -> str:
    """Generate Qt Style Sheet (QSS) for the given theme ('dark' or 'light')."""
    p = DARK_PALETTE if theme_name == "dark" else LIGHT_PALETTE
    return f"""
    /* Scope opaque surfaces to real page containers. Qt 6 renders every
       QLabel as a tiny opaque rectangle if background-color is assigned to
       all QWidgets, causing the dark strips visible in older PocketVideo. */
    QMainWindow, QWidget#RootContainer, QStackedWidget, QScrollArea,
    QWidget#DashboardPage, QScrollArea > QWidget > QWidget {{
        background-color: {p.bg_primary};
        color: {p.text_primary};
        font-family: "Leelawadee UI", "Tahoma", "Segoe UI", sans-serif;
        font-size: 13px;
    }}
    QLabel {{
        background-color: transparent;
        color: {p.text_primary};
        font-family: "Leelawadee UI", "Tahoma", "Segoe UI", sans-serif;
    }}
    QScrollArea {{
        border: none;
    }}
    QGraphicsView {{
        background-color: {p.bg_card};
        border: 1px solid {p.border_subtle};
        border-radius: 8px;
    }}
    QToolTip {{
        background-color: {p.bg_secondary};
        color: {p.text_primary};
        border: 1px solid {p.border_subtle};
        padding: 6px;
    }}
    QTabWidget::pane {{
        border: 1px solid {p.border_subtle};
        background-color: {p.bg_primary};
        border-radius: 8px;
    }}
    QTabBar::tab {{
        background-color: {p.bg_secondary};
        color: {p.text_secondary};
        border: 1px solid {p.border_subtle};
        padding: 9px 16px;
        min-width: 108px;
    }}
    QTabBar::tab:selected {{
        background-color: {p.bg_card_selected};
        color: {p.text_primary};
        border-bottom: 2px solid {p.border_accent};
    }}
    QTableWidget, QTableView {{
        background-color: {p.bg_card};
        alternate-background-color: {p.bg_secondary};
        color: {p.text_primary};
        gridline-color: {p.border_subtle};
        border: 1px solid {p.border_subtle};
        selection-background-color: {p.bg_card_selected};
        selection-color: {p.text_primary};
    }}
    QHeaderView::section {{
        background-color: {p.bg_secondary};
        color: {p.text_primary};
        border: 1px solid {p.border_subtle};
        padding: 6px;
        font-weight: 600;
    }}
    QCheckBox {{
        color: {p.text_primary};
        spacing: 6px;
    }}
    QFrame#SidebarFrame {{
        background-color: {p.bg_secondary};
        border-right: 1px solid {p.border_subtle};
    }}
    QFrame#CardFrame, QFrame#DropZoneFrame {{
        background-color: {p.bg_card};
        border: 1px solid {p.border_subtle};
        border-radius: 12px;
    }}
    QFrame#DropZoneFrame {{
        border: 2px dashed {p.border_accent};
        border-radius: 14px;
    }}
    QFrame#PresetCard {{
        background-color: {p.bg_card};
        border: 2px solid {p.border_subtle};
        border-radius: 12px;
    }}
    QFrame#PresetCard[selected="true"] {{
        background-color: {p.bg_card_selected};
        border: 2px solid {p.border_accent};
    }}
    QLabel#HeadingLabel {{
        font-size: 18px;
        font-weight: 700;
        color: {p.text_primary};
    }}
    QLabel#SubheadingLabel {{
        font-size: 13px;
        color: {p.text_secondary};
    }}
    QLabel#MutedLabel {{
        font-size: 12px;
        color: {p.text_muted};
    }}
    QLabel#BadgeLabel {{
        background-color: {p.accent_primary};
        color: #FFFFFF;
        font-size: 11px;
        font-weight: 600;
        padding: 3px 8px;
        border-radius: 6px;
    }}
    QLabel#WarningBanner {{
        background-color: {p.bg_secondary};
        border-left: 4px solid {p.warning};
        color: {p.text_primary};
        padding: 8px 12px;
        border-radius: 6px;
    }}
    QPushButton {{
        background-color: {p.bg_secondary};
        color: {p.text_primary};
        border: 1px solid {p.border_subtle};
        border-radius: 8px;
        padding: 7px 10px;
        font-weight: 600;
    }}
    QPushButton:hover {{
        border-color: {p.border_accent};
    }}
    QPushButton:disabled, QToolButton:disabled {{
        color: {p.text_muted};
        background-color: {p.bg_secondary};
    }}
    QPushButton:focus, QToolButton:focus {{
        border: 1px solid {p.border_accent};
    }}
    QToolButton {{
        background-color: {p.bg_secondary};
        color: {p.text_primary};
        border: 1px solid {p.border_subtle};
        border-radius: 7px;
        padding: 5px 7px;
    }}
    QToolButton:hover {{
        border-color: {p.border_accent};
        background-color: {p.bg_card_selected};
    }}
    QToolButton::menu-indicator {{
        image: none;
        width: 0px;
    }}
    QMenu {{
        background-color: {p.bg_card};
        color: {p.text_primary};
        border: 1px solid {p.border_subtle};
        border-radius: 8px;
        padding: 6px;
    }}
    QMenu::item {{
        padding: 7px 18px 7px 10px;
        border-radius: 5px;
    }}
    QMenu::item:selected {{
        background-color: {p.bg_card_selected};
        color: {p.text_primary};
    }}
    QSplitter::handle {{
        background-color: {p.border_subtle};
        width: 2px;
    }}
    QPushButton#PrimaryStartButton {{
        background-color: {p.accent_primary};
        color: #FFFFFF;
        border: none;
        border-radius: 10px;
        padding: 12px 24px;
        font-size: 15px;
        font-weight: 700;
    }}
    QPushButton#PrimaryStartButton:hover {{
        background-color: {p.accent_hover};
    }}
    QPushButton#NavButton {{
        text-align: left;
        padding: 9px 12px;
        border-radius: 8px;
        border: none;
        background-color: transparent;
    }}
    QPushButton#NavButton[active="true"] {{
        background-color: {p.accent_primary};
        color: #FFFFFF;
    }}
    QComboBox, QLineEdit, QDoubleSpinBox, QSpinBox {{
        background-color: {p.bg_secondary};
        color: {p.text_primary};
        border: 1px solid {p.border_subtle};
        border-radius: 8px;
        padding: 6px 10px;
    }}
    QProgressBar {{
        background-color: {p.bg_secondary};
        border: 1px solid {p.border_subtle};
        border-radius: 7px;
        text-align: center;
        color: {p.text_primary};
        font-weight: 600;
    }}
    QProgressBar::chunk {{
        background-color: {p.accent_primary};
        border-radius: 6px;
    }}
    """
