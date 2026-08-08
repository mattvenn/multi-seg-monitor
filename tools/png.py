"""Minimal 8 bit RGB PNG reader and writer, so nothing here needs an imaging
library.

The reader exists for the gold image comparison in the testbench, and handles
the subset the writer emits -- 8 bit truecolour, not interlaced -- plus all five
row filters, so a gold file re-saved by some other tool still loads.
"""

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


def read_png(path):
    """Inverse of write_png: returns (width, height, px) with px flat RGB."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")

    width = height = None
    idat = bytearray()
    pos = 8
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        tag = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        pos += 12 + length  # length + tag + body + crc
        if tag == b"IHDR":
            width, height, depth, colour, _, _, interlace = struct.unpack(
                ">IIBBBBB", body
            )
            if (depth, colour, interlace) != (8, 2, 0):
                raise ValueError(
                    f"{path}: only 8 bit RGB without interlacing is supported, "
                    f"got depth {depth} colour type {colour} interlace {interlace}"
                )
        elif tag == b"IDAT":
            idat += body  # a large image may be split across several
        elif tag == b"IEND":
            break

    if width is None:
        raise ValueError(f"{path} has no IHDR")

    raw = zlib.decompress(bytes(idat))
    stride = width * 3
    expect = height * (stride + 1)
    if len(raw) != expect:
        raise ValueError(f"{path}: {len(raw)} bytes of image data, expected {expect}")

    px = bytearray(height * stride)
    prev = bytearray(stride)
    pos = 0
    for y in range(height):
        ftype = raw[pos]
        pos += 1
        line = bytearray(raw[pos : pos + stride])
        pos += stride
        # Each filter predicts a byte from its left neighbour (a), the byte
        # above (b) and the one above-left (c); undo it in place, left to right,
        # so already-corrected bytes are what later ones refer back to.
        if ftype == 1:  # Sub
            for i in range(3, stride):
                line[i] = (line[i] + line[i - 3]) & 0xFF
        elif ftype == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:  # Average
            for i in range(stride):
                a = line[i - 3] if i >= 3 else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:  # Paeth
            for i in range(stride):
                a = line[i - 3] if i >= 3 else 0
                c = prev[i - 3] if i >= 3 else 0
                b = prev[i]
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                if pa <= pb and pa <= pc:
                    pred = a
                elif pb <= pc:
                    pred = b
                else:
                    pred = c
                line[i] = (line[i] + pred) & 0xFF
        elif ftype != 0:
            raise ValueError(f"{path}: row {y} has unknown filter type {ftype}")
        px[y * stride : (y + 1) * stride] = line
        prev = line

    return width, height, list(px)
