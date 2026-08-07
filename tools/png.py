"""Minimal 8 bit RGB PNG writer, so nothing here needs an imaging library."""

import struct
import zlib


def write_png(path, width, height, px):
    """px is a flat sequence of length width*height*3, values 0..255."""
    raw = b"".join(
        b"\x00" + bytes(px[y * width * 3 : (y + 1) * width * 3]) for y in range(height)
    )

    def chunk(tag, data):
        body = tag + data
        return (
            struct.pack(">I", len(data))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(raw, 9)))
        f.write(chunk(b"IEND", b""))
