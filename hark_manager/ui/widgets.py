from PySide6.QtCore import Qt, Signal, QSize, QPropertyAnimation, QEasingCurve, Property, QRectF
from PySide6.QtGui import QPainter, QColor, QPixmap, QPainterPath
from PySide6.QtWidgets import (QFrame, QLabel, QHBoxLayout, QVBoxLayout, QPushButton, QWidget, QLineEdit,
                               QAbstractButton, QSizePolicy)

from . import icons
from .style import LIME, ORANGE, GREEN, GRAY, BORDER


def card(alert=False):
    f = QFrame()
    f.setObjectName("cardAlert" if alert else "card")
    return f


def label(text="", obj=None, wrap=False, size=None, bold=False, color=None):
    l = QLabel(text)
    if obj:
        l.setObjectName(obj)
    l.setWordWrap(wrap)
    css = []
    if size:
        css.append(f"font-size:{size}px")
    if bold:
        css.append("font-weight:700")
    if color:
        css.append(f"color:{color}")
    if css:
        l.setStyleSheet(";".join(css))
    return l


def button(text, kind="outline", icon_name=None, icon_size=18):
    b = QPushButton(text)
    b.setObjectName(kind)
    b.setCursor(Qt.PointingHandCursor)
    if icon_name:
        b.setIcon(icons.icon(icon_name, icon_size))
        b.setIconSize(QSize(icon_size, icon_size))
    return b


class Dot(QWidget):
    def __init__(self, color=GREEN, d=12):
        super().__init__()
        self.c = QColor(color)
        self.d = d
        self.setFixedSize(d + 2, d + 2)

    def set_color(self, c):
        self.c = QColor(c)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(self.c)
        p.drawEllipse(1, 1, self.d, self.d)


STATUS_COLORS = {"Working": GREEN, "Needs reply": ORANGE, "Idle": GRAY, "Closed": GRAY}


class StatusLabel(QWidget):
    def __init__(self, status="Idle"):
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        self.dot = Dot(STATUS_COLORS.get(status, GRAY), 13)
        self.text = QLabel(status)
        self.text.setStyleSheet("font-size:16px")
        h.addWidget(self.dot)
        h.addWidget(self.text)
        h.addStretch()

    def set_status(self, s):
        self.dot.set_color(STATUS_COLORS.get(s, GRAY))
        self.text.setText(s)
        self.text.setStyleSheet("font-size:16px;" + ("color:#7a4b00;" if s == "Needs reply" else ""))


class Toggle(QAbstractButton):
    """iOS-style switch in the lime theme."""

    def __init__(self, checked=False):
        super().__init__()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(66, 34)
        self._pos = 1.0 if checked else 0.0
        self.anim = QPropertyAnimation(self, b"knob", self)
        self.anim.setDuration(140)
        self.anim.setEasingCurve(QEasingCurve.InOutQuad)
        self.toggled.connect(self._animate)

    def _animate(self, on):
        self.anim.stop()
        self.anim.setStartValue(self._pos)
        self.anim.setEndValue(1.0 if on else 0.0)
        self.anim.start()

    def getpos(self):
        return self._pos

    def setpos(self, v):
        self._pos = v
        self.update()

    knob = Property(float, getpos, setpos)

    def setChecked(self, v):
        super().setChecked(v)
        self._pos = 1.0 if v else 0.0
        self.update()

    def sizeHint(self):
        return QSize(66, 34)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(2, 3, 62, 28)
        on = QColor(LIME)
        off = QColor("#D5D8DC")
        c = QColor(int(off.red() + (on.red() - off.red()) * self._pos), int(off.green() + (on.green() - off.green()) * self._pos), int(off.blue() + (on.blue() - off.blue()) * self._pos))
        p.setPen(QColor("#C4C8CD") if self._pos < .5 else Qt.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(r, 14, 14)
        x = 5 + self._pos * 32
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("white"))
        p.drawEllipse(QRectF(x, 6, 22, 22))


class SearchBox(QLineEdit):
    def __init__(self, placeholder):
        super().__init__()
        self.setPlaceholderText(placeholder)
        self.addAction(icons.icon("search", 20, "#3a3f45"), QLineEdit.LeadingPosition)
        self.setMinimumHeight(42)
        self.setStyleSheet("font-size:15px; padding-left:4px")


class Preview(QLabel):
    """Rounded browser preview; shows the live window capture or a placeholder."""

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(170)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.pm = None
        self.placeholder = "Closed"

    def set_png(self, data, placeholder="Closed"):
        self.placeholder = placeholder
        if data:
            pm = QPixmap()
            pm.loadFromData(data)
            self.pm = pm
        else:
            self.pm = None
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(r, 9, 9)
        p.setClipPath(path)
        p.fillRect(r, QColor("#EEF0F2"))
        if self.pm and not self.pm.isNull():
            s = self.pm.scaled(int(r.width()), int(r.height()), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            p.drawPixmap(int(r.x()), int(r.y()), s, 0, 0, int(r.width()), int(r.height()))
        else:
            # browser chrome placeholder like the mock-up
            p.fillRect(QRectF(r.x(), r.y(), r.width(), 26), QColor("#E2E5E8"))
            for i in range(3):
                p.setBrush(QColor("#9AA0A6"))
                p.setPen(Qt.NoPen)
                p.drawEllipse(QRectF(r.x() + 10 + i * 11, r.y() + 9, 7, 7))
            pm = icons.pixmap("browser", 64, "#A9AFB6")
            p.drawPixmap(int(r.center().x() - 32), int(r.center().y() - 26), pm)
            p.setPen(QColor("#7d838b"))
            p.drawText(QRectF(r.x(), r.center().y() + 40, r.width(), 22), Qt.AlignCenter, self.placeholder)
        p.setClipping(False)
        p.setPen(QColor(BORDER))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 9, 9)


class IconLabel(QLabel):
    def __init__(self, name, size=26, color="#111418"):
        super().__init__()
        self.setPixmap(icons.pixmap(name, size, color))
        self.setFixedSize(size + 4, size + 4)


class VpnBadge(QFrame):
    """One badge for a profile's VPN, read by its own bridge: 'VPN on · US' (green) / 'VPN off' / 'VPN unknown'."""
    STYLES = {"on": ("#EAF8D2", "#1F7A1F", "#1F7A1F"), "off": ("#F0F2F4", "#2B3036", "#2B3036"),
              "unknown": ("#F0F2F4", "#2B3036", "#5B616B")}

    def __init__(self, size=14):
        super().__init__()
        self.setObjectName("vpnBadge")
        h = QHBoxLayout(self)
        h.setContentsMargins(9, 3, 11, 3)
        h.setSpacing(5)
        self.ic = QLabel()
        self.tx = QLabel()
        self.sz = size
        h.addWidget(self.ic)
        h.addWidget(self.tx)
        self.state = None
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.set_state("unknown", "")

    def set_state(self, state, info, who=""):
        text = {"on": f"VPN on · {info}", "off": "VPN off"}.get(state, "VPN unknown")
        bg, fg, icol = self.STYLES.get(state, self.STYLES["unknown"])
        if (state, text) != self.state:
            self.state = (state, text)
            self.setStyleSheet(f"QFrame#vpnBadge{{background:{bg};border-radius:12px;}}"
                               f"QLabel{{color:{fg};font-size:{self.sz}px;font-weight:600;background:transparent;}}")
            self.ic.setPixmap(icons.pixmap("shield", self.sz + 2, icol))
            self.tx.setText(text)
        tip = {"on": f"Browsec connected · public IP country {info} (is profile se verify kiya)",
               "off": "Is profile mein koi VPN proxy on nahi (confirmed)"}.get(state, f"VPN status verify nahi hua: {info}")
        self.setToolTip(tip)
        self.setAccessibleName(f"VPN badge {who}: {text}".strip())
