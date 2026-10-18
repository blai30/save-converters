"""Walk the global state stream the way the PC loader (0x14095adb0) does and test section layouts.

Grammar (lengths read via the bounds-checked helper, version table bytes decide variants):
  u16 len0  bytes  (idx17==1: 0x140a4d2b0)
  u16 len1  bytes  (idx18==1: quests 0x140cd5fc0)
  u32|u16 len2 (idx19>=3 ? u32 : u16) bytes (0x140a55220 block)
  u16 len3  bytes  (idx20==5: 0x140bceee0)
  u32 len4  bytes  (idx21==1: 0x140a5b3e0)
  u16 len5  bytes  (idx22==1: ResolveLoadingLoot 0x140a5f5c0: u32 count, 16-byte entries)
  u16 len6  bytes  (idx23==4: pair reader 0x140cdad50)
  u16 len7, u16 len8 bytes
  u32|u16 len9 (idx26>=4 ? u32 : u16), u8, u32 len10 bytes
  (idx31>0: u16 len11 bytes)

Usage: python parse_state.py SAVEFILE
"""

import struct
import sys
from pathlib import Path

import gowr_ps5_to_pc as converter


def walk(data, start, versions):
    position = start
    sections = []

    def u16():
        nonlocal position
        value = struct.unpack_from("<H", data, position)[0]
        position += 2
        return value

    def u32():
        nonlocal position
        value = struct.unpack_from("<I", data, position)[0]
        position += 4
        return value

    def block(name, size):
        nonlocal position
        sections.append((name, position, size))
        position += size

    block("s0_tables", u16())
    block("s1_quests", u16())
    block("s2_block", u32() if versions[19] >= 3 else u16())
    block("s3", u16())
    block("s4", u32())
    block("s5_loot", u16())
    block("s6_pairs", u16())
    block("s7", u16())
    block("s8", u16())
    block("s9", u32() if versions[26] >= 4 else u16())
    position += 1
    block("s10", u32())
    if versions[31]:
        block("s11", u16())
    return sections, position


def parse_pairs(data, start, size, outer, inner):
    """Pair section: [u64 key][u32 count][pad to outer] then count elements of `inner` bytes."""
    end = start + size
    position = start
    first = struct.unpack_from("<Q", data, position)[0]
    if first == 0xFFFFFFFFFFFFFFFF:
        position += 12
    total = struct.unpack_from("<I", data, position)[0]
    position += 4
    keys = []
    for index in range(total):
        if position + outer > end:
            return False, f"item {index} header past end at {position:#x} (keys so far {len(keys)})"
        key, count = struct.unpack_from("<QI", data, position)
        position += outer
        if count > 20000:
            return False, f"item {index} key={key:#x} absurd count {count:#x} at {position - outer:#x}"
        position += count * inner
        keys.append((key, count))
    return position == end, f"items={total} cursor {position:#x} end {end:#x} remaining {end - position:#x} first={keys[:3]}"


S3_PC = dict(header=24, element=8)
S3_PS5 = dict(header=20, element=6)


def parse_s3(data, start, size, layout, items_layout):
    """s3 (0x140bceee0): u32 N; N x {u64 key, u32 ordinal, u32 x, u32 count (+pad)} each followed by
    count x {u32, u8, u8 (+pad)}; u32 M; M x u64; then an entry block without the leading table (0x140bcfe90)."""
    from parse_stream1 import parse_block_b

    end = start + size
    p = start
    total = struct.unpack_from("<I", data, p)[0]
    p += 4
    elements = 0
    for _ in range(total):
        count = struct.unpack_from("<I", data, p + 16)[0]
        p += layout["header"] + count * layout["element"]
        elements += count
        if p > end:
            return False, f"entries overran at {p:#x}"
    hashes = struct.unpack_from("<I", data, p)[0]
    p += 4 + hashes * 8
    block = parse_block_b(data, p, end - p, items_layout, prefix=False)
    return block["cursor"] == end, f"entries={total} elements={elements} hashes={hashes} items={len(block['items'])} elements_block={len(block['elements'])} cursor {block['cursor']:#x} end {end:#x}"


def parse_s8(data, start, size, header, transform):
    """s8 (0x140a2f4d0): header u64, u64, u8 kind (+pad); kind 1: u32 x, u16 y, u8; kind 2: u32 x, u16 y;
    (x >> 24) transform records (vec4, u64, u64, u8, padded); y hashes of 8 bytes."""
    end = start + size
    p = start
    kind = data[p + 16]
    p += header
    x, y = struct.unpack_from("<IH", data, p)
    p += 7 if kind == 1 else 6
    transforms = x >> 24
    p += transforms * transform
    p += y * 8
    return p == end, f"kind={kind} x={x:#x} y={y} transforms={transforms} cursor {p:#x} end {end:#x}"


def parse_loot(data, start, size, entry):
    end = start + size
    count = struct.unpack_from("<I", data, start)[0]
    position = start + 4 + count * entry
    return position == end, f"count={count} entry={entry} cursor {position:#x} end {end:#x}"


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    data = Path(argv[0]).read_bytes()
    regions = converter.layout(data)
    state = regions["state"]
    versions = data[0x1C4:0x1E4]
    print(f"{argv[0]}: state {state.start:#x}..{state.stop:#x} idx17..31={list(versions[17:32])}")
    sections, cursor = walk(data, state.start, versions)
    for name, offset, size in sections:
        print(f"  {name:10} @{offset:#x} size={size:#x}  {data[offset:offset + 24].hex(' ')}")
    print(f"  cursor {cursor:#x} vs state end {state.stop:#x} (remaining {state.stop - cursor:#x})")
    loot = next(s for s in sections if s[0] == "s5_loot")
    for entry in (16, 12, 9):
        print("  loot", parse_loot(data, loot[1], loot[2], entry))
    from parse_stream1 import PC as STREAM1_PC, PS5 as STREAM1_PS5

    s3 = next(s for s in sections if s[0] == "s3")
    for name, layout, items_layout in (("pc", S3_PC, STREAM1_PC), ("ps5", S3_PS5, STREAM1_PS5)):
        print(f"  s3 {name}:", parse_s3(data, s3[1], s3[2], layout, items_layout))
    s8 = next(s for s in sections if s[0] == "s8")
    for header, transform in ((24, 48), (17, 33)):
        print(f"  s8 header={header} transform={transform}:", parse_s8(data, s8[1], s8[2], header, transform))
    pairs = next(s for s in sections if s[0] == "s6_pairs")
    for outer, inner in ((16, 16), (12, 12), (12, 16), (16, 12)):
        ok, note = parse_pairs(data, pairs[1], pairs[2], outer, inner)
        print(f"  pairs outer={outer} inner={inner}: {'EXACT' if ok else 'no'} {note}")


if __name__ == "__main__":
    main(sys.argv[1:])
