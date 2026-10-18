"""Re-serialize PS5-packed structures in a Ragnarok checkpoint with the PC build's alignment.

The PS5 build writes several save structures packed; the PC build pads them to
natural alignment. Each repack below converts one such structure and is verified
by parsing the result with the PC grammar to exactly the section end.

Known structures:
- state stream, pair section (loader 0x14095adb0 -> 0x140cdad50): outer items
  u64 key + u32 count (12 -> 16 bytes), inner elements u64 + 4 flag bytes (12 -> 16).
- state stream, loot section (0x140a5f5c0): entries u64 hash, u8 kind, u64 item,
  u32 count, u8 flag (22 -> 32 bytes).
- tail stream, entity-state section (0x1405148a0): entry header u64 kind, u64
  hash, u32, u8 flag (21 -> 24 bytes) before the property count.
"""

import struct

import gowr_ps5_to_pc as converter
from parse_player import PC_SIZES, type_size, walk_sections as walk_player_sections
from parse_state import walk as walk_state_sections
from parse_stream1 import PC as STREAM1_PC, PS5 as STREAM1_PS5, sections as stream1_sections
from records import PAYLOAD_OFFSET, repack_payload

PAD = b"\0"


def repack_pairs(section):
    """Outer 12 -> 16 and inner 12 -> 16 (old format without the -1 prefix: inner 11 -> 16)."""
    position = 0
    out = bytearray()
    inner = 11
    if section[:8] == b"\xff" * 8:
        out += section[:12]
        position = 12
        inner = 12
    total = struct.unpack_from("<I", section, position)[0]
    out += section[position : position + 4]
    position += 4
    for _ in range(total):
        key, count = struct.unpack_from("<QI", section, position)
        position += 12
        out += struct.pack("<QI", key, count) + PAD * 4
        for _ in range(count):
            out += section[position : position + inner] + PAD * (16 - inner)
            position += inner
    if position != len(section):
        raise ValueError(f"pair section: consumed {position:#x} of {len(section):#x}")
    return bytes(out)


def repack_loot(section):
    """Entries 22 -> 32 bytes (pad after the kind byte and after the flag byte)."""
    count = struct.unpack_from("<I", section, 0)[0]
    out = bytearray(section[:4])
    position = 4
    for _ in range(count):
        item = section[position : position + 22]
        position += 22
        out += item[:9] + PAD * 7 + item[9:22] + PAD * 3
    if position != len(section):
        raise ValueError(f"loot section: consumed {position:#x} of {len(section):#x}")
    return bytes(out)


def property_list_length(data, position):
    """Length of a property list (u32 count, properties, u32 trailer) at position."""
    start = position
    count = struct.unpack_from("<I", data, position)[0]
    position += 4
    for _ in range(count):
        code = data[position + 8]
        position += 9
        size = type_size(code, PC_SIZES)
        if size is None:
            raise ValueError(f"unknown property type {code:#x} at {position - 1:#x}")
        position += size
        if code == 0x11:
            elements = struct.unpack_from("<I", data, position)[0]
            position += 4
            for _ in range(elements):
                position += 1 + type_size(data[position], PC_SIZES)
    return position + 4 - start


def repack_entities(section, header_in=0x15, header_out=0x18):
    """Entity entries: header 21 -> 24 bytes, property lists unchanged, trailer copied."""
    magic, count = struct.unpack_from("<II", section, 0)
    if magic != 3:
        raise ValueError("entity section does not start with 3")
    out = bytearray(section[:8])
    position = 8
    for _ in range(count):
        out += section[position : position + header_in] + PAD * (header_out - header_in)
        position += header_in
        length = property_list_length(section, position)
        out += section[position : position + length]
        position += length
    out += section[position:]
    return bytes(out)


def repack_block_b(block, source=STREAM1_PS5, target=STREAM1_PC, prefix=True):
    """Second stream entry block: pad item sub-entries, element headers and slot entries to the PC layout."""
    p = 0
    out = bytearray()
    if prefix:
        c0 = struct.unpack_from("<I", block, p)[0]
        out += block[p : p + 4 + c0 * 16]
        p += 4 + c0 * 16
    c1 = struct.unpack_from("<I", block, p)[0]
    out += block[p : p + 4]
    p += 4
    for _ in range(c1):
        _, n = struct.unpack_from("<II", block, p)
        out += block[p : p + 8]
        p += 8
        for _ in range(n):
            out += block[p : p + source["item_entry"]] + PAD * (target["item_entry"] - source["item_entry"])
            p += source["item_entry"]
    c2 = struct.unpack_from("<I", block, p)[0]
    out += block[p : p + 4]
    p += 4
    for _ in range(c2):
        m, k = block[p + 8], block[p + 9]
        out += block[p : p + source["element_header"]] + PAD * (target["element_header"] - source["element_header"])
        p += source["element_header"]
        out += block[p : p + m * 16]
        p += m * 16
        for _ in range(k):
            c = block[p]
            out += block[p : p + 1]
            p += 1
            for _ in range(c):
                out += block[p : p + source["slot_entry"]] + PAD * (target["slot_entry"] - source["slot_entry"])
                p += source["slot_entry"]
    if p != len(block):
        raise ValueError(f"entry block: consumed {p:#x} of {len(block):#x}")
    return bytes(out)


def repack_hero(block):
    """Section c: u32, u32 count, count entries (u64, u64, u32) 20 -> 24 bytes, u8 trailer."""
    count = struct.unpack_from("<I", block, 4)[0]
    if 8 + count * 20 + 1 != len(block):
        raise ValueError(f"hero section: {count} entries do not fit {len(block):#x}")
    out = bytearray(block[:8])
    for index in range(count):
        out += block[8 + index * 20 : 8 + (index + 1) * 20] + PAD * 4
    out += block[-1:]
    return bytes(out)


def repack_section_d(block, source=STREAM1_PS5, target=STREAM1_PC):
    """Section d: item headers 12 -> 16 and inner headers 13 -> 16; the u64 lists are unchanged."""
    p = 0
    out = bytearray(block[:4])
    total = struct.unpack_from("<I", block, 0)[0]
    p = 4
    for _ in range(total):
        _, n = struct.unpack_from("<QI", block, p)
        out += block[p : p + source["d_item"]] + PAD * (target["d_item"] - source["d_item"])
        p += source["d_item"]
        for _ in range(n):
            _, count2 = struct.unpack_from("<QI", block, p)
            out += block[p : p + source["d_inner"]] + PAD * (target["d_inner"] - source["d_inner"])
            p += source["d_inner"]
            out += block[p : p + count2 * 8]
            p += count2 * 8
    if p != len(block):
        raise ValueError(f"section d: consumed {p:#x} of {len(block):#x}")
    return bytes(out)


def repack_stream1(stream1):
    secs, consumed = stream1_sections(stream1, 0)
    if consumed != len(stream1):
        raise ValueError(f"second stream: sections cover {consumed:#x} of {len(stream1):#x}")
    pieces = []
    for index, (offset, size) in enumerate(secs):
        body = stream1[offset : offset + size]
        if index == 1:
            body = repack_block_b(body)
        elif index == 2:
            body = repack_hero(body)
        elif index == 3:
            body = repack_section_d(body)
        elif index == 5:
            # Transient entry list the PC save never carries; drop it (count 0).
            body = bytes(4)
        pieces.append(struct.pack("<H", len(body)) + body)
    return b"".join(pieces)


def repack_s3(section):
    """s3: entry headers 20 -> 24, payload elements 6 -> 8, hash list unchanged, trailing entry block repacked."""
    p = 0
    out = bytearray()
    total = struct.unpack_from("<I", section, p)[0]
    out += section[p : p + 4]
    p += 4
    for _ in range(total):
        count = struct.unpack_from("<I", section, p + 16)[0]
        out += section[p : p + 20] + PAD * 4
        p += 20
        for _ in range(count):
            out += section[p : p + 6] + PAD * 2
            p += 6
    hashes = struct.unpack_from("<I", section, p)[0]
    out += section[p : p + 4 + hashes * 8]
    p += 4 + hashes * 8
    out += repack_block_b(section[p:], prefix=False)
    return bytes(out)


def repack_s8(section):
    """s8: header 17 -> 24 (pad after the kind byte), transform records 33 -> 48, hash list unchanged."""
    kind = section[16]
    p = 17
    out = bytearray(section[:17] + PAD * 7)
    small = 7 if kind == 1 else 6
    x = struct.unpack_from("<I", section, p)[0]
    y = struct.unpack_from("<H", section, p + 4)[0]
    out += section[p : p + small]
    p += small
    for _ in range(x >> 24):
        out += section[p : p + 33] + PAD * 15
        p += 33
    out += section[p : p + y * 8]
    p += y * 8
    if p != len(section):
        raise ValueError(f"s8: consumed {p:#x} of {len(section):#x}")
    return bytes(out)


def repack_state(state, versions):
    """Rebuild packed sections; drop whatever the PC grammar leaves unread at the end."""
    sections, consumed = walk_state_sections(state, 0, versions)
    pieces = []
    cursor = 0
    for name, offset, size in sections:
        header = state[cursor:offset]
        body = state[offset : offset + size]
        if name == "s6_pairs":
            body = repack_pairs(body)
        elif name == "s5_loot" and size > 4:
            body = repack_loot(body)
        elif name == "s3":
            body = repack_s3(body)
        elif name == "s8":
            body = repack_s8(body)
        if len(body) != size:
            header = header[:-2] + struct.pack("<H", len(body))
        pieces.append(header + body)
        cursor = offset + size
    pieces.append(state[cursor:consumed])
    return b"".join(pieces)


def repack_player(player, versions):
    sections, _ = walk_player_sections(player, 4, versions)
    pieces = []
    cursor = 0
    for name, offset, size in sections:
        header = player[cursor:offset]
        body = player[offset : offset + size]
        if name == "entities":
            body = repack_entities(body)
            header = header[:-4] + struct.pack("<I", len(body))
        pieces.append(header + body)
        cursor = offset + size
    pieces.append(player[cursor:])
    return b"".join(pieces)


def repack_records(region):
    """Zone record list: every record's entity blob rebuilt in the PC layout, sizes rewritten."""
    out = bytearray(region[: converter.TAIL_FIRST_RECORD])
    position = converter.TAIL_FIRST_RECORD
    count = struct.unpack_from("<I", region, 0)[0]
    for _ in range(count):
        size = struct.unpack_from("<I", region, position + converter.RECORD_SIZE_OFFSET)[0]
        header = bytearray(region[position : position + PAYLOAD_OFFSET])
        payload = repack_payload(region[position + PAYLOAD_OFFSET : position + PAYLOAD_OFFSET + size])
        struct.pack_into("<I", header, converter.RECORD_SIZE_OFFSET, len(payload))
        trailer_start = position + PAYLOAD_OFFSET + size
        out += header + payload + region[trailer_start : trailer_start + converter.RECORD_FIXED - PAYLOAD_OFFSET]
        position = trailer_start + converter.RECORD_FIXED - PAYLOAD_OFFSET
    out += region[position:]
    return bytes(out)


RESOURCE_BITS = 0x100
RESOURCE_PS5 = 0x140
RESOURCE_PC = 0x148
RESOURCE_HEAD = 0x104


def repack_resources(tail):
    """Resource list (u32 count, entries, trailer): entries 0x140 -> 0x148.

    Entry: 0x100 bytes of flags, u32 -1, then PS5 {u16 id, u8 flags, char name[0x31]} / PC {u32 id, u32 flags, char name[0x34]}, u64 hash."""
    count = struct.unpack_from("<I", tail, 0)[0]
    out = bytearray(tail[:4])
    position = 4
    for _ in range(count):
        entry = tail[position : position + RESOURCE_PS5]
        position += RESOURCE_PS5
        fields = RESOURCE_BITS + 4
        entry_id, flags = struct.unpack_from("<HB", entry, fields)
        name = entry[fields + 3 : fields + 3 + 0x31]
        digest = entry[fields + 3 + 0x31 : RESOURCE_PS5]
        out += entry[:fields] + struct.pack("<II", entry_id, flags) + name + PAD * 3 + digest
    out += tail[position:]
    return bytes(out)


def repack(data):
    """Return the checkpoint with every known structure re-serialized for the PC build."""
    versions = data[0x1C4:0x1E4]
    regions = converter.layout(data)
    parts = {name: data[region] for name, region in regions.items()}
    parts["state"] = repack_state(parts["state"], versions)
    parts["stream1"] = repack_stream1(parts["stream1"])
    parts["player"] = repack_player(parts["player"], versions)
    records = repack_records(parts["records"])
    # The resource count and the first entry's flag block sit in the last 0x104 bytes of the record region.
    parts["records"] = records[:-RESOURCE_HEAD] + repack_resources(records[-RESOURCE_HEAD:] + parts["resources"])
    parts["resources"] = b""
    return converter.assemble(data[: converter.RECORD_OFFSET], parts)
