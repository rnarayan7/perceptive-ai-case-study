"""A minimal, dependency-free PNG codec (8-bit).

Enough to read the figure rasters (RGB/RGBA/grayscale/palette, 8 bits/channel)
and to write RGB PNGs for synthetic test images. PNG is chunked and zlib-
compressed, both in the standard library, so no Pillow is needed.

Not a full implementation: 8-bit depth only, no interlacing. That covers the
pdfimages-extracted figures (verified 8-bit RGB) and anything we generate.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import List, Tuple, Union

_SIG = b"\x89PNG\r\n\x1a\n"

# color_type -> channels
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


class RawImage:
    """Decoded pixels as flat RGB bytes (3 per pixel), row-major top to bottom."""

    def __init__(self, width: int, height: int, rgb: bytearray) -> None:
        self.width = width
        self.height = height
        self.rgb = rgb  # length width*height*3

    def pixel(self, x: int, y: int) -> Tuple[int, int, int]:
        i = (y * self.width + x) * 3
        return self.rgb[i], self.rgb[i + 1], self.rgb[i + 2]


def read_png(source: Union[str, Path, bytes]) -> RawImage:
    """Decode a PNG into a :class:`RawImage` (always normalized to RGB)."""
    data = source if isinstance(source, (bytes, bytearray)) else Path(source).read_bytes()
    if data[:8] != _SIG:
        raise ValueError("not a PNG (bad signature)")
    pos = 8
    width = height = bit_depth = color_type = 0
    palette: List[Tuple[int, int, int]] = []
    idat = bytearray()
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length  # 4 len + 4 type + data + 4 crc
        if ctype == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(">IIBB", chunk[:10])
            if bit_depth != 8:
                raise ValueError(f"unsupported bit depth {bit_depth} (only 8)")
            if color_type not in _CHANNELS:
                raise ValueError(f"unsupported color type {color_type}")
        elif ctype == b"PLTE":
            palette = [tuple(chunk[i:i + 3]) for i in range(0, len(chunk), 3)]
        elif ctype == b"IDAT":
            idat += chunk
        elif ctype == b"IEND":
            break

    channels = _CHANNELS[color_type]
    raw = zlib.decompress(bytes(idat))
    rows = _unfilter(raw, width, height, channels)
    rgb = _to_rgb(rows, width, height, color_type, channels, palette)
    return RawImage(width, height, rgb)


def _unfilter(raw: bytes, width: int, height: int, channels: int) -> bytearray:
    """Reverse PNG scanline filters; return raw channel bytes (no filter bytes)."""
    stride = width * channels
    out = bytearray()
    prev = bytearray(stride)
    pos = 0
    for _ in range(height):
        ftype = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        _apply_filter(ftype, line, prev, channels)
        out += line
        prev = line
    return out


def _apply_filter(ftype: int, line: bytearray, prev: bytearray, bpp: int) -> None:
    if ftype == 0:
        return
    for i in range(len(line)):
        a = line[i - bpp] if i >= bpp else 0            # left
        b = prev[i]                                     # up
        c = prev[i - bpp] if i >= bpp else 0            # up-left
        if ftype == 1:
            line[i] = (line[i] + a) & 0xFF
        elif ftype == 2:
            line[i] = (line[i] + b) & 0xFF
        elif ftype == 3:
            line[i] = (line[i] + (a + b) // 2) & 0xFF
        elif ftype == 4:
            line[i] = (line[i] + _paeth(a, b, c)) & 0xFF
        else:
            raise ValueError(f"unknown filter type {ftype}")


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _to_rgb(rows, width, height, color_type, channels, palette) -> bytearray:
    """Normalize decoded channel bytes to flat RGB."""
    n = width * height
    rgb = bytearray(n * 3)
    for i in range(n):
        base = i * channels
        if color_type == 2 or color_type == 6:      # RGB / RGBA
            rgb[i * 3:i * 3 + 3] = rows[base:base + 3]
        elif color_type == 0 or color_type == 4:    # gray / gray+alpha
            g = rows[base]
            rgb[i * 3] = rgb[i * 3 + 1] = rgb[i * 3 + 2] = g
        elif color_type == 3:                        # palette index
            r, g, b = palette[rows[base]]
            rgb[i * 3], rgb[i * 3 + 1], rgb[i * 3 + 2] = r, g, b
    return rgb


def write_png(width: int, height: int, rgb: Union[bytes, bytearray]) -> bytes:
    """Encode flat RGB bytes to a PNG (filter 0). Used to build test images."""
    if len(rgb) != width * height * 3:
        raise ValueError("rgb length must be width*height*3")
    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)  # filter: None
        raw += rgb[y * stride:(y + 1) * stride]
    out = bytearray(_SIG)
    out += _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    out += _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    out += _chunk(b"IEND", b"")
    return bytes(out)


def _chunk(ctype: bytes, data: bytes) -> bytes:
    body = ctype + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
