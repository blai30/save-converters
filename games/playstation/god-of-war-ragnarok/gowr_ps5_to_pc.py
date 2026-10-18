"""Convert a decrypted God of War Ragnarok PS5 checkpoint (GOWRSAVE<n>) for the Steam build.

File layout (both platforms, taken from the game's own loader): a 0x40 byte
file header (u64 time, u32 version 8, u32 build, u32 0x2B8, u32 slot size,
u32 total size) and a record header at 0x40 (u64 time, u32 record version 35,
u32 build, 0x100 byte location name, stats, three stream sizes at 0x198 /
0x19C / 0x1A0 whose sum is the total size, a 32-byte section version table at
0x1C4). Three serialized streams follow at fixed offsets that depend on the
generation byte at 0x1E2: the global state stream at 0x440, a second stream at
0x2F440 (generation 0, 1) or 0x80440 (generation 2), and the tail stream
(player block, zone records, resource bundle list) at 0x44440 or 0xC0440.
Gaps between streams are padding the writer does not clear, so old files can
hold stale data there.

Constant per platform: the build number, the u32 hash that opens the tail
stream (0xE0ECF911 on PS5, 0xD31AD457 on PC) and the packing of many structures
(see repack.py). The u32 at 0x440 looks like a platform tag but is the first
state section's length and data, so --tag corrupts the stream. Section version
bytes (0x1C7, 0x1D7, 0x1E3) are per game version; the PC build accepts older ones.

Usage (the first line is the working PS5 -> PC recipe; the rest are diagnostics):
    python gowr_ps5_to_pc.py GOWRSAVE20 --repack --player-hash --out out/GOWRSAVE20
    python gowr_ps5_to_pc.py GOWRSAVE20 --out out/GOWRSAVE20 [--player-hash] [--versions pc]
    python gowr_ps5_to_pc.py GOWRSAVE20 --out out/GOWRSAVE20 --template PC_SAVE --swap records,resources
    python gowr_ps5_to_pc.py GOWRSAVE20 --out out/GOWRSAVE20 --template PC_SAVE --base template --swap state,stream1
"""

import argparse
import re
import struct
import sys
from pathlib import Path

FILE_HEADER_VERSION = 8
RECORD_VERSION = 35
RECORD_OFFSET = 0x40
BUILD_OFFSET = 0xC
SLOT_SIZE_OFFSET = 0x14
TOTAL_SIZE_OFFSET = 0x18
STREAM_SIZES_OFFSET = 0x198
GENERATION_OFFSET = 0x1E2
PC_BUILD = 6130420
PC_SLOT_SIZE = 0x8C0400
# Section version bytes that differ between the 2023 PS5 build and PC: offset -> (ps5 value, pc value).
SECTION_VERSIONS = {0x1C7: (0x01, 0x02), 0x1D7: (0x02, 0x03), 0x1E3: (0x00, 0x01)}
STATE_OFFSET = 0x440
PS5_STATE_TAG = 0x031E61C0
PC_STATE_TAG = 0x043383FC
# (stream1 offset, tail offset) per generation; the state stream fills the gap before stream1.
OLD_LAYOUT = (0x2F440, 0x44440)
NEW_LAYOUT = (0x80440, 0xC0440)
PS5_PLAYER_HASH = 0xE0ECF911
PC_PLAYER_HASH = 0xD31AD457
# The resource list starts with FF FF FF FF after 0xC4 zero bytes that close the last zone record.
RESOURCES_GAP = 0xC4
END_MARKER = b"\xff\xff\xff\xff"
# Zone record list: u32 count, u32 3, zeros, first record at +0x44. A record has a
# u32 zero at +0xC, two identical u64 pointers at +0x10, u32 payload size at +0x20,
# flag bytes at +0x88 and a total length of 0xD0 + size.
TAIL_FIRST_RECORD = 0x44
RECORD_SIZE_OFFSET = 0x20
RECORD_FLAGS_OFFSET = 0x88
RECORD_FIXED = 0xD0
PC_RECORD_FLAGS = b"\x00\x01\x01\x01"
REGIONS = ("header", "state", "stream1", "player", "records", "resources")


def stream_sizes(data):
    tail, state, stream1 = struct.unpack_from("<III", data, STREAM_SIZES_OFFSET)
    return state, stream1, tail


def stream_offsets(data):
    return NEW_LAYOUT if data[GENERATION_OFFSET] >= 2 else OLD_LAYOUT


def validate(data, label, allow_stale_tail=False):
    _, version, _ = struct.unpack_from("<QII", data, 0)
    _, record_version, _ = struct.unpack_from("<QII", data, RECORD_OFFSET)
    if version != FILE_HEADER_VERSION or record_version != RECORD_VERSION:
        raise ValueError(f"{label}: not a Ragnarok checkpoint (versions {version}/{record_version})")
    state, stream1, tail = stream_sizes(data)
    if state + stream1 + tail != struct.unpack_from("<I", data, TOTAL_SIZE_OFFSET)[0]:
        raise ValueError(f"{label}: stream sizes do not add up to the total size")
    end = stream_offsets(data)[1] + tail
    # The PS5 writer can leave stale bytes from an older, longer save after the tail stream.
    if end > len(data) or (end < len(data) and not allow_stale_tail):
        raise ValueError(f"{label}: tail stream does not end at the end of the file")
    if data[end - 4 : end] != END_MARKER:
        raise ValueError(f"{label}: missing end marker")


def resources_start(data, tail_start):
    for match in re.finditer(re.escape(END_MARKER), data[tail_start:]):
        position = tail_start + match.start()
        before = data[position - RESOURCES_GAP : position]
        after = data[position + 4 : position + 16]
        if before == bytes(RESOURCES_GAP) and re.search(rb"[A-Za-z]{4}", after):
            return position
    raise ValueError("resource list not found")


def looks_like_record(data, offset):
    return data[offset + 0xC : offset + 0x10] == bytes(4) and data[offset + 0x10 : offset + 0x18] == data[offset + 0x18 : offset + 0x20]


def records_start(data, tail_start, end):
    position = tail_start
    while True:
        position = data.find(b"\x03\x00\x00\x00", position + 1, end)
        if position < 0:
            raise ValueError("zone record list not found")
        start = position - 4
        first = start + TAIL_FIRST_RECORD
        if data[start + 8 : first] != bytes(first - start - 8):
            continue
        empty = struct.unpack_from("<I", data, start)[0] == 0 and end - first == RESOURCES_GAP and data[first:end] == bytes(RESOURCES_GAP)
        if empty or (looks_like_record(data, first) and data[first + 0x10 : first + 0x18] != bytes(8)):
            return start


def record_offsets(data, records):
    """Start offset of every zone record in the records region."""
    offsets = []
    position = records.start + TAIL_FIRST_RECORD
    count = struct.unpack_from("<I", data, records.start)[0]
    for _ in range(count):
        if not looks_like_record(data, position):
            raise ValueError(f"zone record list broken at {position:#x}")
        offsets.append(position)
        position += RECORD_FIXED + struct.unpack_from("<I", data, position + RECORD_SIZE_OFFSET)[0]
    return offsets


def layout(data):
    """Slices of the regions that carry data; gaps between them are padding."""
    state_size, stream1_size, tail_size = stream_sizes(data)
    stream1_offset, tail_offset = stream_offsets(data)
    resources = resources_start(data, tail_offset)
    records = records_start(data, tail_offset, resources)
    return {
        "header": slice(RECORD_OFFSET, STATE_OFFSET),
        "state": slice(STATE_OFFSET, STATE_OFFSET + state_size),
        "stream1": slice(stream1_offset, stream1_offset + stream1_size),
        "player": slice(tail_offset, records),
        "records": slice(records, resources),
        "resources": slice(resources, tail_offset + tail_size),
    }


def assemble(file_header, parts):
    """Build a generation 2 file: streams at the PC offsets, padding zeroed, sizes rewritten."""
    stream1_offset, tail_offset = NEW_LAYOUT
    state, stream1 = parts["state"], parts["stream1"]
    if STATE_OFFSET + len(state) > stream1_offset or stream1_offset + len(stream1) > tail_offset:
        raise ValueError("stream does not fit the PC capacity")
    tail = parts["player"] + parts["records"] + parts["resources"]
    header = bytearray(parts["header"])
    struct.pack_into("<III", header, STREAM_SIZES_OFFSET - RECORD_OFFSET, len(tail), len(state), len(stream1))
    header[GENERATION_OFFSET - RECORD_OFFSET] = 2
    result = bytearray(file_header) + header
    result += state + bytes(stream1_offset - STATE_OFFSET - len(state))
    result += stream1 + bytes(tail_offset - stream1_offset - len(stream1))
    result += tail
    struct.pack_into("<I", result, TOTAL_SIZE_OFFSET, len(state) + len(stream1) + len(tail))
    return bytes(result)


def empty_records(parts):
    """Replace the zone record list with an empty one (count 0, then the resources gap)."""
    parts["records"] = struct.pack("<II", 0, 3) + bytes(TAIL_FIRST_RECORD - 8) + bytes(RESOURCES_GAP)


def compose(base, donor=None, swap=(), no_records=False):
    parts = {name: base[region] for name, region in layout(base).items()}
    if donor is not None:
        donor_parts = {name: donor[region] for name, region in layout(donor).items()}
        for name in swap:
            parts[name] = donor_parts[name]
    if no_records:
        empty_records(parts)
    return assemble(base[:RECORD_OFFSET], parts)


def patch_build(data):
    struct.pack_into("<I", data, BUILD_OFFSET, PC_BUILD)
    struct.pack_into("<I", data, RECORD_OFFSET + BUILD_OFFSET, PC_BUILD)
    struct.pack_into("<I", data, SLOT_SIZE_OFFSET, PC_SLOT_SIZE)


def patch_versions(data, platform):
    for offset, values in SECTION_VERSIONS.items():
        data[offset] = values[platform == "pc"]


def patch_tag(data):
    struct.pack_into("<I", data, STATE_OFFSET, PC_STATE_TAG)


def patch_player_hash(data):
    struct.pack_into("<I", data, stream_offsets(data)[1], PC_PLAYER_HASH)


def patch_record_flags(data):
    """Give every zone record with a payload the PC flag bytes."""
    for offset in record_offsets(data, layout(data)["records"]):
        if struct.unpack_from("<I", data, offset + RECORD_SIZE_OFFSET)[0]:
            data[offset + RECORD_FLAGS_OFFSET : offset + RECORD_FLAGS_OFFSET + 4] = PC_RECORD_FLAGS


def convert(data, build=True, versions=None, tag=False, player_hash=False, record_flags=False, no_records=False, template=None, base_is_template=False, swap=(), pokes=(), repack=False):
    if repack:
        import repack as repacker

        data = repacker.repack(data)
    base, donor = (template, data) if base_is_template else (data, template)
    result = bytearray(compose(base, donor, swap, no_records))
    if build:
        patch_build(result)
    if versions:
        patch_versions(result, versions)
    for offset, value in pokes:
        result[offset] = value
    if tag:
        patch_tag(result)
    if player_hash:
        patch_player_hash(result)
    if record_flags:
        patch_record_flags(result)
    return bytes(result)


def describe(data):
    timestamp, _, build = struct.unpack_from("<QII", data, 0)
    name = data[RECORD_OFFSET + 0x10 : RECORD_OFFSET + 0x110].split(b"\0")[0].decode("utf-8", "replace")
    versions = " ".join(f"{data[offset]:02x}" for offset in SECTION_VERSIONS)
    tag = struct.unpack_from("<I", data, STATE_OFFSET)[0]
    player_hash = struct.unpack_from("<I", data, stream_offsets(data)[1])[0]
    regions = layout(data)
    sizes = " ".join(f"{name}={region.stop - region.start:#x}" for name, region in regions.items())
    count = struct.unpack_from("<I", data, regions["records"].start)[0]
    return (
        f"build={build} gen={data[GENERATION_OFFSET]} versions={versions} tag={tag:#010x} "
        f"player_hash={player_hash:#010x} records={count} len={len(data)} {sizes} time={timestamp} {name}"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="decrypted PS5 GOWRSAVE<n> file")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--keep-build", action="store_true", help="leave build number and slot size alone")
    parser.add_argument("--versions", choices=("pc", "ps5"), help="write that platform's section version bytes")
    parser.add_argument("--tag", action="store_true", help="write the PC state tag at 0x440 (diagnostic only: corrupts the state stream)")
    parser.add_argument("--player-hash", action="store_true", help="write the PC hash at the tail stream start")
    parser.add_argument("--record-flags", action="store_true", help="give zone records the PC flag bytes")
    parser.add_argument("--no-records", action="store_true", help="drop the zone record list")
    parser.add_argument("--template", type=Path, help="PC checkpoint to swap regions with")
    parser.add_argument("--base", choices=("source", "template"), default="source", help="which file provides everything not swapped")
    parser.add_argument("--swap", default="", help="comma separated regions taken from the other file: " + ", ".join(REGIONS))
    parser.add_argument("--set", action="append", default=[], metavar="OFFSET=VALUE", help="write a byte, e.g. 0x1c7=2")
    parser.add_argument("--repack", action="store_true", help="re-serialize PS5-packed structures with PC alignment first")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    swap = tuple(name for name in args.swap.split(",") if name)
    unknown = set(swap) - set(REGIONS)
    if unknown:
        parser.error(f"unknown regions: {', '.join(sorted(unknown))}")
    data = args.source.read_bytes()
    validate(data, str(args.source), allow_stale_tail=True)
    template = None
    if args.template:
        template = args.template.read_bytes()
        validate(template, str(args.template))
    result = convert(
        data,
        build=not args.keep_build,
        versions=args.versions,
        tag=args.tag,
        player_hash=args.player_hash,
        record_flags=args.record_flags,
        no_records=args.no_records,
        template=template,
        base_is_template=args.base == "template",
        swap=swap,
        pokes=[(int(offset, 0), int(value, 0)) for offset, value in (item.split("=") for item in args.set)],
        repack=args.repack,
    )
    validate(result, "result")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(result)
    print(f"{args.source.name}: {describe(data)}")
    print(f"{args.out}: {describe(result)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
