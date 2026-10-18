"""Convert Mega Man 11 Switch saves to the PC (Steam) format.

Both platforms store each file (SYSTEM, GAMEDATA, REPLAY) as a table of named entries,
where an entry is a 32-bit name hash, a kind, and either an inline value or a struct blob.
The platforms differ in:

- Container: Switch is plaintext with a 0x10-byte header. PC has a 0x40-byte header holding
  a SHA-1 of the payload, and the whole file is Blowfish ECB encrypted.
- PC adds SteamID entries (sys_stID, gem_stID, ...), which are taken from a PC template.
- Some structs have platform-specific layouts. The PC loader silently skips any blob whose
  size differs from its own struct, so those are remapped field by field.

Usage: python convert_mm11.py <switch save dir> <pc template dir> <output dir>

The PC template is an existing Steam save folder from the same account (used for the
SteamID, data versions, and PC-only settings such as keyboard bindings and resolution).
"""

import hashlib
import struct
import sys
from pathlib import Path

from Crypto.Cipher import Blowfish

FILES = ("SYSTEM.bin", "GAMEDATA.bin", "REPLAY.bin")

# From game.exe (save manager constructor).
BLOWFISH_KEY = b"!!robomono_ororhooooon!!"
PC_MAGIC = 1
PC_HEADER_SIZE = 0x40
SWITCH_HEADER_SIZE = 0x10
DIGEST_OFFSET = 0x0C
PAYLOAD_SIZE_OFFSET = 0x20
ENTRY_HEADER_SIZE = 0x10
BLOB_KIND = 0x0C

# (switch offset, pc offset, length) ranges copied from a Switch struct onto the PC
# template, keyed by (switch size, pc size). Unlisted ranges keep the PC template values.
STRUCT_REMAPS = {
    # gamedata0-7: identical except Switch appends a 20-record timestamped list.
    (0x538, 0x3F8): [(0x000, 0x000, 0x3F8)],
    # systemdata: PC keeps its own bindings, audio/display options and SteamID. Only the
    # progress fields are carried over: two sets of five unlock bitmasks and two 70-bit sets.
    (0x228, 0x23C): [(0x1DC, 0x1E4, 0x28), (0x208, 0x220, 0x18)],
}


def swap_words(data: bytes) -> bytes:
    # The game's Blowfish treats each 32-bit half as little-endian.
    return b"".join(data[i : i + 4][::-1] for i in range(0, len(data), 4))


def decrypt(data: bytes) -> bytes:
    return swap_words(Blowfish.new(BLOWFISH_KEY, Blowfish.MODE_ECB).decrypt(swap_words(data)))


def encrypt(data: bytes) -> bytes:
    return swap_words(Blowfish.new(BLOWFISH_KEY, Blowfish.MODE_ECB).encrypt(swap_words(data)))


def payload_digest(payload: bytes) -> bytes:
    return swap_words(hashlib.sha1(payload).digest())


def parse_entries(data: bytes, table_start: int) -> dict[int, tuple[int, int, bytes]]:
    (first_offset,) = struct.unpack_from("<Q", data, table_start)
    entries = {}
    for pointer in range(table_start, first_offset, 8):
        (offset,) = struct.unpack_from("<Q", data, pointer)
        name, kind, value = struct.unpack_from("<IIQ", data, offset)
        start = offset + ENTRY_HEADER_SIZE
        blob = data[start : start + value] if kind == BLOB_KIND else b""
        entries[name] = (kind, value, blob)
    return entries


def read_pc(path: Path) -> tuple[int, dict[int, tuple[int, int, bytes]]]:
    data = decrypt(path.read_bytes())
    magic, version = struct.unpack_from("<II", data, 0)
    (payload_size,) = struct.unpack_from("<I", data, PAYLOAD_SIZE_OFFSET)
    payload = data[PC_HEADER_SIZE : PC_HEADER_SIZE + payload_size]
    if magic != PC_MAGIC or payload_digest(payload) != data[DIGEST_OFFSET : DIGEST_OFFSET + 20]:
        raise ValueError(f"{path}: not a valid PC save")
    return version, parse_entries(data, PC_HEADER_SIZE)


def read_switch(path: Path) -> dict[int, tuple[int, int, bytes]]:
    return parse_entries(path.read_bytes(), SWITCH_HEADER_SIZE)


def build_pc(version: int, entries: dict[int, tuple[int, int, bytes]]) -> bytes:
    names = sorted(entries)
    table_size = len(names) * 8
    body = bytearray()
    offsets = []
    for name in names:
        kind, value, blob = entries[name]
        offsets.append(PC_HEADER_SIZE + table_size + len(body))
        body += struct.pack("<IIQ", name, kind, value) + blob
    payload = struct.pack(f"<{len(offsets)}Q", *offsets) + body
    payload += bytes(-len(payload) % 0x10)

    header = bytearray(PC_HEADER_SIZE)
    struct.pack_into("<III", header, 0, PC_MAGIC, version, table_size)
    header[DIGEST_OFFSET : DIGEST_OFFSET + 20] = payload_digest(payload)
    struct.pack_into("<I", header, PAYLOAD_SIZE_OFFSET, len(payload))
    return encrypt(bytes(header) + payload)


def convert_blob(switch_blob: bytes, template_blob: bytes) -> bytes:
    if len(switch_blob) == len(template_blob):
        return switch_blob
    remap = STRUCT_REMAPS.get((len(switch_blob), len(template_blob)))
    if remap is None:
        raise ValueError(f"no layout mapping for struct size {len(switch_blob):#x} -> {len(template_blob):#x}")
    result = bytearray(template_blob)
    for switch_offset, pc_offset, length in remap:
        result[pc_offset : pc_offset + length] = switch_blob[switch_offset : switch_offset + length]
    return bytes(result)


def convert_file(switch_path: Path, template_path: Path) -> bytes:
    switch_entries = read_switch(switch_path)
    version, entries = read_pc(template_path)
    missing = switch_entries.keys() - entries.keys()
    if missing:
        raise ValueError(f"{switch_path.name}: entries unknown to PC: {sorted(map(hex, missing))}")
    for name, (switch_kind, switch_value, switch_blob) in switch_entries.items():
        kind, value, blob = entries[name]
        if kind != switch_kind:
            raise ValueError(f"{switch_path.name}: entry {name:#010x} changed kind")
        if kind == BLOB_KIND:
            converted = convert_blob(switch_blob, blob)
            entries[name] = (kind, len(converted), converted)
        else:
            entries[name] = (kind, switch_value, b"")
    return build_pc(version, entries)


def main() -> None:
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    switch_dir, template_dir, output_dir = map(Path, sys.argv[1:])
    output_dir.mkdir(parents=True, exist_ok=True)
    for file_name in FILES:
        data = convert_file(switch_dir / file_name, template_dir / file_name)
        (output_dir / file_name).write_bytes(data)
        # Sanity check: the result must pass the same validation the game does.
        read_pc(output_dir / file_name)
        print(f"{file_name}: converted")


if __name__ == "__main__":
    main()
