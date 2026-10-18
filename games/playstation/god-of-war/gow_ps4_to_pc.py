"""Convert a decrypted God of War (2018) PS4 save (memory.dat) into a PC game.sav.

The PC game.sav uses the same 32 MiB container as the PS4 memory.dat: a 0x40 byte
file header, a 0x1000 byte profile record, then 20 checkpoint slots of 0x1998C8
bytes. Dropping the PS4 file in as-is lets the PC build start New Game+ from it but
crashes when loading a checkpoint directly.

This tool keeps the file header and profile record from a game.sav written by the
PC game (so the PC build sees its own header format, Steam account id and settings
layout), transplants the PS4 checkpoint slots into it, and by default rewrites the
per-checkpoint fields that differ between platforms: the build number in the
record header, the platform marker byte in the checkpoint stats block, and the
section hash that opens the player-state block.

Usage (working PS4 -> PC recipe; the other flags are diagnostics):
    python gow_ps4_to_pc.py memory.dat --template <pc game.sav> --repack-resources --out game.sav
"""

import argparse
import fnmatch
import struct
import sys
from pathlib import Path

FILE_SIZE = 0x1FFFFE0
PROFILE_OFFSET = 0x40
SLOT_AREA_OFFSET = 0x1040
SLOT_SIZE = 0x1998C8
SLOT_COUNT = 20
FILE_HEADER_VERSION = 4
CHECKPOINT_VERSION = 0xA
# Record header: u64 timestamp, u32 format version, u32 build number.
RECORD_BUILD_OFFSET = 0xC
# Constant per platform in the checkpoint stats block: 22 on PS4, 1 on PC.
PLATFORM_MARKER_OFFSET = 0x128
# u32 section hash that opens the player-state block (position, rotation,
# animation state): 0x8DD0B211 on PS4, 0xD31AD457 on PC.
PLAYER_STATE_HASH_OFFSET = 0x30400
# The player region ends at the second zone block, which starts with its count;
# the first zone name follows 0x88 bytes later.
ZONE_LIST_NAME = b"For100_River"
ZONE2_NAME_OFFSET = 0x88
# Fixed-size world-state block between the checkpoint header and the inventory table.
WORLD_BLOCK = slice(0x400, 0x10400)
# Zone-state records in the tail: 32-byte name, u16 flags, u16 format version
# (0x133 on PS4, 0x135 on PC), u16 payload size, u16 count, payload, 130-byte trailer.
# Zones without saved state have flags 0 and no version, size or payload.
ZONE_RECORD_FLAGS_OFFSET = 0x20
ZONE_RECORD_HAS_STATE = 0x100
ZONE_RECORD_VERSION_OFFSET = 0x22
ZONE_RECORD_SIZE_OFFSET = 0x24
ZONE_RECORD_FIXED_BYTES = 40 + 130
ZONE_RECORD_EMPTY_BYTES = 168
ZONE_RECORD_TRAILER_BYTES = 130
# Two pointer pairs inside the trailer (offsets relative to the trailer start).
ZONE_TRAILER_POINTER_RANGES = (slice(46, 62), slice(70, 86))
TRANSPLANT_MODES = ("player", "world", "tail", "resources")
# The loader's own view of a zone: a fixed struct with the blob size at +0x40, then the blob.
ZONE_STRUCT_BYTES = 0xA8
ZONE_STRUCT_BLOB_SIZE_OFFSET = 0x40
# Resource bundle list entries: the PS4 build packs the id and flags after the u32 marker.
PC_RESOURCE_ENTRY_BYTES = 0x70
PS4_RESOURCE_ENTRY_BYTES = 0x68
RESOURCE_MARKER_OFFSET = 0x40


def slot_range(index):
    start = SLOT_AREA_OFFSET + index * SLOT_SIZE
    return slice(start, start + SLOT_SIZE)


def record_header(data, offset):
    return struct.unpack_from("<QII", data, offset)


def used_slots(data):
    result = []
    for index in range(SLOT_COUNT):
        timestamp, version, _ = record_header(data, slot_range(index).start)
        if timestamp and version == CHECKPOINT_VERSION:
            result.append(index)
    return result


def newest_slot(data):
    slots = used_slots(data)
    if not slots:
        return None
    return max(slots, key=lambda index: record_header(data, slot_range(index).start)[0])


def validate(data, label):
    if len(data) != FILE_SIZE:
        raise ValueError(f"{label}: expected {FILE_SIZE} bytes, got {len(data)}")
    _, version, _ = record_header(data, 0)
    if version != FILE_HEADER_VERSION:
        raise ValueError(f"{label}: unexpected file header version {version}")


def region_bounds(record):
    """Locate the player region of a checkpoint.

    Returns (start, end): the player-state hash and the 8-byte terminator just
    before the second zone block's count.
    """
    zone2 = record.find(ZONE_LIST_NAME, PLAYER_STATE_HASH_OFFSET) - ZONE2_NAME_OFFSET
    return PLAYER_STATE_HASH_OFFSET, zone2 - 8


def tail_record_offsets(record):
    """Start offset of every zone-state record in the tail.

    The tail starts with a u32 record count; each record's length follows from
    its flags and size fields, so the records are walked by length. Zone names
    are not searched for, because they can also appear as text inside payloads.
    """
    zone2 = record.find(ZONE_LIST_NAME, PLAYER_STATE_HASH_OFFSET) - ZONE2_NAME_OFFSET
    count = struct.unpack_from("<I", record, zone2)[0]
    offsets = []
    position = zone2 + ZONE2_NAME_OFFSET
    for _ in range(count):
        offsets.append(position)
        position += zone_record_length(record, position)
    return offsets


def zone_record_length(record, offset):
    if struct.unpack_from("<H", record, offset + ZONE_RECORD_FLAGS_OFFSET)[0] != ZONE_RECORD_HAS_STATE:
        return ZONE_RECORD_EMPTY_BYTES
    return ZONE_RECORD_FIXED_BYTES + struct.unpack_from("<H", record, offset + ZONE_RECORD_SIZE_OFFSET)[0]


def zone_record_version(record):
    first = record.find(ZONE_LIST_NAME, PLAYER_STATE_HASH_OFFSET)
    return struct.unpack_from("<H", record, first + ZONE_RECORD_VERSION_OFFSET)[0]


def patch_zone_record_versions(slot, source_version, target_version):
    for offset in tail_record_offsets(slot):
        if struct.unpack_from("<H", slot, offset + ZONE_RECORD_FLAGS_OFFSET)[0] != ZONE_RECORD_HAS_STATE:
            continue
        if struct.unpack_from("<H", slot, offset + ZONE_RECORD_VERSION_OFFSET)[0] == source_version:
            struct.pack_into("<H", slot, offset + ZONE_RECORD_VERSION_OFFSET, target_version)


def resource_section_start(record):
    """Offset just past the last zone-state record, where the resource bundle list begins."""
    last = tail_record_offsets(record)[-1]
    return last + zone_record_length(record, last)


def resource_list_offset(record):
    """Offset of the resource bundle list's u32 count, following the PC loader's own tail grammar.

    The loader (GoW.exe 0x14082cf00) reads a u32 zone count, then per zone a
    0xA8-byte struct (name at +0x84, blob size at +0x40) followed by that many
    bytes of blob. The record model above is the same stream cut at different
    points: a record's last 132 bytes are the start of the next zone's struct.
    """
    zone2 = record.find(ZONE_LIST_NAME, PLAYER_STATE_HASH_OFFSET) - ZONE2_NAME_OFFSET
    position = zone2 + 4
    for _ in range(struct.unpack_from("<I", record, zone2)[0]):
        position += ZONE_STRUCT_BYTES + struct.unpack_from("<I", record, position + ZONE_STRUCT_BLOB_SIZE_OFFSET)[0]
    return position


def resource_entry_size(record):
    """0x70 when the resource list uses the PC layout, 0x68 for the packed PS4 layout."""
    start = resource_list_offset(record) + 4
    for size in (PC_RESOURCE_ENTRY_BYTES, PS4_RESOURCE_ENTRY_BYTES):
        if record[start + size + RESOURCE_MARKER_OFFSET:start + size + RESOURCE_MARKER_OFFSET + 4] == bytes([0xFF]) * 4:
            return size
    raise ValueError("resource list layout not recognized")


def repack_resource_list(record):
    """Rewrite a PS4 resource bundle list in the PC layout, shifting what follows it.

    PS4 entry (0x68): 64 flag bytes, u32 marker, u16 id, u8 flags, name[0x21].
    PC entry (0x70): 64 flag bytes, u32 marker, u32 id, u32 flags, name[0x24].
    """
    record = bytes(record)
    if resource_entry_size(record) == PC_RESOURCE_ENTRY_BYTES:
        return record
    start = resource_list_offset(record)
    count = struct.unpack_from("<I", record, start)[0]
    entries = bytearray(record[start:start + 4])
    position = start + 4
    for _ in range(count):
        entry = record[position:position + PS4_RESOURCE_ENTRY_BYTES]
        bundle_id, flags = struct.unpack_from("<HB", entry, RESOURCE_MARKER_OFFSET + 4)
        entries += entry[:RESOURCE_MARKER_OFFSET + 4] + struct.pack("<II", bundle_id, flags)
        entries += entry[RESOURCE_MARKER_OFFSET + 7:].ljust(PC_RESOURCE_ENTRY_BYTES - RESOURCE_MARKER_OFFSET - 12, b"\0")
        position += PS4_RESOURCE_ENTRY_BYTES
    rest = record[position:].rstrip(b"\0")
    merged = record[:start] + bytes(entries) + rest
    if len(merged) > SLOT_SIZE:
        raise ValueError("repacked checkpoint does not fit in a slot")
    return merged.ljust(SLOT_SIZE, b"\0")


def zone_records(record):
    """Ordered list of (name, record bytes) for every zone-state record in the tail."""
    records = []
    for offset in tail_record_offsets(record):
        name = bytes(record[offset:offset + 32]).split(b"\0")[0]
        records.append((name, bytes(record[offset:offset + zone_record_length(record, offset)])))
    return records


def repair_zone_record(record, pc_record, fix_empty, pointers_from_pc, trailer_from_pc):
    """Apply per-record repairs to a PS4 zone record, borrowing from the PC record of the same zone if given."""
    record = bytearray(record)
    normal = struct.unpack_from("<H", record, ZONE_RECORD_FLAGS_OFFSET)[0] == ZONE_RECORD_HAS_STATE
    if fix_empty and not normal:
        # The PC game writes zeros after the flags of a stateless record; PS4 leaves uninitialized bytes.
        record[ZONE_RECORD_VERSION_OFFSET:ZONE_RECORD_VERSION_OFFSET + 4] = bytes(4)
    if pc_record is None:
        return bytes(record)
    trailer = len(record) - ZONE_RECORD_TRAILER_BYTES
    pc_trailer = pc_record[len(pc_record) - ZONE_RECORD_TRAILER_BYTES:]
    pc_normal = struct.unpack_from("<H", pc_record, ZONE_RECORD_FLAGS_OFFSET)[0] == ZONE_RECORD_HAS_STATE
    if trailer_from_pc and normal and pc_normal:
        record[trailer:] = pc_trailer
    elif pointers_from_pc or trailer_from_pc:
        for span in ZONE_TRAILER_POINTER_RANGES:
            record[trailer + span.start:trailer + span.stop] = pc_trailer[span]
    return bytes(record)


def rebuild_zone_records(
    ps4_slot,
    pc_slot,
    patterns,
    drop_missing=False,
    header_from_pc=False,
    fix_empty=False,
    pointers_from_pc=False,
    trailer_from_pc=False,
):
    """Rebuild the tail's zone-state records from the PS4 checkpoint.

    Records whose names match any pattern are taken from the PC checkpoint when
    it has them. Zones the PC checkpoint has no record for keep their PS4 record,
    or are dropped when drop_missing is set. The tail header (count and the
    bytes up to the first record) can also come from the PC checkpoint, and the
    remaining PS4 records can be repaired (see repair_zone_record). The record
    count is updated and the resource list after the records is preserved.
    """
    ps4_slot, pc_slot = bytes(ps4_slot), bytes(pc_slot)
    pc_by_name = dict(zone_records(pc_slot))
    rebuilt = []
    for name, record in zone_records(ps4_slot):
        matches = any(fnmatch.fnmatchcase(name.decode("latin1"), pattern) for pattern in patterns)
        if name in pc_by_name and matches:
            rebuilt.append(pc_by_name[name])
        elif name in pc_by_name or not drop_missing:
            rebuilt.append(repair_zone_record(record, pc_by_name.get(name), fix_empty, pointers_from_pc, trailer_from_pc))
    zone2 = ps4_slot.find(ZONE_LIST_NAME, PLAYER_STATE_HASH_OFFSET) - ZONE2_NAME_OFFSET
    header = bytearray(ps4_slot[zone2:zone2 + ZONE2_NAME_OFFSET])
    if header_from_pc:
        pc_zone2 = pc_slot.find(ZONE_LIST_NAME, PLAYER_STATE_HASH_OFFSET) - ZONE2_NAME_OFFSET
        header = bytearray(pc_slot[pc_zone2:pc_zone2 + ZONE2_NAME_OFFSET])
    struct.pack_into("<I", header, 0, len(rebuilt))
    merged = ps4_slot[:zone2] + bytes(header) + b"".join(rebuilt) + ps4_slot[resource_section_start(ps4_slot):]
    if any(merged[SLOT_SIZE:]):
        raise ValueError("rebuilt checkpoint does not fit in a slot")
    return merged[:SLOT_SIZE].ljust(SLOT_SIZE, b"\0")


def merge_ps4_zone_records(slot, ps4_slot, patterns, append_ps4_only=False, part="record"):
    """Bring PS4 zone records into a checkpoint whose tail came from the PC template.

    The tail header, record order and resource list stay as they are. Records
    whose names match any pattern are replaced by the PS4 checkpoint's record of
    the same zone (with its format version set to the existing record's), and
    with append_ps4_only the PS4 records for zones the tail lacks are added at
    the end with the record count updated. part limits the swap of a matching
    record to its 'payload' (size field and payload, keeping the existing
    header and 130-byte trailer) or its 'trailer'; records where either side
    has no saved state are then left as they are.
    """
    slot, ps4_slot = bytes(slot), bytes(ps4_slot)
    ps4_records = zone_records(ps4_slot)
    ps4_by_name = dict(ps4_records)
    version = zone_record_version(slot)
    merged_records = []
    for name, record in zone_records(slot):
        matches = any(fnmatch.fnmatchcase(name.decode("latin1"), pattern) for pattern in patterns)
        if matches and name in ps4_by_name:
            record = swap_zone_record_part(record, ps4_by_name[name], part)
        merged_records.append(record)
    if append_ps4_only:
        present = {name for name, _ in zone_records(slot)}
        merged_records += [record for name, record in ps4_records if name not in present]
    merged_records = [bytearray(record) for record in merged_records]
    for record in merged_records:
        if struct.unpack_from("<H", record, ZONE_RECORD_FLAGS_OFFSET)[0] == ZONE_RECORD_HAS_STATE:
            struct.pack_into("<H", record, ZONE_RECORD_VERSION_OFFSET, version)
    zone2 = slot.find(ZONE_LIST_NAME, PLAYER_STATE_HASH_OFFSET) - ZONE2_NAME_OFFSET
    header = bytearray(slot[zone2:zone2 + ZONE2_NAME_OFFSET])
    struct.pack_into("<I", header, 0, len(merged_records))
    merged = slot[:zone2] + bytes(header) + b"".join(merged_records) + slot[resource_section_start(slot):]
    if any(merged[SLOT_SIZE:]):
        raise ValueError("merged checkpoint does not fit in a slot")
    return merged[:SLOT_SIZE].ljust(SLOT_SIZE, b"\0")


def swap_zone_record_part(record, ps4_record, part):
    if part == "record":
        return ps4_record
    has_state = (
        struct.unpack_from("<H", record, ZONE_RECORD_FLAGS_OFFSET)[0] == ZONE_RECORD_HAS_STATE
        and struct.unpack_from("<H", ps4_record, ZONE_RECORD_FLAGS_OFFSET)[0] == ZONE_RECORD_HAS_STATE
    )
    if not has_state:
        return record
    trailer = len(record) - ZONE_RECORD_TRAILER_BYTES
    ps4_trailer = len(ps4_record) - ZONE_RECORD_TRAILER_BYTES
    if part == "payload":
        return record[:ZONE_RECORD_SIZE_OFFSET] + ps4_record[ZONE_RECORD_SIZE_OFFSET:ps4_trailer] + record[trailer:]
    if part == "trailer":
        return record[:trailer] + ps4_record[ps4_trailer:]
    raise ValueError(f"unknown zone record part {part!r}")


def transplant_region(ps4_slot, pc_slot, mode):
    """Return the PS4 checkpoint with one region replaced by the PC checkpoint's.

    'player' swaps the player region, 'world' the fixed world block, 'tail'
    everything from the second zone block to the end of the record, and
    'resources' only the resource bundle list after the last zone record.
    """
    ps4_slot, pc_slot = bytes(ps4_slot), bytes(pc_slot)
    if mode == "world":
        merged = ps4_slot[:WORLD_BLOCK.start] + pc_slot[WORLD_BLOCK] + ps4_slot[WORLD_BLOCK.stop:]
    elif mode == "resources":
        merged = ps4_slot[:resource_section_start(ps4_slot)] + pc_slot[resource_section_start(pc_slot):]
    else:
        ps4_start, ps4_end = region_bounds(ps4_slot)
        pc_start, pc_end = region_bounds(pc_slot)
        if mode == "player":
            merged = ps4_slot[:ps4_start] + pc_slot[pc_start:pc_end] + ps4_slot[ps4_end:]
        else:
            merged = ps4_slot[:ps4_end] + pc_slot[pc_end:]
    if any(merged[SLOT_SIZE:]):
        raise ValueError("transplanted checkpoint does not fit in a slot")
    return merged[:SLOT_SIZE].ljust(SLOT_SIZE, b"\0")


def convert(
    ps4,
    template,
    patch_build=True,
    patch_marker=True,
    patch_player_hash=True,
    patch_zone_version=True,
    keep_profile=False,
    transplant=(),
    zones_from_template=(),
    drop_zones_missing_from_template=False,
    tail_header_from_template=False,
    fix_empty_zone_records=False,
    zone_pointers_from_template=False,
    zone_trailers_from_template=False,
    template_slot=None,
    ps4_zones=(),
    append_ps4_only_zones=False,
    ps4_zone_part="record",
    repack_resources=False,
):
    validate(ps4, "PS4 save")
    validate(template, "template")
    unknown = set(transplant) - set(TRANSPLANT_MODES)
    if unknown:
        raise ValueError(f"unknown transplant mode {sorted(unknown)}")
    reference = newest_slot(template) if template_slot is None else template_slot
    if reference is None or reference not in used_slots(template):
        raise ValueError("template has no PC checkpoint to copy the build number and platform marker from")
    reference_start = slot_range(reference).start
    pc_build = record_header(template, reference_start)[2]
    pc_marker = template[reference_start + PLATFORM_MARKER_OFFSET]
    pc_player_hash = struct.unpack_from("<I", template, reference_start + PLAYER_STATE_HASH_OFFSET)[0]
    pc_slot = template[slot_range(reference)]
    pc_zone_version = zone_record_version(pc_slot)

    out = bytearray(ps4 if keep_profile else template)
    for index in range(SLOT_COUNT):
        out[slot_range(index)] = bytes(SLOT_SIZE)
    for index in used_slots(ps4):
        slot = bytearray(ps4[slot_range(index)])
        # Patch versions before transplants, while the zone names in the world block still match the tail.
        if patch_zone_version:
            patch_zone_record_versions(slot, zone_record_version(slot), pc_zone_version)
        rebuild = (
            zones_from_template or drop_zones_missing_from_template or tail_header_from_template
            or fix_empty_zone_records or zone_pointers_from_template or zone_trailers_from_template
        )
        if rebuild:
            slot = rebuild_zone_records(
                slot,
                pc_slot,
                zones_from_template,
                drop_missing=drop_zones_missing_from_template,
                header_from_pc=tail_header_from_template,
                fix_empty=fix_empty_zone_records,
                pointers_from_pc=zone_pointers_from_template,
                trailer_from_pc=zone_trailers_from_template,
            )
        for mode in transplant:
            slot = transplant_region(slot, pc_slot, mode)
        if ps4_zones or append_ps4_only_zones:
            slot = merge_ps4_zone_records(slot, ps4[slot_range(index)], ps4_zones, append_ps4_only_zones, ps4_zone_part)
        if repack_resources:
            slot = repack_resource_list(slot)
        out[slot_range(index)] = slot
        start = slot_range(index).start
        if patch_build:
            struct.pack_into("<I", out, start + RECORD_BUILD_OFFSET, pc_build)
        if patch_marker:
            out[start + PLATFORM_MARKER_OFFSET] = pc_marker
        if patch_player_hash:
            struct.pack_into("<I", out, start + PLAYER_STATE_HASH_OFFSET, pc_player_hash)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, fromfile_prefix_chars="@"
    )
    parser.add_argument("ps4_save", type=Path, help="decrypted PS4 memory.dat")
    parser.add_argument("--template", type=Path, required=True, help="game.sav written by the PC game")
    parser.add_argument("--out", type=Path, default=Path("game.sav"), help="output path (default: game.sav)")
    parser.add_argument("--keep-build", action="store_true", help="leave the PS4 build number in each checkpoint")
    parser.add_argument("--keep-marker", action="store_true", help="leave the PS4 platform marker in each checkpoint")
    parser.add_argument("--keep-player-hash", action="store_true", help="leave the PS4 player-state section hash in each checkpoint")
    parser.add_argument("--repack-resources", action="store_true", help="rewrite a packed PS4 resource bundle list in the PC layout")
    parser.add_argument("--keep-zone-version", action="store_true", help="leave the PS4 format version in each zone-state record")
    parser.add_argument(
        "--keep-profile",
        action="store_true",
        help="keep the PS4 file header and profile record; the template is then only used for the build number and marker",
    )
    parser.add_argument(
        "--transplant",
        nargs="+",
        choices=TRANSPLANT_MODES,
        default=(),
        help="replace regions of each PS4 checkpoint with the template checkpoint's: 'player' (player-state hash "
        "up to the second zone block), 'world' (fixed world block before the inventory), 'tail' (second zone "
        "block to the end), 'resources' (resource bundle list after the last zone record)",
    )
    parser.add_argument(
        "--zones-from-template",
        nargs="+",
        default=(),
        metavar="PATTERN",
        help="replace the zone-state records whose names match these shell patterns (for example 'Nid*') with "
        "the template checkpoint's; zones missing from the template keep their PS4 record",
    )
    parser.add_argument(
        "--drop-zones-missing-from-template",
        action="store_true",
        help="remove zone-state records for zones the template checkpoint has no record for",
    )
    parser.add_argument(
        "--tail-header-from-template",
        action="store_true",
        help="take the small header block in front of the zone-state records from the template checkpoint",
    )
    parser.add_argument("--fix-empty-zone-records", action="store_true", help="zero the uninitialized bytes after the flags of stateless zone records")
    parser.add_argument("--zone-pointers-from-template", action="store_true", help="copy the two trailer pointer pairs of each zone record from the template's record of the same zone")
    parser.add_argument("--zone-trailers-from-template", action="store_true", help="copy the whole 130-byte trailer of each zone record from the template's record of the same zone")
    parser.add_argument("--template-slot", type=int, help="template slot index to copy from (default: newest)")
    parser.add_argument(
        "--ps4-zones",
        nargs="+",
        default=(),
        metavar="PATTERN",
        help="after the transplants, put back the PS4 zone records whose names match these shell patterns, keeping "
        "the tail's order, header and resource list (use with '--transplant tail'; '@FILE' reads arguments from a file)",
    )
    parser.add_argument(
        "--ps4-zone-part",
        choices=("record", "payload", "trailer"),
        default="record",
        help="what --ps4-zones takes from each PS4 record: the whole record (default), only the payload, or only the 130-byte trailer",
    )
    parser.add_argument("--append-ps4-only-zones", action="store_true", help="after the transplants, append the PS4 records for zones the tail has no record for")
    args = parser.parse_args(argv)

    if args.out.exists():
        parser.error(f"{args.out} already exists, refusing to overwrite")
    try:
        out = convert(
            args.ps4_save.read_bytes(),
            args.template.read_bytes(),
            patch_build=not args.keep_build,
            patch_marker=not args.keep_marker,
            patch_player_hash=not args.keep_player_hash,
            patch_zone_version=not args.keep_zone_version,
            keep_profile=args.keep_profile,
            transplant=args.transplant,
            zones_from_template=args.zones_from_template,
            drop_zones_missing_from_template=args.drop_zones_missing_from_template,
            tail_header_from_template=args.tail_header_from_template,
            fix_empty_zone_records=args.fix_empty_zone_records,
            zone_pointers_from_template=args.zone_pointers_from_template,
            zone_trailers_from_template=args.zone_trailers_from_template,
            template_slot=args.template_slot,
            ps4_zones=args.ps4_zones,
            append_ps4_only_zones=args.append_ps4_only_zones,
            ps4_zone_part=args.ps4_zone_part,
            repack_resources=args.repack_resources,
        )
    except ValueError as error:
        parser.error(str(error))
    args.out.write_bytes(out)
    print(f"wrote {args.out} with {len(used_slots(out))} checkpoint slots")


if __name__ == "__main__":
    sys.exit(main())
