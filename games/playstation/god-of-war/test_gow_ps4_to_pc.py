import struct
import unittest

import gow_ps4_to_pc as conv


def record_header(timestamp, version, build):
    return struct.pack("<QII", timestamp, version, build)


def make_save(header_build, header_tail, profile, slots):
    data = bytearray(conv.FILE_SIZE)
    data[0:0x10] = record_header(0x5D000000, 4, header_build)
    data[0x10:0x1C] = struct.pack("<III", 0xD0, conv.SLOT_SIZE, conv.FILE_SIZE)
    data[0x1C:0x24] = header_tail
    data[conv.PROFILE_OFFSET:conv.PROFILE_OFFSET + len(profile)] = profile
    for index, record in slots.items():
        start = conv.SLOT_AREA_OFFSET + index * conv.SLOT_SIZE
        data[start:start + len(record)] = record
    return data


def make_checkpoint(timestamp, build, marker, body=b"body", player_hash=None, tag=b"X"):
    """Synthetic checkpoint with the landmarks the converter relies on.

    Layout after the player hash: lore list framed by FA FF + u32 size, a gap of
    zeros, the trailing object block, then the second zone block starting with a
    count and 'For100_River' 0x88 bytes later, then a tail.
    """
    record = bytearray(conv.PLAYER_STATE_HASH_OFFSET + 0x3000)
    record[0:0x10] = record_header(timestamp, conv.CHECKPOINT_VERSION, build)
    record[0x10:0x18] = b"Niflheim"
    record[conv.PLATFORM_MARKER_OFFSET] = marker
    record[0x400:0x400 + len(body)] = body
    if player_hash is None:
        player_hash = PS4_PLAYER_HASH if marker == 22 else PC_PLAYER_HASH
    struct.pack_into("<I", record, conv.PLAYER_STATE_HASH_OFFSET, player_hash)
    player = conv.PLAYER_STATE_HASH_OFFSET
    record[player + 0x18:player + 0x20] = b"pos" + tag + b"pos" + tag
    lore = player + 0x600
    lore_size = 0x40 if marker == 22 else 0x30
    record[lore:lore + 2] = b"\xfa\xff"
    struct.pack_into("<I", record, lore + 2, lore_size)
    record[lore + 6:lore + 6 + lore_size] = tag * lore_size
    objects = lore + 6 + lore_size + 0x1200
    record[objects:objects + 8] = b"\x11\0\0\0obj" + tag
    # First zone list in the world block: count, then name32, two pointers, payload length, flags, payload.
    zones = (b"For100_River", b"Zone_B" + tag, b"Zone_C_empty")
    position = 0x5000
    struct.pack_into("<I", record, position - 4, len(zones))
    for name in zones:
        record[position:position + len(name)] = name
        struct.pack_into("<QQII", record, position + 32, 1, 1, 4, 0)
        record[position + 56:position + 60] = b"\x02\0\0\0"
        position += 60
    # Tail: zone-state records in the same order, name32 + header + payload + 130-byte trailer.
    zone2 = objects + 0x300
    record[zone2 - 8:zone2] = b"\x04\0\0\0\0\0\0\0"
    struct.pack_into("<I", record, zone2, len(zones))
    position = zone2 + 0x88
    version = 0x133 if marker == 22 else 0x135
    for name in zones[:2]:
        record[position:position + len(name)] = name
        struct.pack_into("<HHHH", record, position + 32, 0x100, version, 8, 1)
        record[position + 40:position + 48] = b"tail" + tag * 4
        record[position + 48:position + 48 + 130] = tag * 130
        position += 40 + 8 + 130
    # The last zone has no saved state: flags 0, no version or payload, 168 bytes in total.
    record[position:position + len(zones[2])] = zones[2]
    record[position + 34:position + 38] = b"\x40\x80\x00\x80"
    record[position + 38:position + 38 + 130] = tag.lower() * 130
    position += 168
    # Resource bundle list, then the record terminator.
    resources = b"res" + tag * 5 + b"\xff\xff\xff\xff\x01\0\0Boat00" + bytes(20) + b"\xff\xff\xff\xff"
    record[position:position + len(resources)] = resources
    # A zone name quoted inside the first record's trailer must not derail the walk.
    first = zone2 + 0x88
    record[first + 60:first + 60 + len(zones[1]) + 1] = zones[1] + b"\0"
    return bytes(record)


PS4_BUILD = 2693724
PC_BUILD = 4757534
PS4_PLAYER_HASH = 0x8DD0B211
PC_PLAYER_HASH = 0xD31AD457


class ConvertTests(unittest.TestCase):
    def setUp(self):
        self.ps4_profile = record_header(1, 0x11, PS4_BUILD) + b"ps4 settings"
        self.pc_profile = record_header(2, 0x1B, PC_BUILD) + b"pc settings"
        self.ps4 = make_save(
            PS4_BUILD,
            bytes(8),
            self.ps4_profile,
            {0: make_checkpoint(100, PS4_BUILD, 22, b"first"), 3: make_checkpoint(200, PS4_BUILD, 22, b"second")},
        )
        self.template = make_save(
            PC_BUILD,
            struct.pack("<II", 3, 12345678),
            self.pc_profile,
            {0: make_checkpoint(300, PC_BUILD, 1, b"pc checkpoint")},
        )

    def test_keeps_template_header_and_profile(self):
        out = conv.convert(self.ps4, self.template)
        self.assertEqual(out[:conv.SLOT_AREA_OFFSET], self.template[:conv.SLOT_AREA_OFFSET])
        self.assertEqual(len(out), conv.FILE_SIZE)

    def test_transplants_ps4_slots_and_drops_template_slots(self):
        out = conv.convert(self.ps4, self.template, patch_build=False, patch_marker=False, patch_player_hash=False, patch_zone_version=False)
        for index in (0, 3):
            self.assertEqual(out[conv.slot_range(index)], self.ps4[conv.slot_range(index)])
        for index in range(conv.SLOT_COUNT):
            if index in (0, 3):
                continue
            self.assertFalse(any(out[conv.slot_range(index)]))

    def test_patches_build_and_marker_from_template_checkpoint(self):
        out = conv.convert(self.ps4, self.template, patch_zone_version=False)
        for index in (0, 3):
            start = conv.SLOT_AREA_OFFSET + index * conv.SLOT_SIZE
            build = struct.unpack_from("<I", out, start + conv.RECORD_BUILD_OFFSET)[0]
            self.assertEqual(build, PC_BUILD)
            self.assertEqual(out[start + conv.PLATFORM_MARKER_OFFSET], 1)
            self.assertEqual(struct.unpack_from("<I", out, start + conv.PLAYER_STATE_HASH_OFFSET)[0], PC_PLAYER_HASH)
            # Everything else in the record is untouched.
            expected = bytearray(self.ps4[conv.slot_range(index)])
            struct.pack_into("<I", expected, conv.RECORD_BUILD_OFFSET, PC_BUILD)
            expected[conv.PLATFORM_MARKER_OFFSET] = 1
            struct.pack_into("<I", expected, conv.PLAYER_STATE_HASH_OFFSET, PC_PLAYER_HASH)
            self.assertEqual(out[conv.slot_range(index)], expected)

    def test_region_bounds(self):
        record = make_checkpoint(1, PS4_BUILD, 22, tag=b"A")
        start, end = conv.region_bounds(record)
        self.assertEqual(start, conv.PLAYER_STATE_HASH_OFFSET)
        self.assertEqual(record[end:end + 8], b"\x04\0\0\0\0\0\0\0")
        self.assertEqual(record[end + 8 + 0x88:end + 8 + 0x88 + 12], b"For100_River")

    def test_transplant_player_takes_region_from_template_and_keeps_ps4_tail(self):
        ps4 = make_save(PS4_BUILD, bytes(8), self.ps4_profile, {0: make_checkpoint(100, PS4_BUILD, 22, tag=b"A")})
        pc_slot = make_checkpoint(300, PC_BUILD, 1, tag=b"B")
        template = make_save(PC_BUILD, struct.pack("<II", 3, 1), self.pc_profile, {4: pc_slot})
        out = conv.convert(ps4, template, transplant=("player",), template_slot=4, patch_build=False, patch_marker=False, patch_zone_version=False)
        slot = out[conv.slot_range(0)]
        ps4_slot = ps4[conv.slot_range(0)]
        start, ps4_end = conv.region_bounds(ps4_slot)
        _, pc_end = conv.region_bounds(pc_slot)
        self.assertEqual(slot[:start], ps4_slot[:start])
        self.assertEqual(slot[start:pc_end], pc_slot[start:pc_end])
        # The PC region is shorter, so the PS4 zone block and tail shift up and the slot is zero padded.
        tail = ps4_slot[ps4_end:]
        self.assertEqual(slot[pc_end:pc_end + len(tail)], tail)
        self.assertFalse(any(slot[pc_end + len(tail):]))
        self.assertEqual(len(slot), conv.SLOT_SIZE)

    def test_transplant_world_and_tail(self):
        ps4 = make_save(PS4_BUILD, bytes(8), self.ps4_profile, {0: make_checkpoint(100, PS4_BUILD, 22, b"ps4 world", tag=b"A")})
        pc_slot = make_checkpoint(300, PC_BUILD, 1, b"pc world!", tag=b"B")
        template = make_save(PC_BUILD, struct.pack("<II", 3, 1), self.pc_profile, {4: pc_slot})
        ps4_slot = ps4[conv.slot_range(0)]
        world = conv.convert(ps4, template, transplant=("world",), template_slot=4, patch_build=False, patch_marker=False, patch_player_hash=False, patch_zone_version=False)[conv.slot_range(0)]
        self.assertEqual(world[conv.WORLD_BLOCK], pc_slot[conv.WORLD_BLOCK])
        self.assertEqual(world[:conv.WORLD_BLOCK.start], ps4_slot[:conv.WORLD_BLOCK.start])
        self.assertEqual(world[conv.WORLD_BLOCK.stop:], ps4_slot[conv.WORLD_BLOCK.stop:])
        tail = conv.convert(ps4, template, transplant=("tail",), template_slot=4, patch_build=False, patch_marker=False, patch_player_hash=False, patch_zone_version=False)[conv.slot_range(0)]
        _, ps4_end = conv.region_bounds(ps4_slot)
        _, pc_end = conv.region_bounds(pc_slot)
        self.assertEqual(tail[:ps4_end], ps4_slot[:ps4_end])
        self.assertEqual(tail[ps4_end:ps4_end + 0x200], pc_slot[pc_end:pc_end + 0x200])
        self.assertEqual(len(tail), conv.SLOT_SIZE)

    def test_tail_record_offsets_follow_zone_names(self):
        record = make_checkpoint(1, PS4_BUILD, 22, tag=b"A")
        offsets = conv.tail_record_offsets(record)
        self.assertEqual(len(offsets), 3)
        self.assertEqual(record[offsets[0]:offsets[0] + 12], b"For100_River")
        self.assertEqual(record[offsets[1]:offsets[1] + 7], b"Zone_BA")
        self.assertEqual(record[offsets[2]:offsets[2] + 12], b"Zone_C_empty")
        self.assertEqual(offsets[1] - offsets[0], 40 + 8 + 130)
        self.assertEqual(conv.zone_record_length(record, offsets[2]), 168)

    def test_patches_zone_record_versions_from_template(self):
        out = conv.convert(self.ps4, self.template)
        for index in (0, 3):
            slot = out[conv.slot_range(index)]
            offsets = conv.tail_record_offsets(slot)
            for offset in offsets[:2]:
                self.assertEqual(struct.unpack_from("<H", slot, offset + conv.ZONE_RECORD_VERSION_OFFSET)[0], 0x135)
            # Empty records have no version field and must be left alone.
            self.assertEqual(slot[offsets[2] + 32:offsets[2] + 40], self.ps4[conv.slot_range(index)][offsets[2] + 32:offsets[2] + 40])
        kept = conv.convert(self.ps4, self.template, patch_zone_version=False)[conv.slot_range(0)]
        for offset in conv.tail_record_offsets(kept)[:2]:
            self.assertEqual(struct.unpack_from("<H", kept, offset + conv.ZONE_RECORD_VERSION_OFFSET)[0], 0x133)

    def test_transplant_resources_replaces_only_the_list_after_the_last_zone(self):
        ps4 = make_save(PS4_BUILD, bytes(8), self.ps4_profile, {0: make_checkpoint(100, PS4_BUILD, 22, tag=b"A")})
        pc_slot = make_checkpoint(300, PC_BUILD, 1, tag=b"B")
        template = make_save(PC_BUILD, struct.pack("<II", 3, 1), self.pc_profile, {4: pc_slot})
        out = conv.convert(ps4, template, transplant=("resources",), template_slot=4, patch_build=False, patch_marker=False, patch_player_hash=False, patch_zone_version=False)
        slot = out[conv.slot_range(0)]
        ps4_slot = ps4[conv.slot_range(0)]
        cut = conv.resource_section_start(ps4_slot)
        self.assertEqual(slot[:cut], ps4_slot[:cut])
        self.assertEqual(slot[cut:cut + 8], b"resBBBBB")
        self.assertEqual(slot[cut + 8:cut + 8 + 42], pc_slot[conv.resource_section_start(pc_slot) + 8:conv.resource_section_start(pc_slot) + 50])
        self.assertEqual(len(slot), conv.SLOT_SIZE)

    def test_zones_from_template_swaps_matching_records_and_keeps_unmatched(self):
        ps4 = make_save(PS4_BUILD, bytes(8), self.ps4_profile, {0: make_checkpoint(100, PS4_BUILD, 22, tag=b"A")})
        pc_slot = make_checkpoint(300, PC_BUILD, 1, tag=b"B")
        template = make_save(PC_BUILD, struct.pack("<II", 3, 1), self.pc_profile, {4: pc_slot})
        ps4_slot = ps4[conv.slot_range(0)]
        out = conv.convert(ps4, template, zones_from_template=("For*",), template_slot=4, patch_build=False, patch_marker=False, patch_player_hash=False, patch_zone_version=False)
        slot = out[conv.slot_range(0)]
        ps4_records = dict(conv.zone_records(ps4_slot))
        pc_records = dict(conv.zone_records(pc_slot))
        rebuilt = conv.zone_records(slot)
        self.assertEqual([name for name, _ in rebuilt], [b"For100_River", b"Zone_BA", b"Zone_C_empty"])
        self.assertEqual(rebuilt[0][1], pc_records[b"For100_River"])
        self.assertEqual(rebuilt[1][1], ps4_records[b"Zone_BA"])
        self.assertEqual(rebuilt[2][1], ps4_records[b"Zone_C_empty"])
        zone2 = slot.find(conv.ZONE_LIST_NAME, conv.PLAYER_STATE_HASH_OFFSET) - conv.ZONE2_NAME_OFFSET
        self.assertEqual(struct.unpack_from("<I", slot, zone2)[0], 3)
        self.assertEqual(slot[conv.resource_section_start(slot):conv.resource_section_start(slot) + 8], b"resAAAAA")
        # Zone_BA has no PC counterpart, so a catch-all pattern still keeps it.
        everything = conv.convert(ps4, template, zones_from_template=("*",), template_slot=4, patch_zone_version=False)[conv.slot_range(0)]
        self.assertEqual(dict(conv.zone_records(everything))[b"Zone_BA"], ps4_records[b"Zone_BA"])
        self.assertEqual(dict(conv.zone_records(everything))[b"Zone_C_empty"], pc_records[b"Zone_C_empty"])

    def test_drop_missing_zones_and_tail_header_from_template(self):
        ps4 = make_save(PS4_BUILD, bytes(8), self.ps4_profile, {0: make_checkpoint(100, PS4_BUILD, 22, tag=b"A")})
        pc_slot = make_checkpoint(300, PC_BUILD, 1, tag=b"B")
        template = make_save(PC_BUILD, struct.pack("<II", 3, 1), self.pc_profile, {4: pc_slot})
        ps4_slot = ps4[conv.slot_range(0)]
        slot = conv.convert(
            ps4, template, drop_zones_missing_from_template=True, tail_header_from_template=True, template_slot=4,
            patch_build=False, patch_marker=False, patch_player_hash=False, patch_zone_version=False,
        )[conv.slot_range(0)]
        rebuilt = conv.zone_records(slot)
        self.assertEqual([name for name, _ in rebuilt], [b"For100_River", b"Zone_C_empty"])
        self.assertEqual(dict(rebuilt), {name: record for name, record in conv.zone_records(ps4_slot) if name != b"Zone_BA"})
        zone2 = slot.find(conv.ZONE_LIST_NAME, conv.PLAYER_STATE_HASH_OFFSET) - conv.ZONE2_NAME_OFFSET
        pc_zone2 = pc_slot.find(conv.ZONE_LIST_NAME, conv.PLAYER_STATE_HASH_OFFSET) - conv.ZONE2_NAME_OFFSET
        self.assertEqual(struct.unpack_from("<I", slot, zone2)[0], 2)
        self.assertEqual(slot[zone2 + 4:zone2 + 0x88], pc_slot[pc_zone2 + 4:pc_zone2 + 0x88])
        self.assertEqual(slot[conv.resource_section_start(slot):conv.resource_section_start(slot) + 8], b"resAAAAA")

    def test_ps4_zones_merge_into_template_tail(self):
        ps4 = make_save(PS4_BUILD, bytes(8), self.ps4_profile, {0: make_checkpoint(100, PS4_BUILD, 22, tag=b"A")})
        pc_slot = make_checkpoint(300, PC_BUILD, 1, tag=b"B")
        template = make_save(PC_BUILD, struct.pack("<II", 3, 1), self.pc_profile, {4: pc_slot})
        ps4_slot = ps4[conv.slot_range(0)]
        slot = conv.convert(
            ps4, template, transplant=("tail",), ps4_zones=("For*",), append_ps4_only_zones=True, template_slot=4,
        )[conv.slot_range(0)]
        rebuilt = conv.zone_records(slot)
        ps4_records = dict(conv.zone_records(ps4_slot))
        pc_records = dict(conv.zone_records(pc_slot))
        self.assertEqual([name for name, _ in rebuilt], [b"For100_River", b"Zone_BB", b"Zone_C_empty", b"Zone_BA"])
        # PS4 record with the tail's format version, PC records untouched, the PS4-only zone appended.
        expected = bytearray(ps4_records[b"For100_River"])
        struct.pack_into("<H", expected, conv.ZONE_RECORD_VERSION_OFFSET, 0x135)
        self.assertEqual(rebuilt[0][1], bytes(expected))
        self.assertEqual(rebuilt[1][1], pc_records[b"Zone_BB"])
        self.assertEqual(rebuilt[2][1], pc_records[b"Zone_C_empty"])
        self.assertEqual(struct.unpack_from("<H", rebuilt[3][1], conv.ZONE_RECORD_VERSION_OFFSET)[0], 0x135)
        zone2 = slot.find(conv.ZONE_LIST_NAME, conv.PLAYER_STATE_HASH_OFFSET) - conv.ZONE2_NAME_OFFSET
        pc_zone2 = pc_slot.find(conv.ZONE_LIST_NAME, conv.PLAYER_STATE_HASH_OFFSET) - conv.ZONE2_NAME_OFFSET
        self.assertEqual(struct.unpack_from("<I", slot, zone2)[0], 4)
        self.assertEqual(slot[zone2 + 4:zone2 + 0x88], pc_slot[pc_zone2 + 4:pc_zone2 + 0x88])
        self.assertEqual(slot[conv.resource_section_start(slot):conv.resource_section_start(slot) + 8], b"resBBBBB")

    def test_ps4_zone_part_swaps_payload_or_trailer(self):
        ps4 = make_save(PS4_BUILD, bytes(8), self.ps4_profile, {0: make_checkpoint(100, PS4_BUILD, 22, tag=b"A")})
        pc_slot = make_checkpoint(300, PC_BUILD, 1, tag=b"B")
        template = make_save(PC_BUILD, struct.pack("<II", 3, 1), self.pc_profile, {4: pc_slot})
        ps4_river = dict(conv.zone_records(ps4[conv.slot_range(0)]))[b"For100_River"]
        pc_river = dict(conv.zone_records(pc_slot))[b"For100_River"]
        for part, expected in (
            ("payload", pc_river[:0x24] + ps4_river[0x24:-130] + pc_river[-130:]),
            ("trailer", pc_river[:-130] + ps4_river[-130:]),
        ):
            slot = conv.convert(ps4, template, transplant=("tail",), ps4_zones=("For*",), ps4_zone_part=part, template_slot=4)[conv.slot_range(0)]
            records = dict(conv.zone_records(slot))
            self.assertEqual(records[b"For100_River"], expected)
            self.assertEqual(records[b"Zone_C_empty"], dict(conv.zone_records(pc_slot))[b"Zone_C_empty"])

    def test_repack_resource_list_widens_ps4_entries(self):
        record = bytearray(conv.SLOT_SIZE)
        zone2 = conv.PLAYER_STATE_HASH_OFFSET + 0x100
        struct.pack_into("<I", record, zone2, 1)
        zone_struct = zone2 + 4
        record[zone_struct + 0x84:zone_struct + 0x84 + 12] = conv.ZONE_LIST_NAME
        struct.pack_into("<I", record, zone_struct + conv.ZONE_STRUCT_BLOB_SIZE_OFFSET, 8)
        record[zone_struct + conv.ZONE_STRUCT_BYTES:zone_struct + conv.ZONE_STRUCT_BYTES + 8] = b"blobdata"
        start = zone_struct + conv.ZONE_STRUCT_BYTES + 8
        self.assertEqual(conv.resource_list_offset(record), start)
        struct.pack_into("<I", record, start, 2)
        for index, name in enumerate((b"Son00", b"Boat00")):
            entry = start + 4 + index * conv.PS4_RESOURCE_ENTRY_BYTES
            record[entry:entry + 0x40] = bytes([index + 1]) * 0x40
            struct.pack_into("<IHB", record, entry + 0x40, 0xFFFFFFFF, index, 3)
            record[entry + 0x47:entry + 0x47 + len(name)] = name
        rest = start + 4 + 2 * conv.PS4_RESOURCE_ENTRY_BYTES
        record[rest:rest + 8] = b"rest" + bytes([0xFF]) * 4
        self.assertEqual(conv.resource_entry_size(record), conv.PS4_RESOURCE_ENTRY_BYTES)

        out = conv.repack_resource_list(record)
        self.assertEqual(len(out), conv.SLOT_SIZE)
        self.assertEqual(out[:start + 4], bytes(record[:start + 4]))
        self.assertEqual(conv.resource_entry_size(out), conv.PC_RESOURCE_ENTRY_BYTES)
        second = start + 4 + conv.PC_RESOURCE_ENTRY_BYTES
        self.assertEqual(out[second:second + 0x40], bytes([2]) * 0x40)
        self.assertEqual(struct.unpack_from("<III", out, second + 0x40), (0xFFFFFFFF, 1, 3))
        self.assertEqual(out[second + 0x4C:second + 0x52], b"Boat00")
        after = start + 4 + 2 * conv.PC_RESOURCE_ENTRY_BYTES
        self.assertEqual(out[after:after + 8], b"rest" + bytes([0xFF]) * 4)
        self.assertEqual(conv.repack_resource_list(out), out)

    def test_zone_record_repairs(self):
        ps4 = make_save(PS4_BUILD, bytes(8), self.ps4_profile, {0: make_checkpoint(100, PS4_BUILD, 22, tag=b"A")})
        pc_slot = make_checkpoint(300, PC_BUILD, 1, tag=b"B")
        template = make_save(PC_BUILD, struct.pack("<II", 3, 1), self.pc_profile, {4: pc_slot})
        common = dict(template_slot=4, patch_build=False, patch_marker=False, patch_player_hash=False, patch_zone_version=False)
        original = dict(conv.zone_records(ps4[conv.slot_range(0)]))
        fixed = dict(conv.zone_records(conv.convert(ps4, template, fix_empty_zone_records=True, **common)[conv.slot_range(0)]))
        self.assertEqual(fixed[b"Zone_C_empty"][34:38], bytes(4))
        self.assertEqual(fixed[b"Zone_C_empty"][38:], original[b"Zone_C_empty"][38:])
        self.assertEqual(fixed[b"For100_River"], original[b"For100_River"])
        pointers = dict(conv.zone_records(conv.convert(ps4, template, zone_pointers_from_template=True, **common)[conv.slot_range(0)]))
        river = pointers[b"For100_River"]
        self.assertEqual(river[:48], original[b"For100_River"][:48])
        self.assertEqual(river[48 + 46:48 + 62], b"B" * 16)
        self.assertEqual(river[48 + 70:48 + 86], b"B" * 16)
        self.assertEqual(river[48 + 62:48 + 70], b"A" * 8)
        # Zone_BA has no PC counterpart and stays untouched.
        self.assertEqual(pointers[b"Zone_BA"], original[b"Zone_BA"])
        trailers = dict(conv.zone_records(conv.convert(ps4, template, zone_trailers_from_template=True, **common)[conv.slot_range(0)]))
        self.assertEqual(trailers[b"For100_River"][:48], original[b"For100_River"][:48])
        self.assertEqual(trailers[b"For100_River"][48:], dict(conv.zone_records(pc_slot))[b"For100_River"][48:])
        # Stateless records have no PC trailer to copy wholesale, so only their pointers are replaced.
        empty = trailers[b"Zone_C_empty"]
        self.assertEqual(empty[38 + 46:38 + 62], b"b" * 16)
        self.assertEqual(empty[38 + 62:38 + 70], b"a" * 8)

    def test_keep_player_hash(self):
        out = conv.convert(self.ps4, self.template, patch_player_hash=False)
        start = conv.SLOT_AREA_OFFSET
        self.assertEqual(struct.unpack_from("<I", out, start + conv.PLAYER_STATE_HASH_OFFSET)[0], PS4_PLAYER_HASH)
        self.assertEqual(out[start + conv.PLATFORM_MARKER_OFFSET], 1)

    def test_patch_flags_are_independent(self):
        out = conv.convert(self.ps4, self.template, patch_build=True, patch_marker=False)
        start = conv.SLOT_AREA_OFFSET
        self.assertEqual(struct.unpack_from("<I", out, start + conv.RECORD_BUILD_OFFSET)[0], PC_BUILD)
        self.assertEqual(out[start + conv.PLATFORM_MARKER_OFFSET], 22)

    def test_keep_profile_uses_ps4_header_and_profile(self):
        out = conv.convert(self.ps4, self.template, keep_profile=True)
        self.assertEqual(out[:conv.SLOT_AREA_OFFSET], self.ps4[:conv.SLOT_AREA_OFFSET])
        start = conv.SLOT_AREA_OFFSET
        self.assertEqual(struct.unpack_from("<I", out, start + conv.RECORD_BUILD_OFFSET)[0], PC_BUILD)
        self.assertEqual(out[start + conv.PLATFORM_MARKER_OFFSET], 1)

    def test_rejects_wrong_size(self):
        with self.assertRaises(ValueError):
            conv.convert(self.ps4[:-1], self.template)
        with self.assertRaises(ValueError):
            conv.convert(self.ps4, self.template + b"\0")

    def test_rejects_template_without_pc_checkpoint(self):
        empty_template = make_save(PC_BUILD, struct.pack("<II", 3, 1), self.pc_profile, {})
        with self.assertRaises(ValueError):
            conv.convert(self.ps4, empty_template)

    def test_used_slots_and_newest(self):
        self.assertEqual(conv.used_slots(self.ps4), [0, 3])
        self.assertEqual(conv.newest_slot(self.ps4), 3)
        self.assertIsNone(conv.newest_slot(bytearray(conv.FILE_SIZE)))


if __name__ == "__main__":
    unittest.main()
