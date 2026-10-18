"""Convert Death Stranding Director's Cut PS5 saves to PC (Steam).

PS5 checkpoint.dat: 8-byte magic 2A26F4DD693993FB, u32 chunk count, that many u32 chunk
sizes, then from 0x110 the chunks, each a big-endian u64 raw size and a zlib stream. The
chunks join into 12 MiB of raw save data, which both platforms share.

PC checkpoint.dat: three 8-byte NUL-padded decimal sizes, then the slot metadata (CRLF
"key = value" text built from param.sfo), a 228x128 PNG cover and the data block:
u32 magic 0xC0391E1D, a random 4-byte key header, then XOR-encrypted
<u32 length, "steam_<SteamID64>", Oodle Kraken stream of the raw data>. The XOR key is
MurmurHash3_x64_128 (seed 0x2A) of the key header plus fixed key material, repeated over
the whole block (from mi5hmash/DeathStrandingSaveDataResigner). The Kraken stream is
written without a compressor: all-zero 256 KiB blocks as fill quanta, the rest as
uncompressed blocks.

Output names: sdimg_MANUALSAVE0 becomes <account id>/manualsave0/checkpoint.dat, where
the account id is SteamID64 - 76561197960265728. sdimg_PROFILE is skipped: it only holds
settings and the PC game creates its own.

Usage: python convert_death_stranding.py <decrypted PPSA01970 dump folder> <output folder> --steam-id <SteamID64>
"""

import argparse
import datetime
import io
import os
import struct
import zlib
from pathlib import Path
from urllib.parse import quote

from PIL import Image

PS5_MAGIC = bytes.fromhex("2A26F4DD693993FB")
PC_MAGIC = 0xC0391E1D
KEY_MATERIAL = bytes.fromhex("463FBBBC7B6F57ED9B417D36E32612BC")
KEY_SEED = 0x2A
# Low 24 bits of SAVEDATA_LIST_PARAM are the save data version, which must match the PC game.
PC_USER_PARAM = 0x004356D3
KRAKEN_BLOCK_SIZE = 0x40000
STEAM_ID64_BASE = 76561197960265728
COVER_SIZE = (228, 128)
UINT64_MASK = (1 << 64) - 1


def read_param_sfo(path):
    data = path.read_bytes()
    key_table, data_table, count = struct.unpack_from("<III", data, 0x08)
    params = {}
    for i in range(count):
        key_offset, _, length, _, data_offset = struct.unpack_from("<HHIII", data, 0x14 + 0x10 * i)
        key_start = key_table + key_offset
        key = data[key_start : data.index(b"\0", key_start)].decode()
        params[key] = data[data_table + data_offset : data_table + data_offset + length]
    return params


def sfo_text(params, key):
    return params[key].rstrip(b"\0").decode()


def read_ps5_checkpoint(container, source):
    if not container.startswith(PS5_MAGIC):
        raise ValueError(f"{source}: not a Death Stranding PS5 checkpoint")
    count = struct.unpack_from("<I", container, 0x08)[0]
    offset = 0x110
    raw = bytearray()
    for size in struct.unpack_from(f"<{count}I", container, 0x0C):
        raw_size = struct.unpack_from(">Q", container, offset)[0]
        block = zlib.decompress(container[offset + 8 : offset + size])
        if len(block) != raw_size:
            raise ValueError(f"{source}: chunk size mismatch")
        raw += block
        offset += size
    if offset != len(container):
        raise ValueError(f"{source}: trailing data")
    return bytes(raw)


def kraken_encode(raw):
    # Bit 7 of the first block header restarts the decoder.
    stream = bytearray()
    for start in range(0, len(raw), KRAKEN_BLOCK_SIZE):
        block = raw[start : start + KRAKEN_BLOCK_SIZE]
        restart = 0x80 if start == 0 else 0x00
        if block.count(0) == len(block):
            stream += bytes([restart | 0x0C, 0x06, 0x07, 0xFF, 0xFF, 0x00])
        else:
            stream += bytes([restart | 0x4C, 0x06]) + block
    return bytes(stream)


def kraken_decode(stream, raw_size):
    # Decodes only the block kinds kraken_encode writes, for the round-trip check.
    raw = bytearray()
    offset = 0
    while len(raw) < raw_size:
        flags, decoder = stream[offset], stream[offset + 1]
        if flags & 0x0F != 0x0C or decoder != 0x06:
            raise ValueError("not a Kraken block header")
        size = min(KRAKEN_BLOCK_SIZE, raw_size - len(raw))
        if flags & 0x40:
            raw += stream[offset + 2 : offset + 2 + size]
            offset += 2 + size
        else:
            raw += bytes([stream[offset + 5]]) * size
            offset += 6
    if offset != len(stream):
        raise ValueError("trailing Kraken data")
    return bytes(raw)


def rotate_left(value, bits):
    return ((value << bits) | (value >> (64 - bits))) & UINT64_MASK


def murmur3_fmix(value):
    value ^= value >> 33
    value = (value * 0xFF51AFD7ED558CCD) & UINT64_MASK
    value ^= value >> 33
    value = (value * 0xC4CEB9FE1A85EC53) & UINT64_MASK
    return value ^ (value >> 33)


def derive_key(key_header):
    # MurmurHash3_x64_128 over one 16-byte block: the key header, then key material bytes 4..15.
    c1, c2 = 0x87C37B91114253D5, 0x4CF5AD432745937F
    k1, k2 = struct.unpack("<QQ", key_header + KEY_MATERIAL[4:])
    h1 = h2 = KEY_SEED
    h1 ^= (rotate_left((k1 * c1) & UINT64_MASK, 31) * c2) & UINT64_MASK
    h1 = ((rotate_left(h1, 27) + h2) * 5 + 0x52DCE729) & UINT64_MASK
    h2 ^= (rotate_left((k2 * c2) & UINT64_MASK, 33) * c1) & UINT64_MASK
    h2 = ((rotate_left(h2, 31) + h1) * 5 + 0x38495AB5) & UINT64_MASK
    h1 ^= 16
    h2 ^= 16
    h1 = (h1 + h2) & UINT64_MASK
    h2 = (h2 + h1) & UINT64_MASK
    h1, h2 = murmur3_fmix(h1), murmur3_fmix(h2)
    h1 = (h1 + h2) & UINT64_MASK
    h2 = (h2 + h1) & UINT64_MASK
    return struct.pack("<QQ", h1, h2)


def crypt(data, key_header):
    # A continuous 16-byte keystream; game-written files with odd-length tails only decode this way.
    key = derive_key(key_header)
    stream = (key * (len(data) // 16 + 1))[: len(data)]
    return (int.from_bytes(data, "little") ^ int.from_bytes(stream, "little")).to_bytes(len(data), "little")


def build_data_block(raw, user_id):
    key_header = os.urandom(4)
    plain = struct.pack("<I", len(user_id)) + user_id.encode("ascii") + kraken_encode(raw)
    return struct.pack("<I", PC_MAGIC) + key_header + crypt(plain, key_header)


def read_data_block(block, raw_size):
    if struct.unpack_from("<I", block, 0)[0] != PC_MAGIC:
        raise ValueError("bad data block magic")
    plain = crypt(block[8:], block[4:8])
    user_id_length = struct.unpack_from("<I", plain, 0)[0]
    user_id = plain[4 : 4 + user_id_length].decode("ascii")
    return user_id, kraken_decode(plain[4 + user_id_length :], raw_size)


def build_metadata(params):
    # Like game-written files: these characters stay literal, everything else is percent-encoded.
    def encode(value):
        return quote(value, safe=" ,.:-'()<>=_")

    saved_at = datetime.datetime.fromtimestamp(struct.unpack_from("<I", params["PARAMS"], 0x68)[0])
    # Local time in microseconds since 0001-01-01.
    modification_time = (saved_at - datetime.datetime(1, 1, 1)) // datetime.timedelta(microseconds=1)
    fields = [
        ("Title", encode(sfo_text(params, "MAINTITLE"))),
        ("SubTitle", encode(sfo_text(params, "SUBTITLE"))),
        ("Detail", encode(sfo_text(params, "DETAIL"))),
        ("UserParam", f"#{PC_USER_PARAM:08X}"),
        ("ModificationTime", str(modification_time)),
    ]
    lines = ["[Slot]"] + [f"{key.ljust(32)}= {value}" for key, value in fields]
    return ("\r\n".join(lines) + "\r\n").encode("ascii")


def cover_image(icon):
    buffer = io.BytesIO()
    Image.open(icon).convert("RGB").resize(COVER_SIZE, Image.Resampling.LANCZOS).save(buffer, "PNG")
    return buffer.getvalue()


def convert(source_dir, output_dir, steam_id):
    user_id = f"steam_{steam_id}"
    account_dir = output_dir / str(steam_id - STEAM_ID64_BASE)
    for slot in sorted(source_dir.glob("sdimg_*SAVE*")):
        source = slot / "checkpoint.dat"
        raw = read_ps5_checkpoint(source.read_bytes(), source)
        params = read_param_sfo(slot / "sce_sys" / "param.sfo")
        if struct.unpack("<I", params["SAVEDATA_LIST_PARAM"])[0] & 0xFFFFFF != PC_USER_PARAM & 0xFFFFFF:
            raise ValueError(f"{slot}: save data version differs from the PC version")
        metadata = build_metadata(params)
        image = cover_image(slot / "sce_sys" / "icon0.png")
        block = build_data_block(raw, user_id)
        if read_data_block(block, len(raw)) != (user_id, raw):
            raise AssertionError(f"{slot.name}: round-trip mismatch")
        sizes = b"".join(str(len(part)).encode().ljust(8, b"\0") for part in (metadata, image, block))
        path = account_dir / sfo_text(params, "SAVEDATA_DIRECTORY").lower() / "checkpoint.dat"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(sizes + metadata + image + block)
        print(f"{path.relative_to(output_dir)}  {sfo_text(params, 'MAINTITLE')}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="decrypted PPSA01970 folder with the sdimg_* save folders")
    parser.add_argument("output", type=Path)
    parser.add_argument("--steam-id", type=int, required=True, help="SteamID64, the 17-digit number starting with 7656119")
    args = parser.parse_args()
    if not 0 < args.steam_id - STEAM_ID64_BASE < 1 << 32:
        parser.error("--steam-id must be a SteamID64 (17 digits, starting with 7656119)")
    convert(args.source, args.output, args.steam_id)


if __name__ == "__main__":
    main()
