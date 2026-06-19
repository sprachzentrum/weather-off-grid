#!/usr/bin/env python3
"""
Generate the PWA icons (192x192, 512x512) with no external dependencies.

Draws a "sun" mark - a cyan core disc with a ring of dots on the dark brand
background - using only the Python standard library (zlib + struct to emit PNG).
Re-run after changing the brand colours:  python generate_icons.py
"""
import math
import struct
import zlib

BG = (15, 15, 26)        # #0f0f1a
ACCENT = (0, 212, 170)   # #00d4aa


def _png(width: int, height: int, pixels: bytes) -> bytes:
    def chunk(typ: bytes, data: bytes) -> bytes:
        body = typ + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)  # filter type 0 (none)
        raw.extend(pixels[y * stride:(y + 1) * stride])
    idat = zlib.compress(bytes(raw), 9)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def render(size: int) -> bytes:
    cx = cy = size / 2.0
    core_r = size * 0.20
    dot_orbit = size * 0.345
    dot_r = size * 0.052
    n_dots = 12

    dots = [
        (cx + dot_orbit * math.cos(2 * math.pi * i / n_dots),
         cy + dot_orbit * math.sin(2 * math.pi * i / n_dots))
        for i in range(n_dots)
    ]

    px = bytearray(size * size * 3)
    for y in range(size):
        for x in range(size):
            color = BG
            if (x - cx) ** 2 + (y - cy) ** 2 <= core_r ** 2:
                color = ACCENT
            else:
                for dx, dy in dots:
                    if (x - dx) ** 2 + (y - dy) ** 2 <= dot_r ** 2:
                        color = ACCENT
                        break
            o = (y * size + x) * 3
            px[o], px[o + 1], px[o + 2] = color
    return _png(size, size, bytes(px))


if __name__ == "__main__":
    for size in (192, 512):
        data = render(size)
        with open(f"icon-{size}.png", "wb") as fh:
            fh.write(data)
        print(f"wrote icon-{size}.png ({len(data)} bytes)")
