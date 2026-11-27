"""Draws the app icon (no image library needed): a rounded terracotta tile with a white spark. Writes icon.png (512) and icon.ico (256 PNG inside)."""
import math, struct, zlib
from pathlib import Path

def render(n):
    px = bytearray()
    r = n * 0.22
    for y in range(n):
        px.append(0)
        for x in range(n):
            # rounded square mask with 4x supersampling for smooth edges
            cov = spark = 0
            for sy in (0.125, 0.375, 0.625, 0.875):
                for sx in (0.125, 0.375, 0.625, 0.875):
                    fx, fy = x + sx, y + sy
                    dx = max(r - fx, 0, fx - (n - r)); dy = max(r - fy, 0, fy - (n - r))
                    if dx * dx + dy * dy <= r * r:
                        cov += 1
                        # 4-point spark: |u|^p + |v|^p <= 1 with p < 1 gives concave star arms
                        u, v = (fx - n / 2) / (n * 0.34), (fy - n / 2) / (n * 0.34)
                        if abs(u) ** 0.55 + abs(v) ** 0.55 <= 1:
                            spark += 1
            a = cov / 16
            if a == 0:
                px += b"\0\0\0\0"; continue
            s = spark / max(cov, 1)
            top = 1 - y / n
            base = (int(194 + 20 * top), int(96 + 18 * top), int(62 + 10 * top))
            col = tuple(int(base[i] * (1 - s) + 255 * s) for i in range(3))
            px += bytes(col) + bytes([int(255 * a)])
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(bytes(px), 9)) + chunk(b"IEND", b"")

here = Path(__file__).parent
(here / "icon.png").write_bytes(render(512))
p256 = render(256)
(here / "icon.ico").write_bytes(struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(p256), 22) + p256)
print("ok")
