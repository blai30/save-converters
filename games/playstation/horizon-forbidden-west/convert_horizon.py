"""Convert Horizon Forbidden West PS5 saves to PC (Complete Edition).

The PS5 checkpoint.dat is the same checkpoint the PC game writes: a 0x200-byte header
("HRZ2-Release ..." build string, save type, MD5 of the body at 0x1EC, body length at
0x1FC) followed by the body. The PC <slot>.dat wraps it unchanged in a GGDS container:

    0x000  char[4]      "GGDS"
    0x004  u32          1
    0x008  u32          save list param, 0x0644F4A9 on PC (PS5: 0x0444F4A9)
    0x00C  u32          checkpoint length
    0x010  u32          thumbnail length
    0x014  char[0x80]   title, param.sfo MAINTITLE
    0x094  char[0x100]  subtitle, param.sfo SUBTITLE without "Horizon Forbidden West - "
    0x194  checkpoint, then a 480x270 PNG thumbnail (icon0.png resized)

GGDS layout from gist.github.com/Timo654/1b6f28f19df9c6b5b084fdfe9b1e5e71 and
github.com/zakuciael/hfw-save-converter.

Output names: sdimg_QUICKSAVE4 becomes quicksave4.dat, and so on. The profile and the
sce_bu_ backups are skipped; the PC game creates its own profile.

Usage: python convert_horizon.py <decrypted PPSA01521 dump folder> <output folder>
"""

import functools
import hashlib
import io
import struct
import sys
from pathlib import Path

from PIL import Image

# Low 24 bits are the save data version, the top byte differs per platform.
PC_LIST_PARAM = 0x0644F4A9
VERSION_MASK = 0xFFFFFF
SUBTITLE_PREFIX = "Horizon Forbidden West - "
CHECKPOINT_HEADER_SIZE = 0x200
THUMBNAIL_SIZE = (480, 270)


def read_sfo(path):
    data = path.read_bytes()
    key_table, data_table, count = struct.unpack_from("<III", data, 0x08)
    fields = {}
    for i in range(count):
        key_offset, _, length, _, value_offset = struct.unpack_from("<HHIII", data, 0x14 + i * 0x10)
        key_start = key_table + key_offset
        key = data[key_start : data.index(b"\0", key_start)].decode()
        fields[key] = data[data_table + value_offset : data_table + value_offset + length]
    return fields


def sfo_text(fields, key):
    return fields[key].rstrip(b"\0").decode()


def check_checkpoint(checkpoint, slot):
    body = checkpoint[CHECKPOINT_HEADER_SIZE:]
    stored_md5 = checkpoint[0x1EC:0x1FC]
    stored_length = struct.unpack_from("<I", checkpoint, 0x1FC)[0]
    if not checkpoint.startswith(b"HRZ2") or stored_length != len(body) or stored_md5 != hashlib.md5(body).digest():
        raise ValueError(f"{slot}: not a valid Horizon Forbidden West checkpoint")


@functools.cache
def thumbnail(icon):
    buffer = io.BytesIO()
    Image.open(io.BytesIO(icon)).convert("RGB").resize(THUMBNAIL_SIZE, Image.Resampling.LANCZOS).save(buffer, "PNG")
    return buffer.getvalue()


def convert_slot(slot):
    checkpoint = (slot / "checkpoint.dat").read_bytes()
    check_checkpoint(checkpoint, slot)
    fields = read_sfo(slot / "sce_sys" / "param.sfo")
    list_param = struct.unpack("<I", fields["SAVEDATA_LIST_PARAM"])[0]
    if list_param & VERSION_MASK != PC_LIST_PARAM & VERSION_MASK:
        raise ValueError(f"{slot}: save data version differs from the PC version")
    title = sfo_text(fields, "MAINTITLE").encode()
    subtitle = sfo_text(fields, "SUBTITLE").removeprefix(SUBTITLE_PREFIX).encode()
    image = thumbnail((slot / "sce_sys" / "icon0.png").read_bytes())
    header = struct.pack("<4sIIII", b"GGDS", 1, PC_LIST_PARAM, len(checkpoint), len(image))
    header += title.ljust(0x80, b"\0")[:0x80] + subtitle.ljust(0x100, b"\0")[:0x100]
    name = sfo_text(fields, "SAVEDATA_DIRECTORY").lower() + ".dat"
    return name, header + checkpoint + image, sfo_text(fields, "MAINTITLE")


def convert(source_dir, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    for slot in sorted(source_dir.glob("sdimg_*SAVE*")):
        if slot.name.startswith("sdimg_sce_bu_"):
            continue
        name, data, title = convert_slot(slot)
        (output_dir / name).write_bytes(data)
        print(f"{name:24} {title}")


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    convert(Path(sys.argv[1]), Path(sys.argv[2]))


if __name__ == "__main__":
    main()
