"""Material Icon Theme icons (MIT), rendered from the bundled SVGs.

SVGs are drawn with QSvgRenderer instead of relying on Qt's SVG image plugin,
so they render the same in source runs and packaged builds.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from importlib import resources

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

SIZES = (16, 20, 24, 32, 48, 64)


def _svg_bytes(name: str) -> bytes:
    path = resources.files("termswitch") / "assets" / "icons" / "material" / f"{name}.svg"
    return path.read_bytes()


def _render(renderer: QSvgRenderer, size: int, dot: str | None, gray: bool) -> QPixmap:
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p, QRectF(0, 0, size, size))
    if dot:
        # Small status/color dot in the bottom-right corner, with a dark ring
        # so it stays visible on any icon color.
        d = max(size * 0.45, 6.0)
        x = y = size - d
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 170))
        p.drawEllipse(QRectF(x - 1, y - 1, d + 2, d + 2))
        p.setBrush(QColor(dot))
        p.drawEllipse(QRectF(x, y, d, d))
    p.end()
    if gray:
        alpha = img.convertToFormat(QImage.Format.Format_Alpha8)
        g = img.convertToFormat(QImage.Format.Format_Grayscale8).convertToFormat(
            QImage.Format.Format_ARGB32)
        g.setAlphaChannel(alpha)
        img = g
    return QPixmap.fromImage(img)


@lru_cache(maxsize=128)
def icon(name: str, dot: str | None = None, gray: bool = False) -> QIcon:
    """A QIcon for a bundled Material icon, optionally with a colored dot."""
    renderer = QSvgRenderer(QByteArray(_svg_bytes(name)))
    result = QIcon()
    if not renderer.isValid():
        return result
    for size in SIZES:
        result.addPixmap(_render(renderer, size, dot, gray))
    return result


def shell_icon(dot: str | None = None, exited: bool = False) -> QIcon:
    name = "powershell" if sys.platform.startswith("win") else "console"
    return icon(name, dot=None if exited else dot, gray=exited)


# Folder icon for each automatic group kind (see ui.auto_group_label).
AUTO_GROUP_ICONS = {
    "sso": "folder-aws",
    "account": "folder-aws",
    "role": "folder-shared",
    "process": "folder-config",
    "static": "folder-keys",
    "other": "folder-base",
}


# Favorite stars, drawn as vector paths so they stay sharp at any scale. Amber reads
# well on both light and dark backgrounds; the outline uses the text color passed in.
STAR_COLOR = "#e0a030"


def _star_path(size: float):
    import math

    from PySide6.QtGui import QPainterPath

    path = QPainterPath()
    cx = cy = size / 2
    outer, inner = size * 0.46, size * 0.19
    for i in range(10):
        r = outer if i % 2 == 0 else inner
        a = math.radians(-90 + i * 36)
        x, y = cx + r * math.cos(a), cy + r * math.sin(a) + size * 0.03
        path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
    path.closeSubpath()
    return path


@lru_cache(maxsize=16)
def star_icon(filled: bool, outline: str = "#8a8f98", empty: bool = False) -> QIcon:
    """A star for favorites. ``empty=True`` gives a blank icon of the same size, so
    list rows without a star keep their text aligned with rows that have one."""
    from PySide6.QtGui import QPen

    ic = QIcon()
    for size in (16, 32):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        if not empty:
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            path = _star_path(size)
            if filled:
                p.fillPath(path, QColor(STAR_COLOR))
                p.setPen(QPen(QColor(STAR_COLOR).darker(120), size / 16))
            else:
                p.setPen(QPen(QColor(outline), size / 14))
            p.drawPath(path)
            p.end()
        ic.addPixmap(pm)
    return ic


# --------------------------------------------------------------------------
# Icons by text, for menus, toolbars and buttons
# --------------------------------------------------------------------------
# Matched against an action's or button's text with "&" and a trailing "..."
# removed. Patterns are anchored at the start, so dynamic text such as
# "Open 3 terminals" or "Undo Delete profile x" still matches. Anything that
# already has an icon keeps it.

import re as _re  # noqa: E402

TEXT_ICONS: list[tuple[str, str]] = [
    # File
    (r"Reload$", "folder-update"),
    (r"Set up AWS SSO", "folder-secure"),
    (r"Add profiles from AWS SSO", "folder-aws"),
    (r"SSO sessions", "key"),
    (r"Edit config file", "editorconfig"),
    (r"Open in external editor", "document"),
    (r"Show config folder", "folder-config"),
    (r"Backups", "folder-archive"),
    (r"Use (a different|an existing) config file", "folder-import"),
    (r"Use the default config file", "folder-home"),
    (r"Reset config", "folder-trash"),
    (r"Settings", "settings"),
    # View
    (r"Commands$", "command"),
    (r"Find command", "search"),
    (r"(Expand|Collapse) all", "tree"),
    (r"Group untagged profiles by", "folder-base"),
    # Profiles and tabs
    (r"Open (terminal|all \d+ terminals|\d+ terminals)", "console"),
    (r"Open in external terminal", "command"),
    (r"Open$", "console"),
    (r"SSO login", "lock"),
    (r"Check( all| selected| status| status of group)?$", "verified"),
    (r"New profile", "key"),
    (r"Add a profile", "key"),
    (r"Edit( profile)?$", "settings"),
    (r"Rename", "label"),
    (r"Duplicate tab", "console"),
    (r"Duplicate", "template"),
    (r"Delete", "folder-trash"),
    (r"New (sub)?group", "folder-base"),
    (r"Remove from group", "folder-other"),
    (r"Undo", "folder-backup"),
    (r"Copy profile name", "label"),
    (r"Copy AWS_PROFILE command", "command"),
    (r"Open SSO portal", "url"),
    (r"(Set|Clear) tab color", "palette"),
    (r"Terminal colors", "palette"),
    (r"Restart", "folder-update"),
    # Commands pane
    (r"Refresh$", "folder-update"),
    (r"Run$", "console"),
    (r"Insert$", "prompt"),
    (r"Docs$", "readme"),
    (r"Export favorites", "folder-export"),
    (r"Import favorites", "folder-import"),
    (r"Restore default favorites", "folder-backup"),
    # Help, dependency check, About
    (r"Check dependencies", "dependencies-update"),
    (r"Check again", "dependencies-update"),
    (r"Copy fix commands", "command"),
    (r"Licenses", "license"),
    (r"Website", "url"),
    (r"GitHub", "folder-github"),
    (r"Release notes", "changelog"),
    (r"Report an issue", "folder-debug"),
]
_TEXT_ICONS = [(_re.compile(p), name) for p, name in TEXT_ICONS]


def icon_for_text(text: str) -> str | None:
    """The Material icon name for a menu item or button label, or None."""
    clean = text.replace("&&", "\0").replace("&", "").replace("\0", "&").strip()
    clean = _re.sub(r"(\.\.\.|…)$", "", clean).strip()
    for pattern, name in _TEXT_ICONS:
        if pattern.match(clean):
            return name
    return None


def decorate_actions(actions) -> None:
    for action in actions:
        if action.isSeparator() or not action.icon().isNull():
            continue
        name = icon_for_text(action.text())
        if name:
            action.setIcon(icon(name))
        if action.menu() is not None:
            decorate_menu(action.menu())


def decorate_menu(menu) -> None:
    """Add icons to a menu now and whenever it is about to show (its items can
    change, such as the Undo label)."""
    decorate_actions(menu.actions())
    if not menu.property("termswitch_icons"):
        menu.setProperty("termswitch_icons", True)
        menu.aboutToShow.connect(lambda m=menu: decorate_actions(m.actions()))


def decorate_button(button) -> None:
    from PySide6.QtWidgets import QPushButton
    if not isinstance(button, QPushButton) or not button.icon().isNull():
        return
    name = icon_for_text(button.text())
    if name:
        button.setIcon(icon(name))


def install_icon_theme(app) -> None:
    """Give menus, toolbars and push buttons app-wide their Material icons, by text.

    Done with one event filter so every window and dialog gets the same icons
    without each one setting them. Safe to call more than once."""
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtWidgets import QMenu, QPushButton, QToolBar

    if app is None or getattr(app, "_termswitch_icon_filter", None) is not None:
        return

    class _Filter(QObject):
        def eventFilter(self, obj, event):
            if event.type() == QEvent.Type.Polish:
                if isinstance(obj, QMenu):
                    decorate_menu(obj)
                elif isinstance(obj, QToolBar):
                    decorate_actions(obj.actions())
                elif isinstance(obj, QPushButton):
                    decorate_button(obj)
            return False

    f = _Filter(app)
    app.installEventFilter(f)
    app._termswitch_icon_filter = f
    # macOS hides menu icons by default; show them like the rest of the app.
    app.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, False)


# --------------------------------------------------------------------------
# Profiles and services
# --------------------------------------------------------------------------

# Icon for each profile type in the tree; the status dot is drawn on top.
PROFILE_KIND_ICONS = {
    "sso": "lock",
    "role": "hosts",        # two arrows: switching to another role
    "static": "key",
    "process": "dll",       # a program that supplies credentials
    "other": "settings",
}

# Icons for AWS services in the Commands pane's Service list. Services without a
# fitting icon get none.
SERVICE_ICONS = {
    "ec2": "folder-server", "lightsail": "folder-server",
    "autoscaling": "folder-cluster", "application-autoscaling": "folder-cluster",
    "elb": "folder-connection", "elbv2": "folder-connection",
    "ecr": "docker", "ecr-public": "docker", "ecs": "docker",
    "eks": "kubernetes",
    "rds": "database", "dynamodb": "database", "redshift": "database",
    "docdb": "database", "neptune": "database", "elasticache": "database",
    "logs": "log", "cloudwatch": "log", "cloudtrail": "log",
    "lambda": "serverless",
    "cloudformation": "folder-stack",
    "iam": "key", "kms": "key", "sts": "lock", "sso": "lock", "sso-admin": "lock",
    "secretsmanager": "folder-secure", "acm": "certificate",
    "s3": "folder-archive", "s3api": "folder-archive", "s3control": "folder-archive",
    "sqs": "folder-queue", "sns": "email", "ses": "email", "sesv2": "email",
    "apigateway": "folder-api", "apigatewayv2": "folder-api",
    "route53": "url", "route53domains": "url",
    "codebuild": "pipeline", "codepipeline": "pipeline", "deploy": "pipeline",
    "codecommit": "git",
    "configure": "settings", "ssm": "settings", "configservice": "settings",
}


def service_icon(service: str) -> QIcon | None:
    name = SERVICE_ICONS.get(service)
    return icon(name) if name else None
