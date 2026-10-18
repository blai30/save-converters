"""Convert Devil May Cry 5 console saves (decrypted PS4, or PS5 Special Edition) to PC.

Saves are RE Engine DSSS v2 containers:

    u32 "DSSS", u32 version 2, u32 flags, u32 Blowfish option 3
    8-byte check block ("DSSSDSSS" encrypted)
    PC only (flags & 2): 8-byte encrypted Steam account id, so the header is 0x20 bytes
    body: serialized class tree
    u32 MurmurHash3 (seed 0xFFFFFFFF) of everything before it

Encryption is Blowfish CBC with a zero IV and 32-bit little-endian words. PS4 saves
come out of the console already decrypted; PS5 saves use their own key. Body values are
aligned to absolute file offsets, so moving the body from 0x18 to 0x20 means walking
the whole class tree and recomputing every alignment pad (format as in
kvasszn/ree-save-editor).

Usage: python convert_dmc5.py <console dataNNN.bin> --account-id <steam id> --out <pc dataNNN.bin>

The account id is the number in your Steam userdata path
(Steam\\userdata\\<account id>\\601150\\remote\\win64_save).
"""

import argparse
import struct
from pathlib import Path

from Crypto.Cipher import Blowfish

PC_KEY = b"dK7w3CX1y60OJaB4mr2LqsU9tnV58EiG"
PS5_KEY = b"p4cRj0xD9zN7SeMQgH531ufW6k8Tv2Yb"
MAGIC = b"DSSS"
CHECK = b"DSSSDSSS"
BLOWFISH = 0x1
HAS_ACCOUNT_ID = 0x2
CONSOLE_BODY_OFFSET = 0x18
PC_BODY_OFFSET = 0x20
ARRAY, CLASS, STRING = -1, 0x11, 0xF


def swap32(data):
    return b"".join(data[i : i + 4][::-1] for i in range(0, len(data), 4))


def decrypt(data, key):
    aligned = len(data) - len(data) % 8
    cipher = Blowfish.new(key, Blowfish.MODE_CBC, iv=bytes(8))
    return swap32(cipher.decrypt(swap32(data[:aligned]))) + data[aligned:]


def encrypt(data, key):
    aligned = len(data) - len(data) % 8
    cipher = Blowfish.new(key, Blowfish.MODE_CBC, iv=bytes(8))
    return swap32(cipher.encrypt(swap32(data[:aligned]))) + data[aligned:]


def murmur3(data, seed=0xFFFFFFFF):
    c1, c2, mask = 0xCC9E2D51, 0x1B873593, 0xFFFFFFFF

    def rotl(value, shift):
        return ((value << shift) | (value >> (32 - shift))) & mask

    hash_value = seed
    blocks = len(data) // 4
    for (block,) in struct.iter_unpack("<I", data[: blocks * 4]):
        block = rotl(block * c1 & mask, 15) * c2 & mask
        hash_value = (rotl(hash_value ^ block, 13) * 5 + 0xE6546B64) & mask
    tail = data[blocks * 4 :]
    if tail:
        block = int.from_bytes(tail, "little")
        hash_value ^= rotl(block * c1 & mask, 15) * c2 & mask
    hash_value ^= len(data) & mask
    for shift, multiplier in ((16, 0x85EBCA6B), (13, 0xC2B2AE35)):
        hash_value = (hash_value ^ (hash_value >> shift)) * multiplier & mask
    return hash_value ^ (hash_value >> 16)


def read_body(data, key):
    """Returns (body offset, account id or None, plaintext body)."""
    magic, version, flags, blowfish_option = struct.unpack_from("<4sIII", data, 0)
    if magic != MAGIC or version != 2 or blowfish_option != 3:
        raise ValueError(f"not a DSSS v2 save ({magic!r}, version {version}, option {blowfish_option})")
    plain = data[0x10:0x18] == CHECK
    if not plain and decrypt(data[0x10:0x18], key) != CHECK:
        raise ValueError("check block does not decrypt with this platform's key")
    offset, account_id = CONSOLE_BODY_OFFSET, None
    if flags & HAS_ACCOUNT_ID:
        account_id = struct.unpack("<Q", decrypt(data[0x18:0x20], key))[0]
        offset = PC_BODY_OFFSET
    body = data[offset:-4]
    return offset, account_id, body if plain else decrypt(body, key)


def build_pc(body, account_id):
    header = struct.pack("<4sIII", MAGIC, 2, BLOWFISH | HAS_ACCOUNT_ID, 3)
    header += encrypt(CHECK, PC_KEY) + encrypt(struct.pack("<Q", account_id), PC_KEY)
    data = header + encrypt(body, PC_KEY)
    return data + struct.pack("<I", murmur3(data))


class Repadder:
    """Walks a body at its file offset with the game's align-by-size rule and re-emits it
    as if it started at target_base, recomputing every alignment pad."""

    def __init__(self, body, base, target_base):
        self.body, self.base, self.pos = body, base, base
        self.out = bytearray()
        self.target_base = target_base

    def skip(self, size):
        start = self.pos - self.base
        self.out += self.body[start : start + size]
        self.pos += size

    def peek_u32(self):
        return struct.unpack_from("<I", self.body, self.pos - self.base)[0]

    def u32(self):
        value = self.peek_u32()
        self.skip(4)
        return value

    def i32(self):
        return struct.unpack("<i", struct.pack("<I", self.u32()))[0]

    def align(self, size):
        self.pos = (self.pos + size - 1) & ~(size - 1)
        target = self.target_base + len(self.out)
        self.out += bytes(((target + size - 1) & ~(size - 1)) - target)

    def sized(self, field_type, size):
        if field_type == ARRAY:
            return self.array()
        if size != 1:
            self.align(size)
        self.skip(size)

    def value(self, field_type):
        if field_type == ARRAY:
            return self.array()
        if field_type == CLASS:
            return self.klass()
        self.align(4)
        size = self.u32()
        if field_type == STRING:
            self.skip(size * 2)
        else:
            self.sized(field_type, size)

    def array(self):
        self.align(4)
        member_type, member_size, length, array_type = self.i32(), self.u32(), self.u32(), self.i32()
        if array_type == 1 and self.peek_u32() == 0xFFEEFFEE:
            self.skip(4 + length * 4)
        for _ in range(length):
            if array_type == 1:
                self.klass()
            elif member_type == STRING:
                self.align(4)
                self.skip(self.u32() * 2)
            else:
                self.sized(member_type, member_size)

    def klass(self):
        count = self.u32()
        self.u32()
        for _ in range(count):
            self.u32()
            field_type = self.i32()
            if field_type not in range(-1, 0x12) or field_type == 0:
                raise ValueError(f"bad field type {field_type} at {self.pos:#x}")
            self.value(field_type)
            self.align(4)

    def run(self):
        end = self.base + len(self.body) - 3
        while self.pos < end:
            self.u32()
            self.klass()
        if self.pos - self.base != len(self.body):
            raise ValueError(f"consumed {self.pos - self.base} of {len(self.body)} bytes")
        return bytes(self.out)


def repad(body, base, target_base):
    return Repadder(body, base, target_base).run()


def convert(data, account_id):
    # Console saves decrypted by external tools don't always keep a valid file hash, so it isn't checked.
    offset, _, body = read_body(data, PS5_KEY)
    if offset != CONSOLE_BODY_OFFSET:
        raise ValueError("this is already a PC save")
    return build_pc(repad(body, CONSOLE_BODY_OFFSET, PC_BODY_OFFSET), account_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="decrypted PS4 or PS5 dataNNN.bin")
    parser.add_argument("--account-id", type=int, required=True, help="Steam account id (userdata folder name)")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = convert(args.source.read_bytes(), args.account_id)
    offset, account_id, body = read_body(result, PC_KEY)
    if murmur3(result[:-4]) != struct.unpack_from("<I", result, len(result) - 4)[0] or account_id != args.account_id:
        raise AssertionError("verification failed")
    repad(body, offset, offset)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(result)
    print(f"{args.source} -> {args.out} verified")


if __name__ == "__main__":
    main()
