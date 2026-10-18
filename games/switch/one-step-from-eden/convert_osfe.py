"""Convert a One Step From Eden Switch save to the PC (Steam) format.

Both platforms store a single SaveData file holding a .NET BinaryFormatter Hashtable.
Switch writes it as is; PC encrypts it: AES-256-CBC over the Base64 text of the
serialized bytes, with the key and IV derived by PBKDF2-HMAC-SHA1 (1000 iterations).

The Switch build's SpellData lacks the PC-only permDamage field. BinaryFormatter
tolerates missing fields and leaves them at 0, so the payload is copied unchanged.

Usage: python convert_osfe.py <switch SaveData> <output SaveData>
"""

import base64
import hashlib
import sys
from pathlib import Path

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

# From the game's SaveDataCtrl (the password is stored as two XORed byte arrays).
PASSWORD = b"UP3sRC7ufUYlFkYXZ0pG"
SALT = b"x16ne9gneksflxro"
ITERATIONS = 1000
# BinaryFormatter SerializedStreamHeader: record 0, root id 1, header id -1, version 1.0.
NRBF_HEADER = bytes.fromhex("0001000000ffffffff0100000000000000")


def cipher() -> AES:
    derived = hashlib.pbkdf2_hmac("sha1", PASSWORD, SALT, ITERATIONS, 48)
    return AES.new(derived[:32], AES.MODE_CBC, derived[32:])


def encrypt(payload: bytes) -> bytes:
    return cipher().encrypt(pad(base64.b64encode(payload), AES.block_size))


def decrypt(data: bytes) -> bytes:
    return base64.b64decode(unpad(cipher().decrypt(data), AES.block_size))


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    switch_path, output_path = map(Path, sys.argv[1:])
    payload = switch_path.read_bytes()
    if not payload.startswith(NRBF_HEADER):
        sys.exit(f"{switch_path}: not an unencrypted Switch save")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(encrypt(payload))
    print(f"{output_path}: converted")


if __name__ == "__main__":
    main()
