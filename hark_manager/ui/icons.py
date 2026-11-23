"""Small line icons drawn from inline SVG (no external files)."""
from PySide6.QtCore import QByteArray, Qt, QSize
from PySide6.QtGui import QIcon, QPixmap, QPainter
from PySide6.QtSvg import QSvgRenderer

S = 'xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"'
SVG = {
    "grid": '<svg {s}><rect x="3.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.5"/></svg>',
    "list": '<svg {s}><path d="M4 6h16M4 12h16M4 18h16"/></svg>',
    "bell": '<svg {s}><path d="M6 16V11a6 6 0 1 1 12 0v5l1.5 2h-15z"/><path d="M10 20.5a2 2 0 0 0 4 0"/></svg>',
    "gear": '<svg {s}><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>',
    "search": '<svg {s}><circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/></svg>',
    "plus": '<svg {s}><path d="M12 5v14M5 12h14"/></svg>',
    "window": '<svg {s}><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 9h18"/></svg>',
    "play": '<svg {s}><path d="M8 5.5v13l10-6.5z"/></svg>',
    "check": '<svg {s}><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
    "checkcircle": '<svg {s}><circle cx="12" cy="12" r="9"/><path d="M8 12.3l2.8 2.8L16.3 9.5"/></svg>',
    "alertcircle": '<svg {s}><circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5M12 16.3v.2"/></svg>',
    "chat": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="{c}" d="M12 3.5c5 0 9 3.4 9 7.6s-4 7.6-9 7.6c-1 0-2-.1-2.9-.4L4.5 20.5l1.3-3.6C4 15.5 3 13.4 3 11.1 3 6.9 7 3.5 12 3.5z"/><circle cx="8.3" cy="11.2" r="1.2" fill="#fff"/><circle cx="12" cy="11.2" r="1.2" fill="#fff"/><circle cx="15.7" cy="11.2" r="1.2" fill="#fff"/></svg>',
    "logo": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect x="2" y="2" width="9" height="9" rx="2.2" fill="#9BEA2C"/><rect x="13" y="2" width="9" height="9" rx="2.2" fill="#7BD21A"/><rect x="2" y="13" width="9" height="9" rx="2.2" fill="#7BD21A"/><rect x="13" y="13" width="9" height="9" rx="2.2" fill="#9BEA2C"/></svg>',
    "browser": '<svg {s}><rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M3 8.5h18"/><circle cx="6" cy="6.3" r=".4"/><circle cx="8" cy="6.3" r=".4"/><circle cx="10" cy="6.3" r=".4"/></svg>',
}


def pixmap(name, size=20, color="#111418"):
    svg = SVG[name].replace("{s}", S.replace("{c}", color)).replace("{c}", color)
    r = QSvgRenderer(QByteArray(svg.encode()))
    pm = QPixmap(QSize(size * 2, size * 2))
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    r.render(p)
    p.end()
    pm.setDevicePixelRatio(2)
    return pm


def icon(name, size=20, color="#111418"):
    return QIcon(pixmap(name, size, color))
