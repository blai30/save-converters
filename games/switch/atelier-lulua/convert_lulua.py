"""Convert Atelier Lulua Switch saves to the PC (Steam) format.

A data.dat file is a 0x100-byte plaintext header followed by an encoded body. Both
platforms share the same plaintext and the same outer scrambling, but differ in:

- Header timestamp at 0x30: Unix seconds on Switch, Windows FILETIME (UTC) on PC.
- Body compression: Switch uses Gust's LZ format ("Glaze"), PC uses a byte frequency
  table followed by Elias gamma coding. The PC loader only understands the latter.

Usage: python convert_lulua.py <switch save dir> <output dir>
"""

import shutil
import struct
import sys
from collections import Counter
from pathlib import Path

HEADER_SIZE = 0x100
TIMESTAMP_OFFSET = 0x30
# Seconds between 1601-01-01 and 1970-01-01.
EPOCH_DELTA_SECONDS = 11644473600
FILETIME_TICKS_PER_SECOND = 10_000_000
# Anything above this is already a FILETIME (year ~2001 in ticks).
FILETIME_THRESHOLD = 1 << 56

# Scrambler constants from EncDec in Atelier_Lulua.exe.
RANDOM_CONSTANT = 0x3B9A73C9
RANDOM_INCREMENT = 0x2F09
BIT_SCRAMBLE_SEED = 0x3745D
BIT_SCRAMBLE_SIZE = 0x800
FENCED_SEED = 0xD2E3
FENCE_MODULO = 0xE9E
FENCE = 0x74F
ROTATING_SEEDS = (0xA4D5, 0xB599, 0x37DF)
ROTATING_LENGTHS = (7, 13, 23)
FOOTER_SIZE = 0x10
END_MARKER = 0xFF


def read_be32(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


class Random:
    def __init__(self, multiplier: int, state: int) -> None:
        self.multiplier = multiplier & 0xFFFFFFFF
        self.state = state & 0xFFFFFFFF

    def next(self) -> int:
        self.state = (self.multiplier * self.state + RANDOM_INCREMENT) & 0xFFFFFFFF
        return self.state

    def next_u15(self) -> int:
        return (self.next() >> 16) & 0x7FFF


def bit_scramble(buffer: bytearray, start: int, size: int) -> None:
    # Swaps disjoint bit pairs, so the same call both scrambles and descrambles.
    random = Random(RANDOM_CONSTANT, BIT_SCRAMBLE_SEED)
    while size:
        slice_size = min(BIT_SCRAMBLE_SIZE, size)
        positions = list(range(slice_size * 8))
        order = [positions.pop(random.next_u15() % len(positions)) for _ in range(slice_size * 8)]
        for i in range(0, len(order) - 1, 2):
            byte0, bit0 = start + (order[i] >> 3), order[i] & 7
            byte1, bit1 = start + (order[i + 1] >> 3), order[i + 1] & 7
            value0 = (buffer[byte0] >> bit0) & 1
            value1 = (buffer[byte1] >> bit1) & 1
            buffer[byte0] = (buffer[byte0] & ~(1 << bit0)) | (value1 << bit0)
            buffer[byte1] = (buffer[byte1] & ~(1 << bit1)) | (value0 << bit1)
        start += slice_size
        size -= slice_size


def fenced_scramble(buffer: bytearray, size: int, descramble: bool) -> None:
    random = Random(RANDOM_CONSTANT, FENCED_SEED)
    for i in range(0, size, 2):
        key = random.next_u15()
        word = (buffer[i] << 8) | buffer[i + 1]
        if descramble:
            if key % FENCE_MODULO >= FENCE:
                word ^= random.next_u15()
            word = (word - key) & 0xFFFF
        else:
            word = (word + key) & 0xFFFF
            if key % FENCE_MODULO >= FENCE:
                word ^= random.next_u15()
        buffer[i], buffer[i + 1] = word >> 8, word & 0xFF


def rotating_scramble(buffer: bytearray, size: int, seed: int) -> None:
    random = Random(RANDOM_CONSTANT + seed, 0)
    seeds = list(ROTATING_SEEDS)
    index = extra_length = position = 0
    while position < size:
        random.state = seeds[index]
        for _ in range(ROTATING_LENGTHS[index] + extra_length):
            if position >= size:
                return
            buffer[position] ^= (random.next() >> 16) & 0xFF
            position += 1
        seeds[index] = random.state
        index += 1
        if index == len(seeds):
            index = 0
            extra_length += 1


def word_checksums(buffer: bytes, size: int) -> tuple[int, int]:
    words = struct.unpack_from(f">{size // 4}I", buffer)
    xor = 0
    for word in words:
        xor ^= word
    return sum(words) & 0xFFFFFFFF, xor


def descramble(body: bytes) -> tuple[bytes, int]:
    buffer = bytearray(body)
    size = len(buffer.rstrip(b"\0"))
    aligned = (size + 0xF) & ~0xF
    tail = min(aligned, BIT_SCRAMBLE_SIZE)
    bit_scramble(buffer, aligned - tail, tail)
    fenced_scramble(buffer, aligned, descramble=True)
    payload_size = aligned - FOOTER_SIZE
    seed = read_be32(buffer, aligned - 4)
    rotating_scramble(buffer, payload_size, seed)
    checksums = word_checksums(buffer, payload_size)
    expected = (read_be32(buffer, aligned - 8), read_be32(buffer, aligned - 12))
    if buffer[payload_size] != END_MARKER or checksums != expected:
        raise ValueError("descramble checksum mismatch")
    return bytes(buffer[:payload_size]), seed


def scramble(payload: bytes, seed: int) -> bytes:
    payload_size = (len(payload) + 0xF) & ~0xF
    aligned = payload_size + FOOTER_SIZE
    buffer = bytearray(aligned)
    buffer[: len(payload)] = payload
    total, xor = word_checksums(buffer, payload_size)
    rotating_scramble(buffer, payload_size, seed)
    buffer[payload_size] = END_MARKER
    struct.pack_into(">III", buffer, aligned - 12, xor, total, seed)
    fenced_scramble(buffer, aligned, descramble=False)
    tail = min(aligned, BIT_SCRAMBLE_SIZE)
    bit_scramble(buffer, aligned - tail, tail)
    return bytes(buffer)


class BitReader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.position = 0

    def read(self, count: int = 1) -> int:
        value = 0
        for _ in range(count):
            byte = self.data[self.position >> 3]
            value = (value << 1) | ((byte >> (7 - (self.position & 7))) & 1)
            self.position += 1
        return value


def unglaze(source: bytes) -> bytes:
    # Switch compression, ported from VitaSmith's gust_enc (GPLv3).
    decoded_size = read_be32(source, 0)
    bitstream_size = read_be32(source, 4)
    code_count = read_be32(source, 8)
    reader = BitReader(source[12 : 8 + bitstream_size])
    codes = bytearray()
    while len(codes) < code_count:
        if reader.read():
            codes.append(1)
            continue
        length = 1
        while length < 8 and not (bit := reader.read()):
            length += 1
        codes.append(0 if length == 8 else (bit << length) | reader.read(length))
    offset = 8 + bitstream_size
    dictionary_size = read_be32(source, offset)
    dictionary = source[offset + 4 : offset + 4 + dictionary_size]
    lengths = source[offset + 8 + dictionary_size :]

    output = bytearray()
    code_index = dictionary_index = length_index = 0

    def copy_back(distance: int, count: int) -> None:
        for _ in range(count):
            output.append(output[-distance])

    while len(output) < decoded_size:
        code = codes[code_index]
        code_index += 1
        if code == 1:
            output.append(dictionary[dictionary_index])
            dictionary_index += 1
        elif code == 2:
            copy_back(codes[code_index], 1)
            code_index += 1
        elif code == 3:
            distance, length = codes[code_index], codes[code_index + 1]
            code_index += 2
            copy_back(distance + length, length + 1)
        elif code == 4:
            length = codes[code_index]
            code_index += 1
            copy_back(dictionary[dictionary_index] + length, length + 1)
            dictionary_index += 1
        elif code == 5:
            distance = (codes[code_index] << 8) | dictionary[dictionary_index]
            length = codes[code_index + 1]
            code_index += 2
            dictionary_index += 1
            copy_back(distance + length, length + 1)
        elif code in (6, 7):
            if code == 6:
                length = codes[code_index] + 8
                code_index += 1
            else:
                length = lengths[length_index] + 14
                length_index += 1
            output += dictionary[dictionary_index : dictionary_index + length]
            dictionary_index += length
        else:
            raise ValueError(f"invalid Glaze code {code}")
    return bytes(output[:decoded_size])


def gamma_decode(source: bytes) -> bytes:
    reader = BitReader(source[4:])
    output = bytearray()
    for _ in range(read_be32(source, 0)):
        zeros = 0
        while zeros < 8 and not reader.read():
            zeros += 1
        if zeros == 0:
            output.append(0)
        elif zeros == 8:
            output.append(0xFF)
        else:
            output.append(((1 << zeros) | reader.read(zeros)) - 1)
    return bytes(output)


def gamma_encode(data: bytes) -> bytes:
    bits = []
    for value in data:
        if value == 0xFF:
            bits.append("0" * 8)
        else:
            code = format(value + 1, "b")
            bits.append("0" * (len(code) - 1) + code)
    stream = "".join(bits)
    # The game pads the final byte with set bits.
    stream += "1" * (-len(stream) % 8)
    return struct.pack(">I", len(data)) + int(stream, 2).to_bytes(len(stream) // 8, "big")


def frequency_decode(data: bytes) -> bytes:
    count = data[0] or 0x100
    table = data[1 : 1 + count]
    length = read_be32(data, 1 + count)
    start = 5 + count
    return data[start : start + length].translate(table.ljust(0x100, b"\0"))


def frequency_encode(data: bytes) -> bytes:
    counts = Counter(data)
    # Matches the game: most frequent first, ties broken by higher byte value.
    table = bytes(sorted(counts, key=lambda byte: (-counts[byte], -byte)))
    ranks = bytearray(0x100)
    for rank, byte in enumerate(table):
        ranks[byte] = rank
    header = bytes([len(table) & 0xFF]) + table + struct.pack(">I", len(data))
    return header + data.translate(ranks)


def decode_body(body: bytes) -> tuple[bytes, int]:
    payload, seed = descramble(body)
    glaze_size = 12 + read_be32(payload, 4)
    if glaze_size < len(payload) and read_be32(payload, 8) <= 8 * len(payload):
        try:
            return unglaze(payload), seed
        except (IndexError, ValueError):
            pass
    return frequency_decode(gamma_decode(payload)), seed


def encode_body(plain: bytes, seed: int) -> bytes:
    return scramble(gamma_encode(frequency_encode(plain)), seed)


def convert_header(header: bytes) -> bytes:
    data = bytearray(header)
    (timestamp,) = struct.unpack_from("<Q", data, TIMESTAMP_OFFSET)
    if 0 < timestamp < FILETIME_THRESHOLD:
        filetime = (timestamp + EPOCH_DELTA_SECONDS) * FILETIME_TICKS_PER_SECOND
        struct.pack_into("<Q", data, TIMESTAMP_OFFSET, filetime)
    return bytes(data)


def convert_file(source: Path, destination: Path) -> None:
    data = source.read_bytes()
    plain, seed = decode_body(data[HEADER_SIZE:])
    body = encode_body(plain, seed)
    if decode_body(body)[0] != plain:
        raise ValueError(f"{source}: round trip verification failed")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(convert_header(data[:HEADER_SIZE]) + body)
    shutil.copystat(source, destination)


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    source_dir, output_dir = Path(sys.argv[1]), Path(sys.argv[2])
    for source in sorted(source_dir.glob("*/data.dat")):
        convert_file(source, output_dir / source.parent.name / "data.dat")
        print(f"{source.parent.name}: converted")


if __name__ == "__main__":
    main()
