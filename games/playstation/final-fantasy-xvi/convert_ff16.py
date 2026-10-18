"""Convert Final Fantasy XVI PS5 saves to PC.

PS5 and PC share the same save payload: <u32 header size 0x10, u32 CRC-32 of the
rest, u64 version> followed by sections. The PC game wraps it in a PNG: IHDR, a
private faTh chunk, IDAT split every 0x2000 bytes, IEND. faTh holds a UMIF
container: <u32 table size, u32 0, u32 magic, u32 count>, a 0x20-byte entry
<u32 name length, u32 blob length, u64 name offset, u64 payload length, u64 blob offset>
per file, the names, then the blobs, each 4-byte aligned. Names and blobs are XORed
with an 8-byte key; blobs are zlib streams with a preset dictionary
(ff16_zdict.bin) minus their 2-byte header. Format, key and dictionary from
github.com/Nenkai/FF16Tools (MIT).

Output names: sdimg_FAITHDATANNNN becomes NNNN.png and the ten autosaves in
sdimg_FAITHDATAAUTO go into auto.png, newest first. sdimg_FAITHDATASYSTEM
(settings) is skipped: the PC system save has extra sections, so keep the one the
PC game created.

Usage: python convert_ff16.py <decrypted PPSA10664 dump folder> <output folder>
"""

import struct
import sys
import zlib
from pathlib import Path

XOR_KEY = (0x0F3F80FE5F1FC4F3).to_bytes(8, "little")
ZLIB_DICTIONARY = (Path(__file__).parent / "ff16_zdict.bin").read_bytes()
ZLIB_HEADER = b"\x78\xf9"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
CONTAINER_MAGIC = 0x46494D55
IDAT_SIZE = 0x2000
USER_DATA_SECTION = 7


def xor(data):
    # Whole 8-byte blocks use the full key; the tail is split into 4/2/1-byte pieces that each restart at key byte 0.
    stream = XOR_KEY * (len(data) // 8)
    remainder = len(data) % 8
    for width in (4, 2, 1):
        if remainder >= width:
            stream += XOR_KEY[:width]
            remainder -= width
    return bytes(value ^ key for value, key in zip(data, stream))


def load_payload(path):
    payload = path.read_bytes()
    header_size, crc = struct.unpack_from("<II", payload, 0)
    if header_size != 0x10 or crc != zlib.crc32(payload[0x10:]):
        raise ValueError(f"{path}: not a valid Final Fantasy XVI save payload")
    return payload


def save_time(payload):
    # Sections are <u32 type, u32 size, u64 version>; the next one starts at offset + size - 8.
    offset = 0x10
    while offset + 0x10 <= len(payload):
        section_type, size = struct.unpack_from("<II", payload, offset)
        if section_type == USER_DATA_SECTION:
            return struct.unpack_from("<I", payload, offset + 0x10 + 0x58)[0]
        if size < 0x10:
            break
        offset += size - 8
    raise ValueError("UserData section not found")


def compress(payload):
    compressor = zlib.compressobj(level=9, zdict=ZLIB_DICTIONARY)
    stream = compressor.compress(payload) + compressor.flush()
    # The game stores the stream without its 2-byte zlib header and re-adds 78 F9 before inflating.
    if not stream.startswith(ZLIB_HEADER):
        raise AssertionError("unexpected zlib header")
    return xor(stream[2:])


def decompress(blob):
    inflater = zlib.decompressobj(zdict=ZLIB_DICTIONARY)
    payload = inflater.decompress(ZLIB_HEADER + xor(blob)) + inflater.flush()
    if not inflater.eof:
        raise ValueError("truncated save stream")
    return payload


def pad(buffer):
    buffer += bytes(-len(buffer) % 4)


def build_container(entries):
    table_size = 0x10 + 0x20 * len(entries)
    container = bytearray(table_size)
    struct.pack_into("<IIII", container, 0, table_size, 0, CONTAINER_MAGIC, len(entries))
    name_offsets = []
    for name, _ in entries:
        encoded = name.encode() + b"\0"
        name_offsets.append((len(container), len(encoded)))
        container += xor(encoded)
        pad(container)
    for i, (_, payload) in enumerate(entries):
        blob = compress(payload)
        name_offset, name_length = name_offsets[i]
        entry = (name_length, len(blob), name_offset, len(payload), len(container))
        struct.pack_into("<IIQQQ", container, 0x10 + 0x20 * i, *entry)
        container += blob
        pad(container)
    return bytes(container)


def read_container(container):
    table_size, _, magic, count = struct.unpack_from("<IIII", container, 0)
    if magic != CONTAINER_MAGIC or table_size != 0x10 + 0x20 * count:
        raise ValueError("bad UMIF container")
    entries = []
    for i in range(count):
        name_length, blob_length, name_offset, _, blob_offset = struct.unpack_from("<IIQQQ", container, 0x10 + 0x20 * i)
        name = xor(container[name_offset : name_offset + name_length]).rstrip(b"\0").decode()
        entries.append((name, decompress(container[blob_offset : blob_offset + blob_length])))
    return entries


def png_chunks(png):
    chunks = []
    offset = len(PNG_SIGNATURE)
    while offset < len(png):
        length, kind = struct.unpack_from(">I4s", png, offset)
        data = png[offset + 8 : offset + 8 + length]
        if struct.unpack_from(">I", png, offset + 8 + length)[0] != zlib.crc32(kind + data):
            raise ValueError(f"bad PNG CRC on {kind}")
        chunks.append((kind, data))
        offset += 12 + length
    return chunks


def png_chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def build_png(container, icon):
    chunks = png_chunks(icon)
    header = next(data for kind, data in chunks if kind == b"IHDR")
    image = b"".join(data for kind, data in chunks if kind == b"IDAT")
    png = bytearray(PNG_SIGNATURE)
    png += png_chunk(b"IHDR", header)
    png += png_chunk(b"faTh", container)
    for start in range(0, len(image), IDAT_SIZE):
        png += png_chunk(b"IDAT", image[start : start + IDAT_SIZE])
    png += png_chunk(b"IEND", b"")
    return bytes(png)


def write(path, entries, icon):
    png = build_png(build_container(entries), icon)
    # Round-trip check: decoding the written PNG must give back the exact PS5 payloads.
    container = next(data for kind, data in png_chunks(png) if kind == b"faTh")
    if read_container(container) != entries:
        raise AssertionError(f"{path.name}: round-trip mismatch")
    path.write_bytes(png)
    print(f"{path.name:9} {', '.join(name for name, _ in entries)}")


def convert(source_dir, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    icon = (source_dir / "sdimg_FAITHDATAAUTO" / "sce_sys" / "icon0.png").read_bytes()
    for slot in sorted(source_dir.glob("sdimg_FAITHDATA[0-9][0-9][0-9][0-9]")):
        payload = load_payload(slot / "game-save-data")
        write(output_dir / f"{slot.name[-4:]}.png", [("game-save-data", payload)], icon)
    autosaves = [(path.name, load_payload(path)) for path in sorted((source_dir / "sdimg_FAITHDATAAUTO").glob("game-save-data[0-9][0-9]"))]
    autosaves.sort(key=lambda entry: save_time(entry[1]), reverse=True)
    write(output_dir / "auto.png", autosaves, icon)


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    convert(Path(sys.argv[1]), Path(sys.argv[2]))


if __name__ == "__main__":
    main()
