"""Parse the second stream's entry block (section b) as the PC parser 0x140bcf9b0/0x140bcfe90 does.

Grammar (PC layout; PS5 packs the headers):
  u32 c0, c0 x 16-byte entries
  u32 c1, items: u64 (low = id, high = n), n x 16-byte sub-entries (u64 key, u32, u32)
  u32 c2, elements: u64 key, u8 m, u8 k, pad to 16; m x 16-byte sub-entries;
         k slots each: u8 c, c x entries (u64 key, u32, u8, pad to 16)
  then whatever follows (reported).

Usage: python parse_stream1.py SAVEFILE [--ps5]
"""

import struct
import sys
from pathlib import Path

import gowr_ps5_to_pc as converter

PC = dict(item_entry=16, element_header=16, slot_entry=16, d_item=16, d_inner=16)
PS5 = dict(item_entry=12, element_header=10, slot_entry=13, d_item=12, d_inner=13)


def sections(data, start):
    """The seven u16-length sections of the second stream: list of (offset, size)."""
    position = start
    result = []
    for _ in range(7):
        size = struct.unpack_from("<H", data, position)[0]
        position += 2
        result.append((position, size))
        position += size
    return result, position


def parse_block_b(data, start, size, layout, prefix=True):
    end = start + size
    p = start
    c0 = 0
    if prefix:
        c0 = struct.unpack_from("<I", data, p)[0]
        p += 4 + c0 * 16
    c1 = struct.unpack_from("<I", data, p)[0]
    p += 4
    items = []
    for _ in range(c1):
        item_id, n = struct.unpack_from("<II", data, p)
        p += 8 + n * layout["item_entry"]
        items.append((item_id, n))
    c2 = struct.unpack_from("<I", data, p)[0]
    p += 4
    elements = []
    for _ in range(c2):
        key = struct.unpack_from("<Q", data, p)[0]
        m, k = data[p + 8], data[p + 9]
        p += layout["element_header"] + m * 16
        slots = []
        for _ in range(k):
            c = data[p]
            p += 1 + c * layout["slot_entry"]
            slots.append(c)
        elements.append((key, m, k, slots))
        if p > end:
            break
    return dict(c0=c0, items=items, elements=elements, cursor=p, end=end, remaining=end - p)


def parse_section_d(data, start, size, layout):
    """Section d (0x140cdb260): u32 N; items: u64 key, u32 n (+pad); n x (u64 key2, u32 count2, u8 flag (+pad)) + count2 x u64."""
    end = start + size
    p = start
    total = struct.unpack_from("<I", data, p)[0]
    p += 4
    items = []
    for _ in range(total):
        key, n = struct.unpack_from("<QI", data, p)
        p += layout["d_item"]
        inner = []
        for _ in range(n):
            key2, count2 = struct.unpack_from("<QI", data, p)
            p += layout["d_inner"] + count2 * 8
            inner.append(count2)
            if p > end:
                return dict(exact=False, cursor=p, end=end, items=items)
        items.append((key, n, sum(inner)))
    return dict(exact=p == end, cursor=p, end=end, items=items)


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    layout = PS5 if "--ps5" in argv else PC
    path = next(a for a in argv if not a.startswith("--"))
    data = Path(path).read_bytes()
    stream1 = converter.layout(data)["stream1"]
    secs, cursor = sections(data, stream1.start)
    print(f"{path}: stream1 {stream1.start:#x}..{stream1.stop:#x}; sections {[(hex(o), hex(s)) for o, s in secs]}; cursor {cursor:#x} (end {stream1.stop:#x})")
    b_offset, b_size = secs[1]
    result = parse_block_b(data, b_offset, b_size, layout)
    print(f"  block b @{b_offset:#x} size {b_size:#x}: c0={result['c0']} items={result['items']}")
    for key, m, k, slots in result["elements"]:
        print(f"    element key={key:#x} m={m} k={k} slot counts={slots}")
    print(f"  cursor {result['cursor']:#x} end {result['end']:#x} remaining {result['remaining']:#x}")
    if result["remaining"] > 0:
        rest = data[result["cursor"] : result["cursor"] + 48]
        print(f"  next bytes: {rest.hex(' ')}")
    d_offset, d_size = secs[3]
    d_result = parse_section_d(data, d_offset, d_size, layout)
    print(f"  section d @{d_offset:#x} size {d_size:#x}: exact={d_result['exact']} cursor {d_result['cursor']:#x} end {d_result['end']:#x} items={d_result['items']}")


if __name__ == "__main__":
    main(sys.argv[1:])
