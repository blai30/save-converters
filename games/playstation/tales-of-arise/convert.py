"""Converts Tales of Arise PS5 save slots to the PC format.

Usage:
    python convert.py convert <ps5 SaveData_NNN.sav> <output .sav>
    python convert.py roundtrip <.sav> [...]
"""
import struct
import sys

import arise_save

HEADER_SLOT_PC = 10
HEADER_SLOT_PS5 = 6


def replace_payload(file_data, payload):
    converted = bytearray(file_data)
    converted[arise_save.PAYLOAD_OFFSET:arise_save.PAYLOAD_OFFSET + len(payload)] = payload
    return bytes(converted)


def roundtrip(path):
    original = open(path, 'rb').read()
    plaintext, layout = arise_save.decrypt_payload(original)
    payload = arise_save.encrypt_payload(plaintext, layout, plaintext[0x1c])
    return replace_payload(original, payload) == original


def ps5_to_pc(file_data):
    plaintext, layout = arise_save.decrypt_payload(file_data)
    assert plaintext[0x1c] == arise_save.FORMAT_PS5, 'not a PS5 save'
    # Move the section table pointer to the slot the PC loader reads.
    slots = list(struct.unpack_from('<16I', plaintext, 0x20))
    slots[HEADER_SLOT_PC], slots[HEADER_SLOT_PS5] = slots[HEADER_SLOT_PS5], slots[HEADER_SLOT_PC]
    struct.pack_into('<16I', plaintext, 0x20, *slots)
    payload = arise_save.encrypt_payload(plaintext, layout, arise_save.FORMAT_PC)
    return replace_payload(file_data, payload), plaintext


def main():
    command = sys.argv[1]
    if command == 'roundtrip':
        for path in sys.argv[2:]:
            print(path, 'identical' if roundtrip(path) else 'MISMATCH')
    elif command == 'convert':
        source, target = sys.argv[2], sys.argv[3]
        converted, expected = ps5_to_pc(open(source, 'rb').read())
        check, _ = arise_save.decrypt_payload(converted)
        assert check[0x1c] == arise_save.FORMAT_PC and check[0x20:] == expected[0x20:], 'verification failed'
        open(target, 'wb').write(converted)
        print(source, '->', target, 'verified')


if __name__ == '__main__':
    main()
