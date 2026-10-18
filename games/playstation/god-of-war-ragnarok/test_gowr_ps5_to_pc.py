import struct
import unittest

import gowr_ps5_to_pc as converter


def fake_record(fill):
    """Record with a 4-byte payload: header, 0x34-byte name, hash, flags, sizes, payload, trailer."""
    record = struct.pack("<IIII", 0x100000, 17, 0xFFFFFFFF, 0) + b"\x11" * 16 + struct.pack("<I", 4) + bytes(0x28)
    record += b"Mid_Zone\0".ljust(0x34, b"\0") + bytes(8) + b"\x00\x01\x01\x03" + bytes(4) + struct.pack("<HH", 4, 0)
    record += bytes([fill] * 4) + bytes(0x3C)
    assert len(record) == converter.RECORD_FIXED + 4
    return record


def fake_checkpoint(generation=2, fill=1, player_hash=converter.PS5_PLAYER_HASH, stale=False):
    """Minimal file: headers, three streams at the generation's offsets, one record, resources."""
    stream1_offset, tail_offset = converter.NEW_LAYOUT if generation >= 2 else converter.OLD_LAYOUT
    state = struct.pack("<I", 0x031E61C0) + bytes([fill] * 0x20)
    stream1 = bytes([fill + 0x10] * 0x30)
    player = struct.pack("<I", player_hash) + bytes([fill + 0x20] * 0x40)
    records = struct.pack("<II", 1, 3) + bytes(converter.TAIL_FIRST_RECORD - 8) + fake_record(fill) + bytes(converter.RESOURCES_GAP)
    resources = converter.END_MARKER + bytes(3) + b"FreyaFalcon00\0" + bytes([fill + 0x30] * 8) + converter.END_MARKER
    tail = player + records + resources
    data = bytearray(tail_offset)
    struct.pack_into("<QIIII", data, 0, 1696655631, 8, 5320768, 0x2B8, 0x8C0400)
    struct.pack_into("<I", data, converter.TOTAL_SIZE_OFFSET, len(state) + len(stream1) + len(tail))
    struct.pack_into("<QII", data, converter.RECORD_OFFSET, 1696655631, 35, 5320768)
    data[0x50:0x58] = b"Midgard\0"
    struct.pack_into("<III", data, converter.STREAM_SIZES_OFFSET, len(tail), len(state), len(stream1))
    for offset, (ps5_value, _) in converter.SECTION_VERSIONS.items():
        data[offset] = ps5_value
    data[converter.GENERATION_OFFSET] = generation
    data[converter.STATE_OFFSET : converter.STATE_OFFSET + len(state)] = state
    data[stream1_offset : stream1_offset + len(stream1)] = stream1
    if stale:
        data[0x44440:0x44450] = b"\xaa" * 16
    return bytes(data) + tail


class LayoutTests(unittest.TestCase):
    def test_new_generation_regions(self):
        data = fake_checkpoint()
        converter.validate(data, "fake")
        regions = converter.layout(data)
        self.assertEqual(regions["state"].start, 0x440)
        self.assertEqual(regions["stream1"].start, 0x80440)
        self.assertEqual(regions["player"].start, 0xC0440)
        self.assertEqual(regions["records"].start, 0xC0440 + 0x44)
        self.assertEqual(len(converter.record_offsets(data, regions["records"])), 1)
        self.assertEqual(regions["resources"].stop, len(data))
        self.assertEqual(data[regions["resources"].start : regions["resources"].start + 4], converter.END_MARKER)

    def test_old_generation_regions(self):
        data = fake_checkpoint(generation=0)
        converter.validate(data, "fake")
        regions = converter.layout(data)
        self.assertEqual(regions["stream1"].start, 0x2F440)
        self.assertEqual(regions["player"].start, 0x44440)


class ComposeTests(unittest.TestCase):
    def test_compose_clears_stale_padding_and_keeps_streams(self):
        source = fake_checkpoint(stale=True)
        result = converter.compose(source)
        converter.validate(result, "result")
        self.assertEqual(result[0x44440:0x44450], bytes(16))
        for name, region in converter.layout(source).items():
            self.assertEqual(result[converter.layout(result)[name]], source[region], name)

    def test_old_generation_is_moved_to_new_offsets(self):
        source = fake_checkpoint(generation=0)
        result = converter.compose(source)
        converter.validate(result, "result")
        regions = converter.layout(result)
        self.assertEqual(result[converter.GENERATION_OFFSET], 2)
        self.assertEqual(regions["stream1"].start, 0x80440)
        self.assertEqual(result[regions["stream1"]], source[converter.layout(source)["stream1"]])
        self.assertEqual(result[regions["records"]], source[converter.layout(source)["records"]])

    def test_swap_resources_updates_sizes(self):
        base = fake_checkpoint(fill=1)
        donor = fake_checkpoint(fill=2)
        result = converter.compose(base, donor, ("resources",))
        converter.validate(result, "result")
        regions = converter.layout(result)
        self.assertEqual(result[regions["resources"]], donor[converter.layout(donor)["resources"]])
        self.assertEqual(result[regions["records"]], base[converter.layout(base)["records"]])
        self.assertEqual(result[regions["state"]], base[converter.layout(base)["state"]])

    def test_record_flags_and_no_records(self):
        source = fake_checkpoint()
        flagged = converter.convert(source, build=False, record_flags=True)
        offset = converter.record_offsets(flagged, converter.layout(flagged)["records"])[0]
        self.assertEqual(flagged[offset + converter.RECORD_FLAGS_OFFSET : offset + converter.RECORD_FLAGS_OFFSET + 4], converter.PC_RECORD_FLAGS)
        emptied = converter.convert(source, build=False, no_records=True)
        converter.validate(emptied, "emptied")
        regions = converter.layout(emptied)
        self.assertEqual(struct.unpack_from("<I", emptied, regions["records"].start)[0], 0)
        self.assertEqual(emptied[regions["player"]], source[converter.layout(source)["player"]])
        self.assertEqual(emptied[regions["resources"]], source[converter.layout(source)["resources"]])


class PatchTests(unittest.TestCase):
    def test_build_hash_tag_versions(self):
        source = fake_checkpoint()
        result = converter.convert(source, versions="pc", tag=True, player_hash=True)
        self.assertEqual(struct.unpack_from("<I", result, 0xC)[0], converter.PC_BUILD)
        self.assertEqual(struct.unpack_from("<I", result, 0x4C)[0], converter.PC_BUILD)
        self.assertEqual(struct.unpack_from("<I", result, 0x440)[0], converter.PC_STATE_TAG)
        self.assertEqual(struct.unpack_from("<I", result, 0xC0440)[0], converter.PC_PLAYER_HASH)
        for offset, (_, pc_value) in converter.SECTION_VERSIONS.items():
            self.assertEqual(result[offset], pc_value)
        untouched = converter.convert(source, build=False)
        self.assertEqual(untouched, source)
        poked = converter.convert(source, build=False, pokes=[(0x1C7, 2)])
        self.assertEqual(poked[0x1C7], 2)
        self.assertEqual(poked[:0x1C7] + poked[0x1C8:], source[:0x1C7] + source[0x1C8:])

    def test_validate_rejects_other_formats(self):
        with self.assertRaises(ValueError):
            converter.validate(bytes(0x50000), "x")

    def test_stale_bytes_after_tail_are_dropped(self):
        source = fake_checkpoint()
        stale = source + b"\xff" * 95
        with self.assertRaises(ValueError):
            converter.validate(stale, "stale")
        converter.validate(stale, "stale", allow_stale_tail=True)
        result = converter.convert(stale, build=False)
        converter.validate(result, "result")
        self.assertEqual(result, source)


if __name__ == "__main__":
    unittest.main()
