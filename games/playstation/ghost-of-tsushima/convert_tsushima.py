"""Convert Ghost of Tsushima Director's Cut PS5 saves to PC.

Each PS5 save folder holds a dis.dat zero-padded to a fixed slot size. The file is
<u32 magic, u32 length, u32 checksum, u32 count> followed by `count` 8-byte
save-format update IDs and the body. The platforms differ only in that ID list: the
PS5 has one PS5-only ID and a PlayStation marker, where the PC has a PC marker plus
one PC-only ID. The checksum is sum(bytes after the header) + length + magic + count.
Based on github.com/KOVRlN/ghostoftsushima-save-converter.

Output names: sdimg_suckerpunchAuto0 becomes auto.sav; the manual saves (including
sce_bu_ backups) are deduplicated and written oldest first as manual_0000.sav,
manual_0001.sav, ..., each with its original PS5 save time as the file time.
Keep the user.sav the PC game created; sce_sdmemory is not a game save.

Usage: python convert_tsushima.py <decrypted PPSA02225 dump folder> <output folder>
"""

import datetime
import hashlib
import os
import struct
import sys
import uuid
from pathlib import Path

PS5_ONLY = bytes.fromhex("a7d25b6bb1667f30")
PLAYSTATION_MARKER = bytes.fromhex("7e8a516a45ed4447")
PC_MARKER = bytes.fromhex("d5cf106b395e5d10")
PC_EXTRA = bytes.fromhex("bfe1bd115c5c51a2")
UUID_EPOCH = datetime.datetime(1582, 10, 15, tzinfo=datetime.timezone.utc)


def sfo_field(path, wanted):
    data = path.read_bytes()
    _, _, key_offset, data_offset, count = struct.unpack_from("<4sIIII", data, 0)
    for i in range(count):
        key_start, _, length, _, value_start = struct.unpack_from("<HHIII", data, 20 + i * 16)
        key_start += key_offset
        if data[key_start : data.index(b"\0", key_start)].decode() == wanted:
            return data[data_offset + value_start : data_offset + value_start + length]
    raise KeyError(f"{path}: no {wanted}")


def to_pc(data):
    magic, length, _, count = struct.unpack_from("<IIII", data, 0)
    if any(data[length:]):
        raise ValueError("non-zero bytes after the payload")
    ids = [data[0x10 + i * 8 : 0x18 + i * 8] for i in range(count)]
    body = data[0x10 + count * 8 : length]
    pc_ids = []
    for entry in ids:
        if entry == PLAYSTATION_MARKER:
            pc_ids += [PC_MARKER, PC_EXTRA]
        elif entry != PS5_ONLY:
            pc_ids.append(entry)
    content = b"".join(pc_ids) + body
    new_length = 0x10 + len(content)
    checksum = (sum(content) + new_length + magic + len(pc_ids)) & 0xFFFFFFFF
    return struct.pack("<IIII", magic, new_length, checksum, len(pc_ids)) + content


def load(folder):
    param = folder / "sce_sys" / "param.sfo"
    revision = uuid.UUID(bytes_le=sfo_field(param, "SAVEDATA_REVISION"))
    return {
        "payload": to_pc((folder / "dis.dat").read_bytes()),
        "saved_at": UUID_EPOCH + datetime.timedelta(microseconds=revision.time // 10),
        "subtitle": sfo_field(param, "SUBTITLE").rstrip(b"\0").decode(),
    }


def write(save, path):
    path.write_bytes(save["payload"])
    timestamp = save["saved_at"].timestamp()
    os.utime(path, (timestamp, timestamp))
    print(f"{path.name:16} {save['saved_at']:%Y-%m-%d %H:%M}  {save['subtitle']}")


def convert(source_dir, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    write(load(source_dir / "sdimg_suckerpunchAuto0"), output_dir / "auto.sav")
    manual_saves = {}
    for folder in sorted(source_dir.glob("sdimg_*suckerpunchUser*")):
        save = load(folder)
        manual_saves.setdefault(hashlib.sha256(save["payload"]).digest(), save)
    for index, save in enumerate(sorted(manual_saves.values(), key=lambda save: save["saved_at"])):
        write(save, output_dir / f"manual_{index:04}.sav")


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    convert(Path(sys.argv[1]), Path(sys.argv[2]))


if __name__ == "__main__":
    main()
