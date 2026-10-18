import hashlib
import struct
from Crypto.Cipher import AES

PAYLOAD_OFFSET = 0x32058
PAYLOAD_MAGIC = 0x10051630
FORMAT_PC = 0x00
FORMAT_PS5 = 0x03
PC_KEYS = (
    b'fgEQ5GbxFXp-mD6tHTZVmiWwgK8PwgK5MfcnGHurirrJY9xVxkd8i-Vy3LD6Rhx\0',
    b'NjFnSJsNQpiMrnQegcBr5AgrZAGA5gRMkCADNMZR3izEWVR3ZicicXZNsFyKUmr\0',
)
PS5_KEYS = (
    b'9QLJPHXZUnG2LJAHXC6wcfHYkBVzSHyB-53t9cPfnriPcJ-kKcrRaCb6BriUScd\0',
    b'JEExTp59i6QP_pg2GK7ZjcW32bJ-XmRWCXYAXC4YcGMRuRRjBh_D7Ts8MPkZ4G9\0',
)
# Format byte -> (initial key strings, payload offset of the section table pointer).
PLATFORMS = {
    FORMAT_PC: (PC_KEYS, 0x48),
    FORMAT_PS5: (PS5_KEYS, 0x38),
}


class CipherState:
    def __init__(self, format_byte, keys):
        self.format_byte = format_byte
        self.key = bytearray(keys[0])
        self.chain = bytearray(keys[1])

    def mix_chain(self, piece):
        # Each platform XORs with the previous ciphertext piece using its own byte order.
        length = len(piece)
        for i in range(length):
            if self.format_byte == FORMAT_PC:
                piece[i] ^= self.chain[(i + 2) % length]
            elif self.format_byte == FORMAT_PS5:
                piece[i] ^= self.chain[min(i ^ 1, length - 1)]

    def decrypt(self, payload, offset, length):
        aes = AES.new(bytes(self.key[:32]), AES.MODE_ECB)
        for piece_start in range(0, length, 0x40):
            piece_length = min(0x40, length - piece_start)
            start = offset + piece_start
            ciphertext = bytes(payload[start:start + piece_length])
            plaintext = bytearray(aes.decrypt(ciphertext))
            self.mix_chain(plaintext)
            payload[start:start + piece_length] = plaintext
            self.chain[:piece_length] = ciphertext
        self.update_key(hashlib.sha1(payload[offset:offset + length]).digest())

    def encrypt(self, payload, offset, length):
        digest = hashlib.sha1(payload[offset:offset + length]).digest()
        aes = AES.new(bytes(self.key[:32]), AES.MODE_ECB)
        for piece_start in range(0, length, 0x40):
            piece_length = min(0x40, length - piece_start)
            start = offset + piece_start
            plaintext = bytearray(payload[start:start + piece_length])
            self.mix_chain(plaintext)
            ciphertext = aes.encrypt(bytes(plaintext))
            payload[start:start + piece_length] = ciphertext
            self.chain[:piece_length] = ciphertext
        self.update_key(digest)

    def update_key(self, digest):
        old = bytes(self.key)
        for i in range(0x40):
            key_byte, hash_byte = old[i], digest[i % 20]
            if key_byte != hash_byte:
                self.key[i] = key_byte ^ hash_byte
            else:
                self.key[i] = key_byte if key_byte else 0x14
        self.key[0x3f] = 0


def regions(payload, table_field):
    """Yields (offset, length) for each encrypted region in the order the game reads them.

    Must be called on a payload that is being decrypted progressively, since offsets live in plaintext.
    """
    yield 0x20, 0x40
    table = struct.unpack_from('<I', payload, table_field)[0]
    yield table, 0x40
    version, count, entry = struct.unpack_from('<III', payload, table)
    for _ in range(count):
        yield entry, 0x10
        size, next_entry = struct.unpack_from('<II', payload, entry + 8)
        yield entry + 0x10, size
        entry = next_entry


def decrypt_payload(file_data):
    payload = bytearray(file_data[PAYLOAD_OFFSET:])
    magic, size = struct.unpack_from('<II', payload, 0)
    assert magic == PAYLOAD_MAGIC, hex(magic)
    payload = payload[:size]
    assert hashlib.sha1(payload[0x20:]).digest() == bytes(payload[8:0x1c]), 'payload SHA-1 mismatch'
    format_byte = payload[0x1c]
    keys, table_field = PLATFORMS[format_byte]
    state = CipherState(format_byte, keys)
    layout = []
    for offset, length in regions(payload, table_field):
        state.decrypt(payload, offset, length)
        layout.append((offset, length))
    return payload, layout


def encrypt_payload(plaintext, layout, format_byte):
    payload = bytearray(plaintext)
    payload[0x1c] = format_byte
    state = CipherState(format_byte, PLATFORMS[format_byte][0])
    for offset, length in layout:
        state.encrypt(payload, offset, length)
    payload[8:0x1c] = hashlib.sha1(payload[0x20:]).digest()
    return payload
