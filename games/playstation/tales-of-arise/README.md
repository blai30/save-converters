# Tales of Arise: PS5 to PC

Converts decrypted PS5 save slots (`SaveData_NNN.sav`) to the Steam format.

## Recipe

```sh
python convert.py convert SaveData_000.sav out/SaveData_000.sav
```

The converter decrypts the slot with the PS5 keys, moves the section table pointer to the slot the PC loader reads, re-encrypts with the PC keys, and verifies the result by decrypting it again.

Copy the output into `%LOCALAPPDATA%\BANDAI NAMCO Entertainment\Tales of Arise\Saved\SaveGames\<steam id>\SaveData\`, keeping the `SaveData_NNN.sav` name. Back up the existing files first.

`python convert.py roundtrip <file.sav>...` decrypts and re-encrypts each file in memory (nothing is written) and reports whether the result is byte-identical, which is a quick check that a file is readable.

## Status

Working: converted saves load and play. Decryption and encryption also round-trip byte-exact on both platforms.

## Format notes

- The encrypted payload starts at `0x32058`: magic `0x10051630`, u32 size, a SHA-1 of payload `+0x20` to end, a format byte at `+0x1C` (PC `0x00`, PS5 `0x03`), then the data.
- Cipher: AES-256-ECB keyed with the first 32 bytes of a rolling 64-byte key. After each region the key is updated as `key[i] ^= sha1(plain)[i % 20]` (when the two bytes are equal the key byte is kept instead, or set to `0x14` if it is `0`; then `key[63] = 0`). Each 64-byte piece is also XORed with the previous ciphertext piece: PC indexes it as `(i + 2) % len`, PS5 as `min(i ^ 1, len - 1)`.
- Regions are read in order: header (`0x20`/`0x40`), the section table (pointer in header slot 10 on PC, slot 6 on PS5), then 30 linked entries. Section contents are identical across platforms.
- Editing the outer header fields at `0x50`/`0x54` trips a separate hash at `0x20058` that doesn't cover the payload, so the converter keeps the PS5 outer header.

## Dependencies

`pycryptodome` (see the root `requirements.txt`).
