"""配色与全局样式。所有颜色都从这里取,界面里不写死颜色,方便切换浅色 / 深色。"""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

LIGHT = dict(
    bg="#F3F4F7",            # 窗口底色
    surface="#FFFFFF",       # 面板、卡片
    surface2="#F6F7F9",      # 输入框、次级填充
    hover="#EEF0F4",
    border="#E2E5EB",
    border_strong="#CBD0D9",
    text="#1C2028",
    text2="#5A6271",
    text3="#8B92A0",
    primary="#4F46E5",       # 靛蓝:主色
    primary_hover="#4338CA",
    primary_press="#3730A3",
    primary_soft="#EEF0FF",
    primary_soft2="#DFE2FF",
    on_primary="#FFFFFF",
    desk="#E7E9EF",          # 页面后面的"桌面"
    page_shadow="#101828",
    danger="#E5484D",
    success="#2E9E6B",
    warning="#E39B2D",
    toast_bg="#1F2330",
    toast_text="#FFFFFF",
    toast_link="#A5B4FC",
    highlight="#FFE066",
)

DARK = dict(
    bg="#15171C",
    surface="#1D2027",
    surface2="#242831",
    hover="#2A2F39",
    border="#2D323C",
    border_strong="#3B414D",
    text="#E8EAEF",
    text2="#A6ADBA",
    text3="#757D8C",
    primary="#818CF8",
    primary_hover="#939CFA",
    primary_press="#6D78F0",
    primary_soft="#262A48",
    primary_soft2="#30355E",
    on_primary="#10121A",
    desk="#101216",
    page_shadow="#000000",
    danger="#FF6B70",
    success="#3DD68C",
    warning="#F5B94C",
    toast_bg="#E8EAEF",
    toast_text="#15171C",
    toast_link="#4F46E5",
    highlight="#FFE066",
)

FONT_FAMILY = "Microsoft YaHei UI"


class Theme:
    """当前主题。T.c("primary") 取颜色字符串,T.q("primary") 取 QColor。"""

    def __init__(self):
        self.mode = "light"
        self.colors = dict(LIGHT)

    def set_mode(self, mode: str):
        self.mode = mode
        self.colors = dict(DARK if mode == "dark" else LIGHT)

    def c(self, key: str) -> str:
        return self.colors[key]

    def q(self, key: str, alpha: int | None = None) -> QColor:
        col = QColor(self.colors[key])
        if alpha is not None:
            col.setAlpha(alpha)
        return col


T = Theme()


def stylesheet() -> str:
    c = T.colors
    from ui.icons import png_file
    check_img = png_file("check", c["on_primary"], 16)
    chevron_img = png_file("chevron_down", c["text2"], 12)
    return f"""
* {{ font-family: "{FONT_FAMILY}", "Segoe UI"; font-size: 13px; color: {c['text']}; }}
QMainWindow, QDialog {{ background: {c['bg']}; }}
QWidget#AppBar {{ background: {c['surface']}; border-bottom: 1px solid {c['border']}; }}
QWidget#StatusBar {{ background: {c['surface']}; border-top: 1px solid {c['border']}; }}
QWidget#SidePanel, QWidget#Inspector {{ background: {c['surface']}; }}
QWidget#SidePanel {{ border-right: 1px solid {c['border']}; }}
QWidget#Inspector {{ border-left: 1px solid {c['border']}; }}
QWidget#ToolPalette {{ background: {c['surface2']}; border: 1px solid {c['border']}; border-radius: 12px; }}
QLabel#DocTitle {{ font-size: 14px; font-weight: 600; }}
QLabel#Muted, QLabel#Hint {{ color: {c['text2']}; }}
QLabel#Faint {{ color: {c['text3']}; font-size: 12px; }}
QLabel#SectionTitle {{ color: {c['text3']}; font-size: 11px; font-weight: 600; letter-spacing: 1px; padding-top: 6px; }}
QLabel#PanelTitle {{ font-size: 13px; font-weight: 600; }}
QLabel#WelcomeTitle {{ font-size: 24px; font-weight: 700; }}
QLabel#WelcomeSub {{ color: {c['text2']}; font-size: 14px; }}
QFrame#Card {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 14px; }}
QFrame#DropZone {{ background: {c['surface']}; border: 2px dashed {c['border_strong']}; border-radius: 16px; }}
QFrame#DropZone[active="true"] {{ background: {c['primary_soft']}; border-color: {c['primary']}; }}
QFrame#Divider {{ background: {c['border']}; min-width: 1px; max-width: 1px; }}
QFrame#HDivider {{ background: {c['border']}; min-height: 1px; max-height: 1px; }}

QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 5px; }}
QToolButton:hover {{ background: {c['hover']}; }}
QToolButton:pressed {{ background: {c['primary_soft2']}; }}
QToolButton:checked {{ background: {c['primary_soft']}; border-color: {c['primary_soft2']}; }}
QToolButton:disabled {{ color: {c['text3']}; }}
QToolButton#Tool {{ padding: 6px; border-radius: 9px; }}
QToolButton#TextButton {{ padding: 5px 10px; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}

QPushButton {{ background: {c['surface']}; border: 1px solid {c['border_strong']}; border-radius: 8px;
              padding: 7px 14px; font-weight: 500; }}
QPushButton:hover {{ background: {c['hover']}; }}
QPushButton:pressed {{ background: {c['primary_soft2']}; }}
QPushButton:disabled {{ color: {c['text3']}; border-color: {c['border']}; }}
QPushButton#Primary {{ background: {c['primary']}; border: 1px solid {c['primary']}; color: {c['on_primary']}; }}
QPushButton#Primary:hover {{ background: {c['primary_hover']}; border-color: {c['primary_hover']}; }}
QPushButton#Primary:pressed {{ background: {c['primary_press']}; }}
QPushButton#Primary:disabled {{ background: {c['primary_soft2']}; border-color: {c['primary_soft2']}; color: {c['surface']}; }}
QPushButton#Ghost {{ background: transparent; border: 1px solid transparent; color: {c['text2']}; }}
QPushButton#Ghost:hover {{ background: {c['hover']}; color: {c['text']}; }}
QPushButton#Ghost::menu-indicator {{ image: url("{chevron_img}"); subcontrol-origin: padding;
                                     subcontrol-position: right center; width: 12px; height: 12px; right: 6px; }}
QPushButton#Ghost[hasMenu="true"] {{ padding-right: 26px; }}
QPushButton#Ghost:open {{ background: {c['primary_soft']}; color: {c['primary']}; }}
QPushButton#Danger {{ color: {c['danger']}; }}
QPushButton#Big {{ padding: 11px 22px; font-size: 14px; border-radius: 10px; }}
QPushButton#Swatch {{ border-radius: 9px; padding: 0; min-width: 18px; max-width: 18px; min-height: 18px; max-height: 18px;
                     border: 1px solid {c['border_strong']}; }}
QPushButton#Swatch:checked {{ border: 2px solid {c['primary']}; }}
QPushButton#Chip {{ border-radius: 14px; padding: 5px 12px; background: {c['surface2']}; border: 1px solid {c['border']}; }}
QPushButton#Chip:checked {{ background: {c['primary_soft']}; border-color: {c['primary']}; color: {c['primary']}; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit {{
    background: {c['surface2']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 5px 8px;
    selection-background-color: {c['primary']}; selection-color: {c['on_primary']}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{
    border: 1px solid {c['primary']}; background: {c['surface']}; }}
QLineEdit#Search {{ border-radius: 16px; padding: 5px 10px 5px 4px; min-width: 200px; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 8px;
    selection-background-color: {c['primary_soft']}; selection-color: {c['text']}; padding: 4px; outline: none; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 16px; border: none; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px; border: 1px solid {c['border_strong']}; background: {c['surface']}; }}
QCheckBox::indicator:checked {{ background: {c['primary']}; border-color: {c['primary']}; image: url("{check_img}"); }}
QCheckBox::indicator:hover {{ border-color: {c['primary']}; }}
QRadioButton {{ spacing: 8px; }}
QRadioButton::indicator {{ width: 16px; height: 16px; border-radius: 9px; border: 1px solid {c['border_strong']};
                          background: {c['surface']}; }}
QRadioButton::indicator:checked {{ border: 5px solid {c['primary']}; background: {c['on_primary']}; width: 8px;
                                  height: 8px; }}
QRadioButton::indicator:hover {{ border-color: {c['primary']}; }}

QSlider::groove:horizontal {{ height: 4px; background: {c['border']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {c['primary']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 14px; height: 14px; margin: -6px 0; border-radius: 7px;
    background: {c['surface']}; border: 2px solid {c['primary']}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['border_strong']}; border-radius: 4px; min-height: 36px; }}
QScrollBar::handle:vertical:hover {{ background: {c['text3']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c['border_strong']}; border-radius: 4px; min-width: 36px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QMenu {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 7px 28px 7px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {c['primary_soft']}; color: {c['text']}; }}
QMenu::item:disabled {{ color: {c['text3']}; }}
QMenu::separator {{ height: 1px; background: {c['border']}; margin: 5px 8px; }}
QMenu::icon {{ padding-left: 8px; }}

QToolTip {{ background: {c['toast_bg']}; color: {c['toast_text']}; border: none; border-radius: 6px; padding: 6px 9px; }}

QListWidget#Thumbs {{ background: {c['surface']}; border: none; outline: none; }}
QTreeWidget {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 10px; outline: none; }}
QTreeWidget::item {{ padding: 6px 4px; }}
QTreeWidget::item:selected {{ background: {c['primary_soft']}; color: {c['text']}; }}
QHeaderView::section {{ background: {c['surface2']}; border: none; border-bottom: 1px solid {c['border']}; padding: 6px; color: {c['text2']}; }}
QProgressBar {{ background: {c['surface2']}; border: none; border-radius: 4px; height: 8px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {c['primary']}; border-radius: 4px; }}
QFrame#Toast {{ background: {c['toast_bg']}; border-radius: 10px; }}
QLabel#ToastText {{ color: {c['toast_text']}; background: transparent; }}
QPushButton#ToastButton {{ background: transparent; border: none; border-radius: 6px; padding: 4px 10px;
                          color: {c['toast_link']}; font-weight: 600; }}
QPushButton#ToastButton:hover {{ background: rgba(128, 128, 128, 0.22); }}
QWidget#Segmented {{ background: {c['surface2']}; border: 1px solid {c['border']}; border-radius: 9px; }}
QToolButton#SegButton {{ border: 1px solid transparent; border-radius: 7px; padding: 4px 6px; color: {c['text2']}; }}
QToolButton#SegButton:hover {{ background: {c['hover']}; color: {c['text']}; }}
QToolButton#SegButton:checked {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border_strong']}; }}
QPushButton#ListAction {{ text-align: left; border: none; background: transparent; padding: 7px 8px; border-radius: 8px;
                          font-weight: 400; }}
QPushButton#ListAction:hover {{ background: {c['hover']}; }}
QPushButton#ListAction:pressed {{ background: {c['primary_soft']}; }}
QPushButton#ListAction[danger="true"] {{ color: {c['danger']}; }}
QPushButton#Toggle {{ padding: 5px 8px; min-width: 28px; }}
QPushButton#Toggle:checked {{ background: {c['primary_soft']}; border-color: {c['primary']}; color: {c['primary']}; }}
QLineEdit#PageBox {{ padding: 3px 4px; }}
QLabel#DialogTitle {{ font-size: 17px; font-weight: 600; }}
QPushButton#DangerFill {{ background: {c['danger']}; border: 1px solid {c['danger']}; color: #FFFFFF; }}
QPushButton#DangerFill:hover {{ background: {c['danger']}; border-color: {c['text']}; }}
QPushButton#MsgIcon {{ background: {c['primary_soft']}; border: none; border-radius: 20px; padding: 0; }}
QPushButton#MsgIcon[kind="warn"] {{ background: {c['surface2']}; }}
QPushButton#MsgIcon[kind="error"], QPushButton#MsgIcon[kind="danger"] {{ background: {c['surface2']}; }}
QPushButton#HeadIcon {{ background: {c['primary_soft']}; border: none; border-radius: 10px; padding: 0; }}
QToolButton#Boxed {{ border: 1px solid {c['border']}; background: {c['surface']}; border-radius: 9px; }}
QToolButton#Boxed:hover {{ background: {c['hover']}; border-color: {c['border_strong']}; }}
QLabel#Kbd {{ background: {c['surface2']}; border: 1px solid {c['border']}; border-radius: 5px; padding: 1px 6px;
             color: {c['text2']}; font-size: 12px; }}
QLabel#Logo {{ background: {c['primary']}; border-radius: 9px; }}
QLabel#Dot {{ color: {c['warning']}; font-size: 16px; }}
QPushButton#RecentRow {{ background: transparent; border: none; border-radius: 10px; padding: 0; text-align: left; }}
QPushButton#RecentRow:hover {{ background: {c['hover']}; }}
QPushButton#RecentRow:pressed {{ background: {c['primary_soft']}; }}
QLabel#RecentName {{ font-weight: 500; }}
QLabel#DropTitle {{ font-size: 16px; font-weight: 600; }}
QPushButton#QuickCard {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 14px;
                         padding: 0; text-align: left; }}
QPushButton#QuickCard:hover {{ border-color: {c['primary']}; }}
QPushButton#QuickCard:pressed {{ background: {c['primary_soft']}; }}
QLabel#QuickTitle {{ font-size: 14px; font-weight: 600; }}
QWidget#Welcome {{ background: {c['bg']}; }}
QPushButton#Primary[big="true"] {{ font-size: 14px; border-radius: 10px; padding: 8px 20px; }}
QWidget#Center {{ background: {c['desk']}; }}
QTextBrowser {{ background: {c['surface']}; border: none; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QSplitter::handle {{ background: transparent; }}
"""


def apply(app: QApplication, mode: str | None = None):
    if mode:
        T.set_mode(mode)
    font = QFont(FONT_FAMILY, 10)
    app.setFont(font)
    pal = app.palette()
    pal.setColor(QPalette.Window, T.q("bg"))
    pal.setColor(QPalette.Base, T.q("surface"))
    pal.setColor(QPalette.Text, T.q("text"))
    pal.setColor(QPalette.WindowText, T.q("text"))
    pal.setColor(QPalette.Highlight, T.q("primary"))
    pal.setColor(QPalette.HighlightedText, T.q("on_primary"))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet())
