LIME = "#9BEA2C"
LIME_SOFT = "#EAF8D2"
LIME_BORDER = "#7BD21A"
ORANGE = "#F5A524"
ORANGE_SOFT = "#FFF1D6"
GREEN = "#2DB82D"
GRAY = "#8A8F98"
TEXT = "#111418"
MUTED = "#5B616B"
BORDER = "#E3E6EA"
BG = "#F6F7F8"
CARD = "#FFFFFF"

QSS = f"""
* {{ font-family: 'Segoe UI Variable Text', 'Segoe UI'; color: {TEXT}; }}
QMainWindow, #root {{ background: {BG}; }}
#sidebar {{ background: {BG}; border-right: 1px solid {BORDER}; }}
#content {{ background: {BG}; }}
#rightPanel {{ background: #F3F4F5; border-left: 1px solid {BORDER}; }}
QLabel#h1 {{ font-size: 30px; font-weight: 700; }}
QLabel#h2 {{ font-size: 21px; font-weight: 700; }}
QLabel#muted {{ color: {MUTED}; font-size: 14px; }}
QPushButton#nav {{ text-align: left; padding: 10px 14px; border: none; border-radius: 10px; font-size: 16px; background: transparent; }}
QPushButton#nav:hover {{ background: #EEF0F2; }}
QPushButton#nav:checked {{ background: {LIME_SOFT}; }}
QLabel#badge {{ background: {ORANGE}; color: #1b1300; border-radius: 11px; font-weight: 700; font-size: 12px; min-width: 22px; max-width: 22px; min-height: 22px; max-height: 22px; qproperty-alignment: AlignCenter; }}
QFrame#card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 14px; }}
QFrame#cardSel {{ background: #F4FCE8; border: 2px solid {LIME}; border-radius: 14px; }}
QFrame#cardTeam {{ background: {CARD}; border: 2px solid {LIME}; border-radius: 14px; }}
QFrame#cardAlert {{ background: {CARD}; border: 2px solid {ORANGE}; border-radius: 14px; }}
QPushButton#primary {{ background: {LIME}; border: none; border-radius: 9px; padding: 9px 18px; font-size: 15px; font-weight: 600; }}
QPushButton#primary:hover {{ background: #8FE01F; }}
QPushButton#primary:pressed {{ background: #7ccc12; }}
QPushButton#outline {{ background: {CARD}; border: 1.5px solid {LIME_BORDER}; border-radius: 9px; padding: 7px 14px; font-size: 14px; font-weight: 600; }}
QPushButton#outline:hover {{ background: {LIME_SOFT}; }}
QPushButton#outline:disabled {{ border-color: {BORDER}; color: {GRAY}; }}
QPushButton#plain {{ background: {CARD}; border: 1px solid #CDD1D6; border-radius: 9px; padding: 8px 18px; font-size: 15px; }}
QPushButton#plain:hover {{ background: #F0F2F4; }}
QPushButton#link {{ background: transparent; border: none; color: {MUTED}; font-size: 14px; padding: 6px 10px; }}
QPushButton#link:hover {{ color: {TEXT}; text-decoration: underline; }}
QPushButton#chip {{ background: {CARD}; border: 1px solid #D6DADF; border-radius: 9px; padding: 8px 18px; font-size: 15px; }}
QPushButton#chip:checked {{ background: {LIME_SOFT}; border-color: {LIME_SOFT}; font-weight: 600; }}
QPushButton#seg {{ background: {CARD}; border: 1px solid #D6DADF; padding: 6px 12px; }}
QPushButton#seg:checked {{ background: {LIME}; border-color: {LIME}; }}
QPushButton#dots {{ background: transparent; border: none; font-size: 18px; font-weight: 700; padding: 0 6px; }}
QPushButton#dots::menu-indicator {{ image: none; width: 0; }}
QLineEdit, QComboBox {{ background: {CARD}; border: 1px solid #CDD1D6; border-radius: 9px; padding: 7px 10px; font-size: 14px; }}
QLineEdit:focus, QComboBox:focus {{ border: 1.5px solid {LIME_BORDER}; }}
QComboBox::drop-down {{ border: none; width: 30px; }}
QComboBox::down-arrow {{ image: url(ARROW_PATH); width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{ background: white; selection-background-color: {LIME_SOFT}; selection-color: {TEXT}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ width: 8px; background: transparent; }}
QScrollBar::handle:vertical {{ background: #CDD1D6; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QLabel#pillNeeds {{ background: {ORANGE_SOFT}; border-radius: 8px; padding: 5px 10px; font-size: 14px; }}
QLabel#pillDone {{ background: {LIME_SOFT}; border-radius: 8px; padding: 5px 10px; font-size: 14px; }}
QLabel#banner {{ background: {ORANGE_SOFT}; border: 1px solid {ORANGE}; border-radius: 10px; padding: 10px 12px; font-size: 14px; }}
QMenu {{ background: white; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 7px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {LIME_SOFT}; color: {TEXT}; }}
QToolTip {{ background: white; border: 1px solid {BORDER}; padding: 4px; }}
"""


def build_qss():
    import os
    from ..config import LOCALDATA
    path = os.path.join(LOCALDATA, "chevron.svg")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#111418" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>')
    return QSS.replace("ARROW_PATH", path.replace("\\", "/"))
