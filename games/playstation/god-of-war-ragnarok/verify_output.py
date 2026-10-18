"""Verify a converted checkpoint: every stream, section, zone record blob and the resource list under the PC rules.

Usage: python verify_output.py FILE [FILE ...]
"""

import struct
import sys
from pathlib import Path

import gowr_ps5_to_pc as converter
from parse_player import walk_sections
from parse_state import S3_PC, parse_pairs, parse_s3, parse_s8, walk
from parse_stream1 import PC, parse_block_b, parse_section_d, sections
from records import find_entity_blob

RESOURCE_TRAILER = 0x194


def verify(data):
    converter.validate(data, "output")
    regions = converter.layout(data)
    versions = data[0x1C4:0x1E4]
    state_sections, cursor = walk(data, regions["state"].start, versions)
    by_name = {name: (offset, size) for name, offset, size in state_sections}
    stream1_sections, cursor1 = sections(data, regions["stream1"].start)
    _, cursor_player = walk_sections(data, regions["player"].start + 4, versions)
    block_b = parse_block_b(data, *stream1_sections[1], PC)
    checks = dict(
        state=cursor == regions["state"].stop,
        s3=parse_s3(data, *by_name["s3"], S3_PC, PC)[0],
        s6=parse_pairs(data, *by_name["s6_pairs"], 16, 16)[0],
        s8=parse_s8(data, *by_name["s8"], 24, 48)[0],
        stream1=cursor1 == regions["stream1"].stop,
        stream1_b=block_b["cursor"] == block_b["end"],
        stream1_d=parse_section_d(data, *stream1_sections[3], PC)["exact"],
        player=cursor_player == regions["player"].stop,
    )
    offsets = converter.record_offsets(data, regions["records"])
    failures = 0
    for offset in offsets:
        size = struct.unpack_from("<I", data, offset + 0x20)[0]
        if size and find_entity_blob(data[offset + 0x90 : offset + 0x90 + size], 0x18) is None:
            failures += 1
    checks["records"] = failures == 0
    last = offsets[-1]
    resources = last + 0x90 + struct.unpack_from("<I", data, last + 0x20)[0]
    count = struct.unpack_from("<I", data, resources)[0]
    checks["resources"] = len(data) - (resources + 4 + count * 0x148) == RESOURCE_TRAILER
    return checks


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for path in argv:
        data = Path(path).read_bytes()
        checks = verify(data)
        failed = [name for name, ok in checks.items() if not ok]
        print(f"{path}: {'OK' if not failed else 'FAIL ' + ','.join(failed)}  {converter.describe(data).split('time=')[1]}")


if __name__ == "__main__":
    main(sys.argv[1:])
