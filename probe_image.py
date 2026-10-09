"""Small randomized PNG used to verify actual image interpretation."""
import base64
import random
import struct
import zlib


def image_probe():
    colors = [("red", (240, 20, 20)), ("green", (20, 200, 30)),
              ("blue", (20, 40, 240)), ("yellow", (245, 230, 20))]
    random.SystemRandom().shuffle(colors)
    size = 128
    raw = b"".join(b"\0" + b"".join(bytes(colors[(y // 64) * 2 + x // 64][1])
                                    for x in range(size)) for y in range(size))
    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", size, size, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii"), colors[0][0]
