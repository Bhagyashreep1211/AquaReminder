#!/usr/bin/env python3
"""
AquaReminder — a glassmorphic macOS status-bar hydration companion.

Runs quietly in the menu bar and, on a timer, slides a frosted-glass HUD up from
the bottom-right corner with a little pixel character who drinks water with you.

Usage:
    python3 app.py            # normal run (lives in the macOS status bar)
    python3 app.py --smoke    # headless self-test: exercises every paint path
    python3 app.py --demo     # run and pop the reminder immediately
"""

import json
import os
import sys
import math

# High-DPI / Retina support. Must be set before QApplication is constructed.
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")

from PyQt6.QtCore import (
    Qt, QTimer, QPoint, QPointF, QRectF, QPropertyAnimation,
    QParallelAnimationGroup, QEasingCurve, QSettings, QUrl, QProcess,
)
from PyQt6.QtGui import (
    QPainter, QColor, QFont, QFontDatabase, QBrush, QPen, QPixmap, QIcon,
    QPolygonF, QPalette, QLinearGradient, QPainterPath, QAction,
)
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QSystemTrayIcon, QMenu, QSizePolicy, QFileDialog,
)

try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
except ImportError:      # QtMultimedia is optional; afplay covers macOS anyway.
    QAudioOutput = None
    QMediaPlayer = None

# --------------------------------------------------------------------------- #
# Theme
# --------------------------------------------------------------------------- #

DEFAULT_INTERVAL_MINUTES = 90
DAILY_GOAL = 8

ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


def is_dark_mode(widget=None):
    """Detect the system appearance, preferring Qt's native colour-scheme hint."""
    app = QApplication.instance()
    if app is not None:
        hints = app.styleHints()
        scheme = getattr(hints, "colorScheme", None)
        if scheme is not None:
            try:
                return scheme() == Qt.ColorScheme.Dark
            except Exception:
                pass
    palette = widget.palette() if widget is not None else QApplication.palette()
    return palette.color(QPalette.ColorRole.Window).value() < 128


class Theme:
    """Colour tokens resolved once per repaint for the current appearance."""

    def __init__(self, dark):
        self.dark = dark
        if dark:
            self.glass_top = QColor(30, 41, 59, 232)
            self.glass_bottom = QColor(15, 23, 42, 224)
            self.border = QColor(255, 255, 255, 46)
            self.highlight = QColor(255, 255, 255, 30)
            self.shadow = QColor(0, 0, 0, 120)
            self.text = "#F1F5F9"
            self.text_muted = "#94A3B8"
            self.track = QColor(255, 255, 255, 38)
        else:
            self.glass_top = QColor(255, 255, 255, 240)
            self.glass_bottom = QColor(241, 245, 249, 228)
            self.border = QColor(255, 255, 255, 200)
            self.highlight = QColor(255, 255, 255, 170)
            self.shadow = QColor(15, 23, 42, 60)
            self.text = "#0F172A"
            self.text_muted = "#64748B"
            self.track = QColor(15, 23, 42, 28)
        self.accent = QColor(56, 189, 248)


def ui_font(size, bold=False):
    """System UI font at a given size.

    Built from QFontDatabase rather than a bare QFont() so Qt never falls back to
    the non-existent "Sans Serif" family (which costs ~100ms in alias lookup).
    """
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    font.setPointSizeF(size)
    font.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    return font


# --------------------------------------------------------------------------- #
# Glassmorphic card
# --------------------------------------------------------------------------- #

class GlassmorphicCard(QWidget):
    """Frosted, rounded card.

    The soft shadow is painted by hand rather than with QGraphicsDropShadowEffect:
    a graphics effect on a WA_TranslucentBackground widget renders through an
    opaque offscreen buffer, which is what produces the classic black box.
    """

    SHADOW_MARGIN = 16
    RADIUS = 22

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def content_margins(self, padding):
        m = self.SHADOW_MARGIN + padding
        return (m, m, m, m)

    def paintEvent(self, event):
        theme = Theme(is_dark_mode(self))
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            body = QRectF(self.rect()).adjusted(
                self.SHADOW_MARGIN, self.SHADOW_MARGIN,
                -self.SHADOW_MARGIN, -self.SHADOW_MARGIN,
            )
            if body.width() <= 0 or body.height() <= 0:
                return

            # Layered soft shadow.
            painter.setPen(Qt.PenStyle.NoPen)
            layers = 12
            for i in range(layers, 0, -1):
                spread = i * 1.15
                alpha = max(1, int(theme.shadow.alpha() / (layers * 1.6)))
                painter.setBrush(QBrush(QColor(
                    theme.shadow.red(), theme.shadow.green(), theme.shadow.blue(), alpha)))
                painter.drawRoundedRect(
                    body.adjusted(-spread, -spread + 2.0, spread, spread + 4.0),
                    self.RADIUS + spread, self.RADIUS + spread,
                )

            # Frosted body.
            gradient = QLinearGradient(body.topLeft(), body.bottomLeft())
            gradient.setColorAt(0.0, theme.glass_top)
            gradient.setColorAt(1.0, theme.glass_bottom)
            painter.setBrush(QBrush(gradient))
            painter.setPen(QPen(theme.border, 1.2))
            painter.drawRoundedRect(body, self.RADIUS, self.RADIUS)

            # Specular sheen across the top third.
            sheen = QPainterPath()
            sheen.addRoundedRect(body, self.RADIUS, self.RADIUS)
            painter.save()
            painter.setClipPath(sheen)
            gloss = QLinearGradient(
                body.topLeft(), QPointF(body.left(), body.top() + body.height() * 0.45))
            gloss.setColorAt(0.0, theme.highlight)
            gloss.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(gloss))
            painter.drawRect(body)
            painter.restore()
        finally:
            painter.end()


class ProgressPips(QWidget):
    """Eight little glasses showing today's progress."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedHeight(14)
        self.count = 0

    def set_count(self, count):
        self.count = max(0, min(DAILY_GOAL, count))
        self.update()

    def paintEvent(self, event):
        theme = Theme(is_dark_mode(self))
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            gap = 6.0
            pip_w = max(6.0, (self.width() - gap * (DAILY_GOAL - 1)) / DAILY_GOAL)
            y = (self.height() - 8) / 2.0
            for i in range(DAILY_GOAL):
                x = i * (pip_w + gap)
                painter.setBrush(QBrush(theme.accent if i < self.count else theme.track))
                painter.drawRoundedRect(QRectF(x, y, pip_w, 8), 4, 4)
        finally:
            painter.end()


# --------------------------------------------------------------------------- #
# Pixel character
# --------------------------------------------------------------------------- #

_sprites_cache = None
_sprites_loaded = False


class BuddySprites:
    """Frames sliced out of assets/buddy.png, with their segment map."""

    def __init__(self, frames, segments, fps, bottoms):
        self.frames = frames
        self.segments = segments          # name -> (start index, frame count)
        self.fps = float(fps) if fps else 24.0
        self.bottoms = bottoms            # lowest opaque row, per frame
        self.width = frames[0].width()
        self.height = frames[0].height()
        self.ground = max(bottoms)
        self.lift = max(1, self.ground - min(bottoms))


def load_buddy_sprites():
    """Load the sprite sheet once; None when it has not been built.

    tools/build_sprites.py renders it from character_animation.gif. If the
    assets are absent or malformed the widget falls back to the hand-drawn
    buddy, so a fresh checkout still runs and --smoke still passes.
    """
    global _sprites_cache, _sprites_loaded
    if _sprites_loaded:
        return _sprites_cache
    _sprites_loaded = True
    try:
        with open(os.path.join(ASSET_DIR, "buddy.json")) as fh:
            manifest = json.load(fh)
        sheet = QPixmap(os.path.join(ASSET_DIR, "buddy.png"))
        frame_w = int(manifest["frame_width"])
        frame_h = int(manifest["frame_height"])
        count = int(manifest["frame_count"])
        if sheet.isNull() or frame_w <= 0 or frame_h <= 0 or count <= 0:
            return None
        if sheet.width() < frame_w or sheet.height() < count * frame_h:
            return None

        frames = [sheet.copy(0, i * frame_h, frame_w, frame_h) for i in range(count)]
        segments = {}
        for name, seg in manifest.get("segments", {}).items():
            start, length = int(seg["start"]), int(seg["count"])
            if length > 0 and 0 <= start and start + length <= count:
                segments[name] = (start, length)
        if not segments:
            return None

        bottoms = [int(v) for v in manifest.get("bottoms", [])]
        if len(bottoms) != count:
            bottoms = [frame_h - 1] * count

        _sprites_cache = BuddySprites(frames, segments, manifest.get("fps"), bottoms)
    except (OSError, ValueError, KeyError, TypeError):
        _sprites_cache = None
    return _sprites_cache


class PixelCharacterWidget(QWidget):
    """The buddy: sprite-sheet playback, with the hand-drawn figure as fallback."""

    # state -> (segments played once as an intro, segment looped afterwards)
    TIMELINES = {
        "drink": (("wave",), "drink"),
        "cheer": ((), "cheer"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sprites = load_buddy_sprites()
        if self.sprites is None:
            self.setFixedSize(150, 210)
            self._interval = 33  # ~30 FPS
        else:
            # Tall enough to leave her feet level with the card's visible bottom
            # edge rather than with the window's, which sits SHADOW_MARGIN lower.
            height = max(self.sprites.height,
                         self.sprites.ground + 1 + GlassmorphicCard.SHADOW_MARGIN)
            self.setFixedSize(self.sprites.width, height)
            self._interval = max(1, int(round(1000.0 / self.sprites.fps)))
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        self.state = "drink"
        self.t = 0.0

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(self._interval)

    def set_state(self, state):
        self.state = state
        self.t = 0.0
        self.update()

    def stop(self):
        self.timer.stop()

    def start(self):
        if not self.timer.isActive():
            self.timer.start(self._interval)

    def _tick(self):
        self.t += self._interval / 1000.0
        self.update()

    # -- painting ----------------------------------------------------------- #

    def _current_frame(self):
        """Sheet index for the current clock, walking this state's timeline."""
        sprites = self.sprites
        intro, looping = self.TIMELINES.get(self.state, ((), "idle"))
        t = max(0.0, self.t)

        for name in intro:
            seg = sprites.segments.get(name)
            if seg is None:
                continue
            start, length = seg
            span = length / sprites.fps
            if t < span:
                return start + min(length - 1, int(t * sprites.fps))
            t -= span

        seg = sprites.segments.get(looping) or sprites.segments.get("idle")
        if seg is None:
            return 0
        start, length = seg
        return start + int(t * sprites.fps) % length

    def paintEvent(self, event):
        if self.sprites is None:
            self._paint_fallback()
            return

        sprites = self.sprites
        index = self._current_frame()
        painter = QPainter(self)
        try:
            # Contact shadow, squashed and faded as the cheer jump lifts her off.
            grounded = 1.0 - min(1.0, (sprites.ground - sprites.bottoms[index])
                                 / float(sprites.lift))
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(15, 23, 42, int(20 + 34 * grounded))))
            width = sprites.width * 0.38 * (0.55 + 0.45 * grounded)
            height = 3.0 + 5.0 * grounded
            painter.drawEllipse(QRectF((sprites.width - width) / 2.0,
                                       sprites.ground + 3.0 - height / 2.0,
                                       width, height))

            # Nearest-neighbour so the pixel art stays crisp when the Retina
            # backing store scales it up.
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            painter.drawPixmap(0, 0, sprites.frames[index])
        finally:
            painter.end()

    def _paint_fallback(self):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

            s = 3.4  # pixel size
            float_y = math.sin(self.t * 2.2) * 2.6
            if self.state == "cheer":
                float_y += -abs(math.sin(self.t * 6.0)) * 3.5

            skin = QColor(246, 205, 176)
            skin_dark = QColor(219, 168, 138)
            shirt = QColor(248, 250, 252)
            shirt_dark = QColor(219, 226, 235)
            jeans = QColor(59, 116, 196)
            jeans_dark = QColor(44, 92, 162)
            hair = QColor(38, 30, 34)
            shoes = QColor(238, 240, 246)
            lanyard = QColor(30, 41, 59)
            badge = QColor(139, 92, 246)

            def px(x, y, w, h, color):
                painter.setBrush(QBrush(color))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRect(QRectF(x * s, (y + float_y) * s, w * s, h * s))

            # Ground shadow — squashes as the character rises.
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            shrink = abs(float_y) * 0.5
            painter.setBrush(QBrush(QColor(15, 23, 42, 40)))
            painter.drawEllipse(QRectF((13 + shrink * 0.4) * s, 57.5 * s,
                                       (15 - shrink * 0.8) * s, 3.0 * s))
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

            # Legs and shoes.
            px(13, 37, 6, 17, jeans)
            px(21, 37, 6, 17, jeans)
            px(13, 50, 6, 4, jeans_dark)
            px(21, 50, 6, 4, jeans_dark)
            px(11.5, 54, 8, 4, shoes)
            px(20.5, 54, 8, 4, shoes)

            # Torso.
            px(13, 23, 14, 14, shirt)
            px(13, 34, 14, 3, shirt_dark)

            # Lanyard + badge.
            px(17.5, 23, 1, 8, lanyard)
            px(21.5, 23, 1, 8, lanyard)
            px(17.5, 30, 5, 5, badge)
            px(19, 31.5, 2, 1, QColor(255, 255, 255, 180))

            # Neck and head.
            px(17, 20, 6, 3, skin_dark)
            px(14, 9, 12, 12, skin)

            # Hair.
            px(12, 6, 16, 5, hair)
            px(11, 9, 3, 20, hair)
            px(26, 9, 3, 20, hair)
            px(14, 9, 5, 2, hair)

            if self.state == "cheer":
                self._paint_cheer(painter, px, s, float_y, skin, hair)
            else:
                self._paint_drink(painter, px, s, float_y, skin, hair)
        finally:
            painter.end()

    def _paint_drink(self, painter, px, s, float_y, skin, hair):
        """Raise a glass to the mouth on a loop and drain it."""
        cycle = 3.2
        phase = (self.t % cycle) / cycle
        # Ease the glass up, hold, then lower.
        if phase < 0.35:
            lift = (phase / 0.35) ** 0.6
        elif phase < 0.75:
            lift = 1.0
        else:
            lift = 1.0 - ((phase - 0.75) / 0.25) ** 1.6
        drinking = 0.3 < phase < 0.8

        # Face: content, eyes closed while sipping.
        if drinking:
            px(15.5, 13, 3, 1, hair)
            px(21.5, 13, 3, 1, hair)
        else:
            px(16, 12.5, 2, 2, QColor(24, 24, 28))
            px(22, 12.5, 2, 2, QColor(24, 24, 28))
            px(16.5, 13, 1, 1, QColor(255, 255, 255, 200))
            px(22.5, 13, 1, 1, QColor(255, 255, 255, 200))
        px(19, 17 - lift * 0.5, 2, 1 + lift, QColor(196, 122, 108))
        px(14.5, 15.5, 2, 1.5, QColor(244, 168, 156, 150))
        px(23.5, 15.5, 2, 1.5, QColor(244, 168, 156, 150))

        # Arm rises with the glass.
        arm_y = 26 - lift * 8
        px(26.5, arm_y, 3.5, 10 - lift * 2, skin)

        # Glass.
        gx = 27.0
        gy = 20.5 - lift * 8.0
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor(190, 226, 252, 220), 1.0))
        painter.setBrush(QBrush(QColor(214, 238, 255, 110)))
        painter.drawRoundedRect(QRectF(gx * s, (gy + float_y) * s, 7 * s, 10 * s), 3, 3)

        # Water drains as the sip progresses.
        fill = 1.0 - (0.75 * lift if drinking else 0.0)
        wh = 7.6 * max(0.12, fill)
        painter.setPen(Qt.PenStyle.NoPen)
        water = QLinearGradient(QPointF(0, (gy + 10) * s), QPointF(0, gy * s))
        water.setColorAt(0.0, QColor(14, 165, 233))
        water.setColorAt(1.0, QColor(103, 216, 255))
        painter.setBrush(QBrush(water))
        painter.drawRoundedRect(
            QRectF((gx + 0.7) * s, (gy + 9.2 - wh + float_y) * s, 5.6 * s, wh * s), 2, 2)
        painter.setBrush(QBrush(QColor(255, 255, 255, 90)))
        painter.drawRect(QRectF((gx + 1.2) * s, (gy + 1.2 + float_y) * s, 0.9 * s, 7 * s))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        # Resting arm.
        px(10.5, 26, 3.5, 10, skin)

    def _paint_cheer(self, painter, px, s, float_y, skin, hair):
        """Arms up, big smile, sparkles."""
        px(16, 12.5, 2, 2, QColor(24, 24, 28))
        px(22, 12.5, 2, 2, QColor(24, 24, 28))
        px(16.5, 12.5, 1, 1, QColor(255, 255, 255, 220))
        px(22.5, 12.5, 1, 1, QColor(255, 255, 255, 220))
        # Open smile.
        px(17.5, 16.5, 5, 2, QColor(150, 78, 72))
        px(18, 16.5, 4, 0.8, QColor(255, 255, 255, 210))
        px(14.5, 15, 2.5, 2, QColor(244, 150, 140, 170))
        px(23, 15, 2.5, 2, QColor(244, 150, 140, 170))

        wave = math.sin(self.t * 7.0) * 1.6
        px(8.5 + wave * 0.4, 11 + wave, 3.5, 13, skin)
        px(28 - wave * 0.4, 11 - wave, 3.5, 13, skin)

        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        sparks = (
            (6.0, 5.0, 0.0), (31.5, 7.5, 1.1), (33.0, 20.0, 2.2),
            (4.0, 18.0, 3.0), (19.0, 1.5, 0.6),
        )
        for sx, sy, offset in sparks:
            pulse = (math.sin(self.t * 5.0 + offset) + 1.0) / 2.0
            size = (1.4 + pulse * 2.4) * s
            alpha = int(90 + pulse * 165)
            color = QColor(255, 214, 102, alpha) if offset % 2 < 1 else QColor(125, 211, 252, alpha)
            painter.setBrush(QBrush(color))
            cx = sx * s
            cy = (sy + float_y) * s
            star = QPolygonF([
                QPointF(cx, cy - size), QPointF(cx + size * 0.38, cy - size * 0.38),
                QPointF(cx + size, cy), QPointF(cx + size * 0.38, cy + size * 0.38),
                QPointF(cx, cy + size), QPointF(cx - size * 0.38, cy + size * 0.38),
                QPointF(cx - size, cy), QPointF(cx - size * 0.38, cy - size * 0.38),
            ])
            painter.drawPolygon(star)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)


# --------------------------------------------------------------------------- #
# Reminder sound
# --------------------------------------------------------------------------- #

DEFAULT_SOUND = os.path.join(ASSET_DIR, "water_pour.wav")
DEFAULT_CHEER = os.path.join(ASSET_DIR, "cheer.wav")

# What the file picker offers. QtMultimedia leans on AVFoundation here, so this
# is roughly "whatever QuickTime opens".
SOUND_SUFFIXES = (".wav", ".mp3", ".m4a", ".aac", ".aiff", ".aif", ".caf",
                  ".flac", ".ogg", ".mp4")
SOUND_FILTER = ("Audio (%s);;All files (*)"
                % " ".join("*" + suffix for suffix in SOUND_SUFFIXES))


class AppSound:
    """One swappable cue — the reminder pour, or the cheer on logging a glass.

    Your own upload wins; the synthesised file in assets/ is the fallback, so
    there is always something to play. The choice lives in QSettings under
    `key`, which means it survives a restart.
    """

    def __init__(self, settings, key, default_path, default_label):
        self.settings = settings
        self.key = key
        self.default_path = default_path
        self.default_label = default_label
        self._player = None
        self._output = None

    # -- preference --------------------------------------------------------- #

    def custom_path(self):
        """The uploaded file, or "" when unset or since deleted."""
        value = self.settings.value("%s/path" % self.key, "", type=str) or ""
        return value if value and os.path.isfile(value) else ""

    def set_custom_path(self, path):
        self.settings.setValue("%s/path" % self.key, path or "")
        self.settings.sync()

    def is_muted(self):
        return bool(self.settings.value("%s/muted" % self.key, False, type=bool))

    def set_muted(self, muted):
        self.settings.setValue("%s/muted" % self.key, bool(muted))
        self.settings.sync()

    def resolved_path(self):
        """The file that will actually play: upload first, built-in second."""
        custom = self.custom_path()
        if custom:
            return custom
        return self.default_path if os.path.isfile(self.default_path) else ""

    def using_custom(self):
        return bool(self.custom_path())

    def label(self):
        custom = self.custom_path()
        if custom:
            return os.path.basename(custom)
        if os.path.isfile(self.default_path):
            return self.default_label
        return "No sound available"

    # -- playback ----------------------------------------------------------- #

    def play(self):
        if self.is_muted():
            return
        # --smoke renders offscreen; a self-test should not make noise.
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return
        path = self.resolved_path()
        if not path:
            return
        if not self._play_qt(path):
            self._play_afplay(path)

    def _play_qt(self, path):
        if QMediaPlayer is None or QAudioOutput is None:
            return False
        try:
            if self._player is None:
                self._output = QAudioOutput()
                self._output.setVolume(0.85)
                self._player = QMediaPlayer()
                self._player.setAudioOutput(self._output)
            # Re-set the source each time so a repeat reminder restarts the clip
            # instead of resuming from its finished end position.
            self._player.stop()
            self._player.setSource(QUrl.fromLocalFile(path))
            self._player.play()
            return self._player.error() == QMediaPlayer.Error.NoError
        except Exception:
            return False

    def _play_afplay(self, path):
        """macOS fallback if QtMultimedia cannot handle the file."""
        if sys.platform != "darwin" or not os.path.exists("/usr/bin/afplay"):
            return False
        try:
            return QProcess.startDetached("/usr/bin/afplay", [path])
        except Exception:
            return False


# --------------------------------------------------------------------------- #
# HUD
# --------------------------------------------------------------------------- #

class WaterReminderHUD(QWidget):
    """Frameless, fully translucent overlay that slides up from the corner."""

    WIDTH = 460
    HEIGHT = 258

    def __init__(self, on_action):
        super().__init__()
        self.on_action = on_action
        self._closing = False
        self._anim_group = None
        self._pending_close = None
        self.target_x = 0
        self.target_y = 0
        self._build_ui()
        self._apply_theme()

        app = QApplication.instance()
        if app is not None:
            signal = getattr(app.styleHints(), "colorSchemeChanged", None)
            if signal is not None:
                signal.connect(self._on_scheme_changed)

    # -- construction ------------------------------------------------------- #

    def _build_ui(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setWindowTitle("AquaReminder")

        self.resize(self.WIDTH, self.HEIGHT)
        self._reposition()

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.character = PixelCharacterWidget(self)
        root.addWidget(self.character, 0, Qt.AlignmentFlag.AlignBottom)

        self.card = GlassmorphicCard(self)
        root.addWidget(self.card, 1)

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(*self.card.content_margins(10))
        card_layout.setSpacing(8)

        self.title = QLabel("Time for a water break", self.card)
        self.title.setFont(ui_font(15, bold=True))
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.title)

        self.subtitle = QLabel("Your body will thank you 💧", self.card)
        self.subtitle.setFont(ui_font(11))
        self.subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.subtitle)

        self.pips = ProgressPips(self.card)
        card_layout.addWidget(self.pips)
        card_layout.addSpacing(2)

        self.btn_drank = QPushButton("✅  I drank water", self.card)
        self.btn_drank.setFont(ui_font(12, bold=True))
        self.btn_drank.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_drank.clicked.connect(self.handle_drank)
        card_layout.addWidget(self.btn_drank)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_10m = QPushButton("⏰  In 10 min", self.card)
        self.btn_10m.setFont(ui_font(11))
        self.btn_10m.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_10m.clicked.connect(lambda: self.dismiss("snooze_10m"))
        row.addWidget(self.btn_10m)

        self.btn_snooze = QPushButton("😴  Later", self.card)
        self.btn_snooze.setFont(ui_font(11))
        self.btn_snooze.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_snooze.clicked.connect(lambda: self.dismiss("snooze"))
        row.addWidget(self.btn_snooze)
        card_layout.addLayout(row)

    def _reposition(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            self.target_x, self.target_y = 80, 80
        else:
            geo = screen.availableGeometry()
            self.target_x = geo.x() + geo.width() - self.WIDTH - 24
            self.target_y = geo.y() + geo.height() - self.HEIGHT - 16
        self.move(self.target_x, self.target_y + 34)

    # -- theming ------------------------------------------------------------ #

    def _on_scheme_changed(self, *_args):
        self._apply_theme()

    def _apply_theme(self):
        theme = Theme(is_dark_mode(self))
        self.title.setStyleSheet(
            "color: %s; background: transparent; border: none;" % theme.text)
        self.subtitle.setStyleSheet(
            "color: %s; background: transparent; border: none;" % theme.text_muted)

        self.btn_drank.setStyleSheet("""
            QPushButton {
                background-color: #10B981; color: #FFFFFF;
                border: none; border-radius: 11px; padding: 10px 14px;
            }
            QPushButton:hover  { background-color: #0EA271; }
            QPushButton:pressed { background-color: #0B8A60; }
            QPushButton:disabled { background-color: #34D399; color: #F0FDF4; }
        """)
        secondary = """
            QPushButton {
                background-color: %s; color: %s;
                border: 1px solid %s; border-radius: 10px; padding: 7px 10px;
            }
            QPushButton:hover  { background-color: %s; }
        """
        if theme.dark:
            sheet = secondary % ("rgba(255,255,255,0.10)", "#E2E8F0",
                                 "rgba(255,255,255,0.16)", "rgba(255,255,255,0.18)")
        else:
            sheet = secondary % ("rgba(15,23,42,0.06)", "#334155",
                                 "rgba(15,23,42,0.10)", "rgba(15,23,42,0.12)")
        self.btn_10m.setStyleSheet(sheet)
        self.btn_snooze.setStyleSheet(sheet)
        self.update()

    # -- lifecycle ---------------------------------------------------------- #

    def popup(self, count_today=0):
        self._closing = False
        self._pending_close = None
        self.character.set_state("drink")
        self.character.start()
        self.title.setText("Time for a water break")
        self.subtitle.setText("Your body will thank you 💧")
        self.pips.set_count(count_today)
        self._set_buttons_enabled(True)
        self._apply_theme()

        self._reposition()
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()

        self._anim_group = QParallelAnimationGroup(self)

        fade_in = QPropertyAnimation(self, b"windowOpacity", self)
        fade_in.setDuration(460)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)

        slide_up = QPropertyAnimation(self, b"pos", self)
        slide_up.setDuration(460)
        slide_up.setStartValue(QPoint(self.target_x, self.target_y + 34))
        slide_up.setEndValue(QPoint(self.target_x, self.target_y))
        slide_up.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._anim_group.addAnimation(fade_in)
        self._anim_group.addAnimation(slide_up)
        self._anim_group.start()

    def _set_buttons_enabled(self, enabled):
        for btn in (self.btn_drank, self.btn_10m, self.btn_snooze):
            btn.setEnabled(enabled)

    def handle_drank(self):
        if self._closing:
            return
        self._set_buttons_enabled(False)
        self.character.set_state("cheer")
        self.title.setText("Nice one! 🎉")
        self.subtitle.setText("Staying hydrated looks good on you")
        self.on_action("drank")
        self.pips.set_count(self.pips.count + 1)
        QTimer.singleShot(2300, lambda: self.dismiss("done"))

    def dismiss(self, action):
        if self._closing:
            return
        self._closing = True
        self._pending_close = action
        self._set_buttons_enabled(False)

        self._anim_group = QParallelAnimationGroup(self)

        fade_out = QPropertyAnimation(self, b"windowOpacity", self)
        fade_out.setDuration(340)
        fade_out.setStartValue(self.windowOpacity())
        fade_out.setEndValue(0.0)
        fade_out.setEasingCurve(QEasingCurve.Type.OutCubic)

        slide_down = QPropertyAnimation(self, b"pos", self)
        slide_down.setDuration(340)
        slide_down.setStartValue(self.pos())
        slide_down.setEndValue(QPoint(self.target_x, self.target_y + 26))
        slide_down.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._anim_group.addAnimation(fade_out)
        self._anim_group.addAnimation(slide_down)
        self._anim_group.finished.connect(self._finish_close)
        self._anim_group.start()

    def _finish_close(self):
        action = self._pending_close
        self._pending_close = None
        self.character.stop()
        self.hide()
        self._closing = False
        if action is not None:
            self.on_action(action)


# --------------------------------------------------------------------------- #
# Status-bar application
# --------------------------------------------------------------------------- #

def make_droplet_icon(size=22):
    """Crisp Retina droplet drawn at the device pixel ratio."""
    app = QApplication.instance()
    dpr = 2.0
    if app is not None and app.primaryScreen() is not None:
        dpr = max(1.0, float(app.primaryScreen().devicePixelRatio()))

    pixmap = QPixmap(int(size * dpr), int(size * dpr))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.scale(dpr, dpr)

        path = QPainterPath()
        path.moveTo(size * 0.5, size * 0.10)
        path.quadTo(size * 0.86, size * 0.52, size * 0.84, size * 0.64)
        path.arcTo(QRectF(size * 0.16, size * 0.42, size * 0.68, size * 0.50), 0.0, -180.0)
        path.quadTo(size * 0.14, size * 0.52, size * 0.5, size * 0.10)
        path.closeSubpath()

        gradient = QLinearGradient(QPointF(size * 0.5, size * 0.08),
                                   QPointF(size * 0.5, size * 0.92))
        gradient.setColorAt(0.0, QColor(125, 211, 252))
        gradient.setColorAt(1.0, QColor(2, 132, 199))
        painter.setBrush(QBrush(gradient))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(path)

        painter.setBrush(QBrush(QColor(255, 255, 255, 120)))
        painter.drawEllipse(QRectF(size * 0.33, size * 0.56, size * 0.15, size * 0.20))
    finally:
        painter.end()
    return QIcon(pixmap)


POLICY_REGULAR = 0
POLICY_ACCESSORY = 1

_ns_cache = None


def _ns_app():
    """(ctypes, libobjc, NSApp) on macOS, else None. Loaded once."""
    global _ns_cache
    if _ns_cache is not None:
        return _ns_cache or None
    if sys.platform != "darwin":
        _ns_cache = False
        return None
    try:
        import ctypes
        import ctypes.util

        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]

        cls = objc.objc_getClass(b"NSApplication")
        shared = objc.objc_msgSend
        shared.restype = ctypes.c_void_p
        shared.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        ns_app = shared(ctypes.c_void_p(cls),
                        ctypes.c_void_p(objc.sel_registerName(b"sharedApplication")))
        if not ns_app:
            _ns_cache = False
            return None
        _ns_cache = (ctypes, objc, ns_app)
    except Exception:
        _ns_cache = False
        return None
    return _ns_cache


def set_activation_policy(policy):
    """Swap between dock-visible and status-bar-only."""
    handle = _ns_app()
    if not handle:
        return
    ctypes, objc, ns_app = handle
    try:
        send = objc.objc_msgSend
        send.restype = ctypes.c_bool
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
        send(ctypes.c_void_p(ns_app),
             ctypes.c_void_p(objc.sel_registerName(b"setActivationPolicy:")), policy)
    except Exception:
        pass  # Cosmetic only — a visible dock icon is harmless.


def activate_app():
    """Pull the app to the front.

    An accessory app has no dock icon to click, so without this the file picker
    can open behind whatever you were working in.
    """
    handle = _ns_app()
    if not handle:
        return
    ctypes, objc, ns_app = handle
    try:
        send = objc.objc_msgSend
        send.restype = None
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]
        send(ctypes.c_void_p(ns_app),
             ctypes.c_void_p(objc.sel_registerName(b"activateIgnoringOtherApps:")), True)
    except Exception:
        pass


def hide_dock_icon():
    """Make this a true status-bar-only app."""
    set_activation_policy(POLICY_ACCESSORY)


class AquaTrayApp:
    def __init__(self, argv=None):
        self.app = QApplication(argv if argv is not None else sys.argv)
        self.app.setApplicationName("AquaReminder")
        self.app.setApplicationDisplayName("AquaReminder")
        self.app.setQuitOnLastWindowClosed(False)

        self.app.setOrganizationName("AquaReminder")

        self.interval_minutes = DEFAULT_INTERVAL_MINUTES
        self.count_today = 0

        self.settings = QSettings("AquaReminder", "AquaReminder")
        self.sound = AppSound(self.settings, "sound",
                              DEFAULT_SOUND, "Default water sound")
        self.cheer_sound = AppSound(self.settings, "cheer",
                                    DEFAULT_CHEER, "Default cheer sound")
        self._sound_menus = []

        self.hud = WaterReminderHUD(self.handle_action)

        self.tray = QSystemTrayIcon(make_droplet_icon(), self.app)
        self.menu = QMenu()
        self._build_menu()
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_tray_activated)
        self._refresh_tooltip()
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

        self.timer = QTimer(self.app)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.trigger_reminder)
        self._arm(self.interval_minutes)

    # -- menu --------------------------------------------------------------- #

    def _build_menu(self):
        drink_now = QAction("💧  Drink water now", self.menu)
        drink_now.triggered.connect(self.trigger_reminder)
        self.menu.addAction(drink_now)

        self.status_action = QAction("", self.menu)
        self.status_action.setEnabled(False)
        self.menu.addAction(self.status_action)

        self.menu.addSeparator()
        interval_menu = self.menu.addMenu("Remind me every")
        self.interval_actions = []
        for minutes, label in ((30, "30 minutes"), (60, "1 hour"),
                               (90, "1 hour 30 minutes"), (120, "2 hours")):
            action = QAction(label, interval_menu)
            action.setCheckable(True)
            action.setChecked(minutes == self.interval_minutes)
            action.triggered.connect(
                lambda _checked=False, m=minutes: self.set_interval(m))
            interval_menu.addAction(action)
            self.interval_actions.append((minutes, action))

        self._add_sound_menu("Reminder sound", self.sound, "💧  Use default water sound")
        self._add_sound_menu("Cheer sound", self.cheer_sound, "🎉  Use default cheer sound")
        self._refresh_sound_menu()

        reset = QAction("Reset today's count", self.menu)
        reset.triggered.connect(self.reset_count)
        self.menu.addAction(reset)

        self.menu.addSeparator()
        quit_action = QAction("Quit AquaReminder", self.menu)
        quit_action.triggered.connect(self.app.quit)
        self.menu.addAction(quit_action)

    # -- sound -------------------------------------------------------------- #

    def _add_sound_menu(self, title, sound, default_label):
        """Build the identical five-item submenu for one cue."""
        menu = self.menu.addMenu(title)

        status = QAction("", menu)
        status.setEnabled(False)
        menu.addAction(status)
        menu.addSeparator()

        choose = QAction("📂  Choose audio file…", menu)
        choose.triggered.connect(lambda _checked=False, s=sound: self.choose_sound(s))
        menu.addAction(choose)

        use_default = QAction(default_label, menu)
        use_default.triggered.connect(
            lambda _checked=False, s=sound: self.use_default_sound(s))
        menu.addAction(use_default)

        preview = QAction("🔈  Play it now", menu)
        preview.triggered.connect(lambda _checked=False, s=sound: s.play())
        menu.addAction(preview)

        menu.addSeparator()
        mute = QAction("🔇  Mute", menu)
        mute.setCheckable(True)
        mute.setChecked(sound.is_muted())
        mute.triggered.connect(
            lambda checked=False, s=sound: self.set_muted(s, checked))
        menu.addAction(mute)

        self._sound_menus.append((sound, status, use_default, mute))

    def _refresh_sound_menu(self):
        for sound, status, use_default, mute in self._sound_menus:
            status.setText("   %s" % sound.label())
            use_default.setEnabled(sound.using_custom())
            mute.setChecked(sound.is_muted())

    def choose_sound(self, sound):
        """Pick your own audio for one of the cues."""
        # A status-bar-only app cannot take focus on its own, so briefly become a
        # regular app or the picker opens behind the frontmost window.
        set_activation_policy(POLICY_REGULAR)
        activate_app()
        try:
            start_dir = os.path.dirname(sound.custom_path()) or os.path.expanduser("~")
            path, _ = QFileDialog.getOpenFileName(
                None, "Choose sound", start_dir, SOUND_FILTER)
        finally:
            set_activation_policy(POLICY_ACCESSORY)

        if not path:
            return
        if not path.lower().endswith(SOUND_SUFFIXES):
            self.tray.showMessage(
                "AquaReminder",
                "That file type isn't supported — keeping the current sound.",
                QSystemTrayIcon.MessageIcon.Warning, 4000)
            return

        sound.set_custom_path(path)
        self._refresh_sound_menu()
        sound.play()               # immediate confirmation of what you picked

    def use_default_sound(self, sound):
        sound.set_custom_path("")
        self._refresh_sound_menu()
        sound.play()

    def set_muted(self, sound, muted):
        sound.set_muted(muted)
        self._refresh_sound_menu()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.trigger_reminder()

    def _refresh_tooltip(self):
        self.tray.setToolTip(
            "AquaReminder — %d/%d glasses today" % (self.count_today, DAILY_GOAL))
        self.status_action.setText(
            "   %d of %d glasses today" % (self.count_today, DAILY_GOAL))

    # -- behaviour ---------------------------------------------------------- #

    def _arm(self, minutes):
        self.timer.start(int(minutes * 60 * 1000))

    def set_interval(self, minutes):
        self.interval_minutes = minutes
        for value, action in self.interval_actions:
            action.setChecked(value == minutes)
        self._arm(minutes)

    def reset_count(self):
        self.count_today = 0
        self._refresh_tooltip()

    def trigger_reminder(self):
        self.timer.stop()
        self.sound.play()
        self.hud.popup(self.count_today)

    def handle_action(self, action):
        if action == "drank":
            self.cheer_sound.play()      # fires as she jumps
            self.count_today += 1
            self._refresh_tooltip()
        elif action == "snooze_10m":
            self._arm(10)
        elif action in ("snooze", "done"):
            self._arm(self.interval_minutes)

    def run(self):
        hide_dock_icon()
        return self.app.exec()


# --------------------------------------------------------------------------- #
# Entry point + headless self-test
# --------------------------------------------------------------------------- #

def smoke_test():
    """Exercise every paint path offscreen so painting bugs fail loudly."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    instance = AquaTrayApp(argv=[sys.argv[0]])
    hud = instance.hud

    hud.popup(3)
    hud.resize(hud.WIDTH, hud.HEIGHT)

    def render(tag):
        for widget in (hud, hud.card, hud.character, hud.pips):
            pixmap = QPixmap(max(1, widget.width()), max(1, widget.height()))
            pixmap.fill(Qt.GlobalColor.transparent)
            widget.render(pixmap)
        print("  rendered: %s" % tag)

    hud.character.set_state("drink")
    for step in range(7):
        hud.character.t = step * 0.5
        render("drink t=%.2f" % hud.character.t)

    hud.character.set_state("cheer")
    for step in range(7):
        hud.character.t = step * 0.5
        render("cheer t=%.2f" % hud.character.t)

    make_droplet_icon()

    # Sound: resolution and menu state only — play() is a no-op offscreen, so a
    # self-test never makes noise.
    for sound, default in ((instance.sound, DEFAULT_SOUND),
                           (instance.cheer_sound, DEFAULT_CHEER)):
        original = sound.custom_path()
        sound.set_custom_path("")
        assert sound.resolved_path() == default, "default %s missing" % sound.key
        assert not sound.using_custom()
        sound.set_custom_path("/nonexistent/never-here.mp3")
        assert sound.resolved_path() == default, "deleted upload must fall back"
        sound.set_custom_path(original)
        sound.play()
        print("  sound[%s]: %s -> %s"
              % (sound.key, sound.label(), os.path.basename(sound.resolved_path())))
    instance._refresh_sound_menu()

    instance.set_interval(30)
    instance.handle_action("drank")
    hud.dismiss("snooze")
    QTimer.singleShot(900, instance.app.quit)
    code = instance.app.exec()
    print("smoke test finished: exit=%d glasses=%d" % (code, instance.count_today))
    return code


def main():
    args = sys.argv[1:]
    if "--smoke" in args:
        return smoke_test()

    instance = AquaTrayApp()
    if "--demo" in args:
        QTimer.singleShot(600, instance.trigger_reminder)
    return instance.run()


if __name__ == "__main__":
    sys.exit(main())
