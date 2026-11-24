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
QPushButton#primary {{ background: {LIME}; color: {TEXT}; border: none; border-radius: 9px; padding: 9px 18px; font-size: 15px; font-weight: 600; }}
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
QPushButton#seg:checked {{ background: {LIME}; border-color: {LIME}; color: {TEXT}; }}
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


# ======================= Light / Dark theme =======================
# Light is the look above. Dark (charcoal background, white text, lime controls) is derived from it: every style
# sheet in the app (the main QSS and the small inline ones on cards, badges, panes, dialogs) is rewritten colour by
# colour when it is applied, so a widget made later or restyled later (a card rebuilt, a badge re-coloured) comes
# out dark too. Switching back restores each widget's own light sheet.
import colorsys
import re

THEMES = ("light", "dark")
_theme = "light"

DARK = {"bg": "#15181C", "card": "#1E2227", "panel": "#1A1D21", "hover": "#262B31", "border": "#2F353D",
        "text": "#FFFFFF", "muted": "#A8AFB8", "input": "#23282E", "lime_soft": "#2C3A1B"}

# exact light -> dark mappings per role (background / text / border); anything else is mapped by lightness
_BG = {"#f6f7f8": "#15181C", "#ffffff": "#1E2227", "#f3f4f5": "#1A1D21", "#f0f2f4": "#262B31", "#eef0f2": "#262B31",
       "#e3e6ea": "#2F353D", "#e2e5e8": "#2A2F36", "#e9ecef": "#2A2F36", "#d5d9de": "#3A414A", "#d5d8dc": "#3A414A",
       "#eaf8d2": "#2C3A1B", "#f4fce8": "#223019", "#fff1d6": "#3A2C12", "#fde2e0": "#3D2120", "#ddf5d0": "#1F3A1F",
       "#e6f0ff": "#1C2A40", "#ecEEF0".lower(): "#2A2F36", "#f7f8f9": "#1A1D21", "#fafbfc": "#1A1D21"}
_TX = {"#111418": "#FFFFFF", "#000000": "#FFFFFF", "#5b616b": "#A8AFB8", "#2b3036": "#D5D9DE", "#4a5058": "#B8BEC6",
       "#3a3f45": "#C9CED6", "#7a4b00": "#F5C46B", "#c62828": "#FF8A80", "#1e7b1e": "#7EE07E", "#1f7a1f": "#7EE07E",
       "#2e6b00": "#B6F07A", "#1f5fbf": "#8AB4FF", "#8a8f98": "#9AA1AA", "#1b1300": "#1b1300"}
_BD = {"#e3e6ea": "#2F353D", "#cdd1d6": "#3A414A", "#d6dadf": "#3A414A", "#d5d9de": "#3A414A", "#eceef0": "#2A2F36",
       "#c9ee95": "#4A6A22", "#c4c8cd": "#4A515B", "#ffffff": "#2F353D"}
_NAMED = {"white": "#ffffff", "black": "#000000"}
_COLOR = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b|\bwhite\b|\bblack\b|rgba?\([^)]*\)")
_DECL = re.compile(r"([A-Za-z-]+)(\s*:\s*)([^;{}]+)")
MARK = "/*hbm-dark*/"


def adapt_color(c, role="text"):
    """One light-theme colour -> its Dark counterpart (role: "text", "bg" or "border")."""
    return _map(c, role)


def theme():
    return _theme


def is_dark():
    return _theme == "dark"


def _hex(c):
    c = _NAMED.get(c.lower(), c.lower())
    if len(c) == 4:
        c = "#" + "".join(ch * 2 for ch in c[1:])
    return c


def _rgb(c):
    return tuple(int(c[i:i + 2], 16) / 255 for i in (1, 3, 5))


def _fmt(r, g, b):
    return "#%02X%02X%02X" % tuple(max(0, min(255, round(x * 255))) for x in (r, g, b))


def _bright_sat(c):
    """Lime / orange / green accents: kept as they are in Dark."""
    h, l, s = colorsys.rgb_to_hls(*_rgb(c))
    return s > 0.55 and 0.35 < l < 0.75


def _map(c, role):
    if c.startswith("rgb"):
        m = re.findall(r"[\d.]+", c)
        if len(m) >= 3 and role == "bg":
            r, g, b = (float(x) / 255 for x in m[:3])
            h, l, s = colorsys.rgb_to_hls(r, g, b)
            if l > 0.85 and s < 0.3:           # light translucent fill -> dark translucent fill
                return c.replace(m[0], "40", 1).replace(m[1], "45", 1).replace(m[2], "52", 1)
        return c
    c = _hex(c)
    if len(c) != 7:
        return c
    table = {"bg": _BG, "text": _TX, "border": _BD}[role]
    if c in table:
        return table[c]
    if _bright_sat(c):
        return c
    h, l, s = colorsys.rgb_to_hls(*_rgb(c))
    if role == "text":
        if l < 0.5:                             # dark text -> light text (tinted text keeps its hue)
            return _fmt(*colorsys.hls_to_rgb(h, 0.80 if s > 0.25 else 0.88, min(s, 0.7)))
        return c
    if role == "border":
        if l > 0.6:
            return _fmt(*colorsys.hls_to_rgb(h, 0.24, min(s, 0.25)))
        return c
    if l > 0.6:                                 # light fill -> dark fill (tints keep their hue, muted)
        return _fmt(*colorsys.hls_to_rgb(h, 0.11 + (1 - l) * 0.35, min(s, 0.35)))
    return c


def _role(prop):
    p = prop.lower()
    if p in ("color", "selection-color") or p.endswith("-color") and "background" not in p and "border" not in p:
        return "text"
    if p.startswith("border") or p == "outline":
        return "border"
    if "background" in p or p == "alternate-background-color":
        return "bg"
    return None


def adapt(css):
    """A light style sheet -> its Dark version (idempotent: an adapted sheet carries MARK)."""
    if not css or MARK in css:
        return css
    blocks = re.split(r"(\{[^{}]*\})", css) if "{" in css else [css]
    out = []
    for blk in blocks:
        body = blk
        # a rule with a lime / accent background keeps its dark text (Save changes, Tile All, seg:checked)
        bgs = [m.group(3) for m in _DECL.finditer(body) if _role(m.group(1)) == "bg"]
        keep_text = any(_COLOR.search(v) and _bright_sat(_hex(_COLOR.search(v).group(0))) for v in bgs
                        if _COLOR.search(v) and not _COLOR.search(v).group(0).startswith("rgb"))

        def decl(m):
            role = _role(m.group(1))
            if not role or (role == "text" and keep_text):
                return m.group(0)
            return m.group(1) + m.group(2) + _COLOR.sub(lambda c: _map(c.group(0), role), m.group(3))
        body = _DECL.sub(decl, body)
        out.append(body)
    res = "".join(out).replace("chevron.svg", "chevron_dark.svg")
    return MARK + res


def build_qss():
    import os
    from ..config import LOCALDATA
    path = os.path.join(LOCALDATA, "chevron.svg")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#111418" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>')
    dpath = os.path.join(LOCALDATA, "chevron_dark.svg")
    if not os.path.exists(dpath):
        with open(dpath, "w") as f:
            f.write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#E8EAED" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>')
    # Light text on lime would be unreadable in Dark: the lime buttons name their dark text explicitly
    return (QSS + DARK_SAFE).replace("ARROW_PATH", path.replace("\\", "/"))


DARK_SAFE = f"""
QDialog, QMessageBox, QInputDialog {{ background: {BG}; }}
"""


def dark_title_bar(w, on=None):
    """Windows 10/11 title bar of a top-level window in the theme's colours (DWMWA_USE_IMMERSIVE_DARK_MODE)."""
    try:
        import ctypes
        if not w.isWindow() or not w.testAttribute(Qt_WA_WState_Created()):
            return
        v = ctypes.c_int(1 if (is_dark() if on is None else on) else 0)
        hwnd = int(w.winId())
        for attr in (20, 19):          # 20 on Windows 10 2004+ / 11, 19 on older builds
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v)) == 0:
                break
        # repaint the caption now (Windows 10 only redraws it on an activation change)
        u = ctypes.windll.user32
        active = 1 if u.GetForegroundWindow() == hwnd else 0
        u.SendMessageW(hwnd, 0x0086, 1 - active, 0)      # WM_NCACTIVATE
        u.SendMessageW(hwnd, 0x0086, active, 0)
    except Exception:
        pass


def Qt_WA_WState_Created():
    from PySide6.QtCore import Qt
    return Qt.WA_WState_Created


def icon_color():
    return "#E8EAED" if is_dark() else TEXT


class _ThemeFilter:
    """App-wide: a widget's own style sheet is adapted to Dark when it is first shown or whenever it changes."""
    obj = None

    @classmethod
    def install(cls):
        from PySide6.QtCore import QObject, QEvent
        from PySide6.QtWidgets import QApplication, QWidget

        class F(QObject):
            def eventFilter(self, o, e):
                t = e.type()
                if t == QEvent.Show and _theme == "dark" and isinstance(o, QWidget) and o.isWindow():
                    dark_title_bar(o, True)
                if (t == QEvent.StyleChange or t == QEvent.Polish) and _theme == "dark" and isinstance(o, QWidget):
                    try:
                        css = o.styleSheet()
                        if css and MARK not in css:
                            o.setProperty("hbm_css", css)
                            o.setStyleSheet(adapt(css))
                    except Exception:
                        pass
                return False
        if cls.obj is None and QApplication.instance() is not None:
            cls.obj = F()
            QApplication.instance().installEventFilter(cls.obj)


_LIGHT_PAL = None


def _palette(dark):
    from PySide6.QtGui import QPalette, QColor
    if not dark:
        return QPalette(_LIGHT_PAL)
    pal = QPalette(_LIGHT_PAL)
    c = lambda x: QColor(x)
    for role, col in ((QPalette.Window, DARK["bg"]), (QPalette.Base, DARK["input"]), (QPalette.AlternateBase, DARK["card"]),
                      (QPalette.Text, "#FFFFFF"), (QPalette.WindowText, "#FFFFFF"), (QPalette.Button, DARK["card"]),
                      (QPalette.ButtonText, "#FFFFFF"), (QPalette.ToolTipBase, DARK["card"]), (QPalette.ToolTipText, "#FFFFFF"),
                      (QPalette.PlaceholderText, "#8A919B"), (QPalette.Highlight, LIME), (QPalette.HighlightedText, TEXT),
                      (QPalette.Light, "#3A414A"), (QPalette.Mid, "#2F353D"), (QPalette.Dark, "#111316")):
        pal.setColor(role, c(col))
    return pal


def apply_theme(name):
    """Switch the whole running UI (every window, page, drawer, dialog and the Multi-View) to Light or Dark now."""
    global _theme
    from PySide6.QtWidgets import QApplication, QAbstractButton
    name = name if name in THEMES else "light"
    app = QApplication.instance()
    global _LIGHT_PAL
    if _LIGHT_PAL is None:
        from PySide6.QtGui import QPalette
        _LIGHT_PAL = QPalette(app.palette())      # the look Light always had
    _ThemeFilter.install()
    if name == _theme and app.property("hbm_theme") == name:
        return
    _theme = name
    app.setProperty("hbm_theme", name)
    app.setPalette(_palette(name == "dark"))
    from . import icons
    for w in app.allWidgets():
        try:
            src = w.property("hbm_css")
            css = w.styleSheet()
            if name == "dark":
                if css and MARK not in css:
                    w.setProperty("hbm_css", css)
                    w.setStyleSheet(adapt(css))
            elif src is not None and css and MARK in css:
                w.setStyleSheet(src)
            ic = w.property("hbm_icon")
            if ic and isinstance(w, QAbstractButton):
                n, sz = str(ic).split(":")
                w.setIcon(icons.icon(n, int(sz)))
            if hasattr(w, "on_theme"):
                w.on_theme()
            if w.isWindow():
                dark_title_bar(w)
            w.update()
        except Exception:
            pass
