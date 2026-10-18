"""Parse the tail stream's Kratos block the way the PC loader (0x14095a1f0) does.

Walks the length-prefixed sections, then parses the entity-state section
(u32 3, u32 count, 0x18-byte entries each followed by a typed property list)
with the PC type-size table from the property reader at 0x1407e79c0, and
reports the first inconsistency.

Usage: python parse_player.py SAVEFILE [--sizes ps5|pc]
"""

import struct
import sys
from pathlib import Path

import gowr_ps5_to_pc as converter

FOUR = {2, 3, 7, 43, 52, 53, 55, 56, 60}
SIXTEEN = {6, 11, 18, 19, 23, 62, 67}
PC_SIZES = {1: 1, 5: 12, 15: 0x18, 25: 0xA0, 26: 0x1440}


def type_size(code, table):
    if code in table:
        return table[code]
    if code in FOUR:
        return 4
    if code in SIXTEEN:
        return 16
    if 1 <= code <= 0x4B:
        return 8
    return None


def walk_sections(data, start, versions):
    """Return (list of (name, offset, size), cursor) following the second loader's grammar."""
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

    version = u16()
    if version >= 3:
        u16()
    block("L", u16())
    u16()
    block("N", u32())
    block("M", u16())
    block("K", u32() if versions[3] >= 2 else u16())
    for index in range(4):
        block(f"s{index}", u16())
    for index in range(13):
        block(f"t{index}", u16())
    block("entities", u32())
    block("B", u32())
    block("C", u32())
    return sections, position


def parse_entities(data, start, size, table):
    end = start + size
    position = start
    magic, count = struct.unpack_from("<II", data, position)
    position += 8
    if magic != 3:
        return f"magic {magic} != 3 at {start:#x}"
    log = [f"entities: count={count}"]
    for index in range(count):
        if position + 0x18 > end:
            return "\n".join(log + [f"entity {index}: entry header past end at {position:#x}"])
        kind, name_hash, extra_a, extra_b = struct.unpack_from("<QQII", data, position)
        position += 0x18
        prop_count = struct.unpack_from("<I", data, position)[0]
        position += 4
        if prop_count > 4096:
            return "\n".join(log + [f"entity {index} hash={name_hash:#x}: absurd property count {prop_count:#x} at {position - 4:#x}"])
        props = []
        for prop in range(prop_count):
            prop_hash = struct.unpack_from("<Q", data, position)[0]
            code = data[position + 8]
            position += 9
            size = type_size(code, table)
            if size is None:
                return "\n".join(log + [f"entity {index} hash={name_hash:#x} prop {prop} hash={prop_hash:#x}: unknown type {code:#x} at {position - 1:#x}"])
            position += size
            if code == 0x11:
                elements = struct.unpack_from("<I", data, position)[0]
                position += 4
                if elements > 65536:
                    return "\n".join(log + [f"entity {index} hash={name_hash:#x} prop {prop}: absurd array count {elements:#x} at {position - 4:#x}"])
                for _ in range(elements):
                    element_size = type_size(data[position], table)
                    if element_size is None:
                        return "\n".join(log + [f"entity {index} prop {prop}: unknown element type {data[position]:#x} at {position:#x}"])
                    position += 1 + element_size
            props.append((prop_hash, code, size))
        trailer = struct.unpack_from("<I", data, position)[0]
        position += 4
        if index < 6 or index >= count - 2:
            log.append(f"  entity {index}: kind={kind:#x} hash={name_hash:#x} a={extra_a:#x} b={extra_b:#x} props={prop_count} types={[c for _, c, _ in props][:12]} trailer={trailer:#x}")
    log.append(f"parsed {count} entities, cursor {position:#x}, section end {end:#x}, remaining {end - position:#x}")
    return "\n".join(log)


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    table = dict(PC_SIZES)
    if "--sizes" in argv:
        choice = argv[argv.index("--sizes") + 1]
        if choice == "ps5":
            table[25] = 0x278
    path = next(a for a in argv if not a.startswith("--") and a not in ("ps5", "pc"))
    data = Path(path).read_bytes()
    regions = converter.layout(data)
    player = regions["player"]
    versions = data[0x1C4:0x1E4]
    print(f"{path}: player block {player.start:#x}..{player.stop:#x}, versions idx1..3 = {list(versions[1:4])}")
    sections, cursor = walk_sections(data, player.start + 4, versions)
    for name, offset, size in sections:
        print(f"  {name:9} @{offset:#x} size={size:#x}")
    print(f"  cursor after C: {cursor:#x} (player block end {player.stop:#x}, remaining {player.stop - cursor:#x})")
    entities = next(s for s in sections if s[0] == "entities")
    print(parse_entities(data, entities[1], entities[2], table))


if __name__ == "__main__":
    main(sys.argv[1:])
