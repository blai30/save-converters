"""Zone records: locate and rebuild the entity blob inside each record's bit-packed payload.

The PC reader (0x140c11400) walks the payload as an MSB-first bit stream: nine presence bits (each
followed by a 17-bit count and objects when set), zone-dependent fields, a 16-bit byte count plus
bytes, then a 15-bit length and that many bytes holding the zone's entity section in the same
format as the Kratos block (u32 3, u32 count, entries with a 0x15 (PS5) / 0x18 (PC) header and a
typed property list, then a u32 holding the blob length). The blob is located by searching every
bit offset for a section that parses exactly, so the fields before it need no grammar.

Usage: python records.py SAVEFILE [--ps5]
"""

import struct
import sys
from pathlib import Path

import gowr_ps5_to_pc as converter
from parse_player import PC_SIZES, type_size

PAYLOAD_OFFSET = 0x90
MAX_ENTITIES = 4096


def bits_of(data):
    return int.from_bytes(data, "big")


def read_bits(value, total_bits, position, count):
    """count bits starting at bit position (MSB first) of an integer holding total_bits bits."""
    shift = total_bits - position - count
    return (value >> shift) & ((1 << count) - 1)


def property_list_length(blob, position):
    start = position
    count = struct.unpack_from("<I", blob, position)[0]
    position += 4
    if count > MAX_ENTITIES:
        return None
    for _ in range(count):
        if position + 9 > len(blob):
            return None
        size = type_size(blob[position + 8], PC_SIZES)
        if size is None:
            return None
        position += 9 + size
        if blob[position - 1 - size] == 0x11:
            if position + 4 > len(blob):
                return None
            elements = struct.unpack_from("<I", blob, position)[0]
            position += 4
            if elements > 65536:
                return None
            for _ in range(elements):
                if position >= len(blob):
                    return None
                element = type_size(blob[position], PC_SIZES)
                if element is None:
                    return None
                position += 1 + element
    return position + 4 - start


def parse_entity_blob(blob, header):
    """Entity sections as written into a zone record: returns the entity count when the blob is exact."""
    if len(blob) < 12 or struct.unpack_from("<I", blob, 0)[0] != 3:
        return None
    count = struct.unpack_from("<I", blob, 4)[0]
    if count > MAX_ENTITIES:
        return None
    position = 8
    for _ in range(count):
        position += header
        if position + 4 > len(blob):
            return None
        length = property_list_length(blob, position)
        if length is None:
            return None
        position += length
    if position + 4 != len(blob) or struct.unpack_from("<I", blob, position)[0] != len(blob):
        return None
    return count


def find_entity_blob(payload, header):
    """(bit offset of the 15-bit length, blob length, entity count) inside a record payload, or None."""
    stream = payload[2:]
    total = len(stream) * 8
    value = bits_of(stream)
    for position in range(15, total - 96 + 1):
        if read_bits(value, total, position, 32) != 0x03000000:
            continue
        length = read_bits(value, total, position - 15, 15)
        if length < 12 or position + length * 8 > total:
            continue
        blob = read_bits(value, total, position, length * 8).to_bytes(length, "big")
        count = parse_entity_blob(blob, header)
        if count is not None:
            return position - 15, length, count
    return None


def repack_entity_blob(blob):
    """PS5 entity blob -> PC layout: entry headers 0x15 -> 0x18, final length word updated."""
    count = struct.unpack_from("<I", blob, 4)[0]
    out = bytearray(blob[:8])
    position = 8
    for _ in range(count):
        out += blob[position : position + 0x15] + bytes(3)
        position += 0x15
        length = property_list_length(blob, position)
        out += blob[position : position + length]
        position += length
    out += struct.pack("<I", len(out) + 4)
    return bytes(out)


def repack_payload(payload):
    """Rebuild a PS5 record payload with its entity blob in the PC layout (payload unchanged if no blob)."""
    found = find_entity_blob(payload, 0x15)
    if found is None:
        return payload
    length_bit, length, _ = found
    stream = payload[2:]
    total = len(stream) * 8
    value = bits_of(stream)
    blob_bit = length_bit + 15
    blob = read_bits(value, total, blob_bit, length * 8).to_bytes(length, "big")
    new_blob = repack_entity_blob(blob)
    head = read_bits(value, total, 0, length_bit) if length_bit else 0
    tail_bits = total - (blob_bit + length * 8)
    tail = read_bits(value, total, blob_bit + length * 8, tail_bits) if tail_bits else 0
    new_total = length_bit + 15 + len(new_blob) * 8 + tail_bits
    new_value = (((head << 15 | len(new_blob)) << (len(new_blob) * 8) | bits_of(new_blob)) << tail_bits) | tail
    new_stream = new_value.to_bytes((new_total + 7) // 8, "big")
    return struct.pack("<H", len(new_stream)) + new_stream


def records(data):
    """(offset, size, name, payload) for every zone record."""
    region = converter.layout(data)["records"]
    result = []
    for offset in converter.record_offsets(data, region):
        size = struct.unpack_from("<I", data, offset + 0x20)[0]
        name = data[offset + 0x4C : offset + 0x80].split(b"\0")[0].decode()
        result.append((offset, size, name, data[offset + PAYLOAD_OFFSET : offset + PAYLOAD_OFFSET + size]))
    return result


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    header = 0x15 if "--ps5" in argv else 0x18
    path = next(a for a in argv if not a.startswith("--"))
    data = Path(path).read_bytes()
    found = missing = empty = 0
    for offset, size, name, payload in records(data):
        if size == 0:
            empty += 1
            continue
        result = find_entity_blob(payload, header)
        if result is None:
            missing += 1
            print(f"  no blob: {name} size={size:#x} head={payload[:16].hex(' ')}")
        else:
            found += 1
    print(f"{path}: records with blob {found}, without {missing}, empty {empty}")


if __name__ == "__main__":
    main(sys.argv[1:])
