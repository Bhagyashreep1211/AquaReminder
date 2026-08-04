#!/usr/bin/env python3
"""Turn character_animation.gif into a transparent sprite sheet for the HUD.

The source GIF renders the character over a *baked-in* grey checkerboard — those
are real opaque pixels, not alpha — so dropping it straight onto the frosted
card would paint a grey slab. This script keys the checkerboard out, drops the
stray background twinkles, crops every frame to one shared box (so the motion is
preserved instead of each frame being re-centred), and writes:

    assets/buddy.png    — vertical sprite sheet, straight alpha
    assets/buddy.json   — frame size + named segments for app.py

Run once after changing the GIF:

    python3 tools/build_sprites.py
"""

import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect
from PyQt6.QtGui import QImage, QImageReader, QPainter
from PyQt6.QtWidgets import QApplication

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "character_animation.gif")
ASSETS = os.path.join(ROOT, "assets")

# Frame ranges in the source GIF, end-exclusive. The GIF also holds a walk-around
# (15-47) and a pointing beat (48-62) that the HUD has no use for. Cheer stops at
# 138 because the source bakes a "cheer!" speech bubble into 138-149, and the HUD
# says its own piece in the card next to her.
SEGMENTS = {
    "idle":  (0, 15),
    "drink": (63, 96),
    "wave":  (96, 114),
    "cheer": (114, 138),
}

PAD = 6           # breathing room around the shared crop box
MARGIN = 14       # how close a stray blob must hug the body to be kept


def load_frames(path):
    reader = QImageReader(path)
    frames = []
    while True:
        image = reader.read()
        if image.isNull():
            break
        frames.append(image.convertToFormat(QImage.Format.Format_ARGB32))
    if not frames:
        raise SystemExit("no frames read from %s" % path)
    return frames


def raw(image):
    """Frame as a mutable BGRA bytearray plus its row stride."""
    stride = image.bytesPerLine()
    bits = image.bits()
    bits.setsize(stride * image.height())
    return bytearray(bits), stride


def is_background(b, g, r):
    """True for the grey checkerboard: desaturated and in its brightness band."""
    hi = max(r, g, b)
    lo = min(r, g, b)
    return (hi - lo) <= 14 and 100 <= hi <= 205


def flood_background(buf, stride, w, h):
    """Mark checkerboard reachable from the border.

    Flooding inward from the edges rather than testing pixels independently is
    what keeps her white top and pale shoes intact — those read as "grey-ish"
    on their own, but they are enclosed by the character, so the fill never
    reaches them.
    """
    bg = bytearray(w * h)
    stack = []

    def maybe_push(x, y):
        i = y * w + x
        if bg[i]:
            return
        o = y * stride + x * 4
        if is_background(buf[o], buf[o + 1], buf[o + 2]):
            bg[i] = 1
            stack.append((x, y))

    for x in range(w):
        maybe_push(x, 0)
        maybe_push(x, h - 1)
    for y in range(h):
        maybe_push(0, y)
        maybe_push(w - 1, y)

    while stack:
        x, y = stack.pop()
        if x > 0:
            maybe_push(x - 1, y)
        if x + 1 < w:
            maybe_push(x + 1, y)
        if y > 0:
            maybe_push(x, y - 1)
        if y + 1 < h:
            maybe_push(x, y + 1)
    return bg


def components(bg, w, h):
    """Connected runs of kept (non-background) pixels, as (size, bbox, pixels)."""
    seen = bytearray(w * h)
    out = []
    for sy in range(h):
        for sx in range(w):
            start = sy * w + sx
            if bg[start] or seen[start]:
                continue
            stack = [(sx, sy)]
            seen[start] = 1
            pixels = []
            minx = maxx = sx
            miny = maxy = sy
            while stack:
                x, y = stack.pop()
                pixels.append((x, y))
                if x < minx: minx = x
                if x > maxx: maxx = x
                if y < miny: miny = y
                if y > maxy: maxy = y
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if 0 <= nx < w and 0 <= ny < h:
                        i = ny * w + nx
                        if not bg[i] and not seen[i]:
                            seen[i] = 1
                            stack.append((nx, ny))
            out.append((len(pixels), (minx, miny, maxx, maxy), pixels))
    return out


def key_frame(image):
    """Alpha-keyed copy of one frame, plus the bbox of what survived."""
    w, h = image.width(), image.height()
    buf, stride = raw(image)
    bg = flood_background(buf, stride, w, h)

    blobs = components(bg, w, h)
    if not blobs:
        return None, None
    blobs.sort(key=lambda b: b[0], reverse=True)
    bminx, bminy, bmaxx, bmaxy = blobs[0][1]          # the character herself

    # Keep the body, plus any blob hugging it (the cheer sparkles live just off
    # her hands). Twinkles scattered across the backdrop are dropped no matter
    # how big they are — proximity to the body is the test, not size.
    keep = []
    for size, (minx, miny, maxx, maxy), pixels in blobs:
        near = (minx >= bminx - MARGIN and maxx <= bmaxx + MARGIN
                and miny >= bminy - MARGIN and maxy <= bmaxy + MARGIN)
        if near:
            keep.append((minx, miny, maxx, maxy, pixels))
    if not keep:
        return None, None

    out = QImage(w, h, QImage.Format.Format_ARGB32)
    out.fill(0)
    dst, dstride = raw(out)
    minx = min(k[0] for k in keep)
    miny = min(k[1] for k in keep)
    maxx = max(k[2] for k in keep)
    maxy = max(k[3] for k in keep)
    for _, _, _, _, pixels in keep:
        for x, y in pixels:
            s = y * stride + x * 4
            d = y * dstride + x * 4
            dst[d:d + 3] = buf[s:s + 3]
            dst[d + 3] = 255

    result = QImage(bytes(dst), w, h, dstride, QImage.Format.Format_ARGB32).copy()
    return result, (minx, miny, maxx, maxy)


def main():
    app = QApplication([sys.argv[0]])  # noqa: F841 — QImage needs a Q*Application

    frames = load_frames(SOURCE)
    print("source: %d frames, %dx%d" % (len(frames), frames[0].width(), frames[0].height()))

    order = []
    for name, (start, end) in SEGMENTS.items():
        order.append((name, list(range(start, min(end, len(frames))))))

    keyed = {}
    box = None
    for name, indices in order:
        for i in indices:
            image, bbox = key_frame(frames[i])
            if image is None:
                continue
            keyed[i] = image
            box = bbox if box is None else (
                min(box[0], bbox[0]), min(box[1], bbox[1]),
                max(box[2], bbox[2]), max(box[3], bbox[3]),
            )
        print("  keyed %-6s %d frames" % (name, len(indices)))

    src_w, src_h = frames[0].width(), frames[0].height()
    crop = QRect(
        max(0, box[0] - PAD),
        max(0, box[1] - PAD),
        min(src_w, box[2] + PAD + 1) - max(0, box[0] - PAD),
        min(src_h, box[3] + PAD + 1) - max(0, box[1] - PAD),
    )
    print("shared crop: %dx%d at (%d,%d)" % (crop.width(), crop.height(), crop.x(), crop.y()))

    # One shared crop for every frame keeps her feet planted and lets the cheer
    # jump actually leave the ground, instead of each frame being re-centred.
    layout = []
    sheet_frames = []
    for name, indices in order:
        start = len(sheet_frames)
        for i in indices:
            if i in keyed:
                sheet_frames.append(keyed[i])
        layout.append((name, start, len(sheet_frames) - start))

    sheet = QImage(crop.width(), crop.height() * len(sheet_frames),
                   QImage.Format.Format_ARGB32)
    sheet.fill(0)
    painter = QPainter(sheet)
    for row, image in enumerate(sheet_frames):
        painter.drawImage(0, row * crop.height(), image,
                          crop.x(), crop.y(), crop.width(), crop.height())
    painter.end()

    # Lowest opaque row per frame, in cropped coordinates. The HUD uses it to sit
    # a contact shadow under her feet and shrink it as the cheer jump lifts off.
    bottoms = []
    for row in range(len(sheet_frames)):
        base = row * crop.height()
        lowest = crop.height() - 1
        for y in range(crop.height() - 1, -1, -1):
            if any(sheet.pixelColor(x, base + y).alpha() > 0
                   for x in range(crop.width())):
                lowest = y
                break
        bottoms.append(lowest)

    os.makedirs(ASSETS, exist_ok=True)
    png = os.path.join(ASSETS, "buddy.png")
    sheet.save(png)

    manifest = {
        "frame_width": crop.width(),
        "frame_height": crop.height(),
        "frame_count": len(sheet_frames),
        "fps": 24,
        "bottoms": bottoms,
        "segments": {name: {"start": start, "count": count}
                     for name, start, count in layout},
    }
    with open(os.path.join(ASSETS, "buddy.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")

    print("wrote %s (%.1f KB) and buddy.json"
          % (png, os.path.getsize(png) / 1024.0))
    for name, start, count in layout:
        print("  %-6s start=%-4d count=%d" % (name, start, count))
    return 0


if __name__ == "__main__":
    sys.exit(main())
