# Devil May Cry 5: PS4 / PS5 to PC

Converts decrypted console saves (`dataNNN.bin`) into PC saves for your Steam account.

## Recipe

```sh
python convert_dmc5.py data000.bin --account-id <steam account id> --out out/data000.bin
```

The account id is the number in `Steam\userdata\<account id>\601150\remote\win64_save`, which is also where the output goes. `data000.bin` is the main campaign, `dataNNNSlot.bin` are the character slots, `data00-1.bin` is system data. Back up the folder first.

## What it changes

Saves are RE Engine `DSSS` v2 containers: a `0x10` header, an encrypted `DSSSDSSS` check block, the serialized class tree, and a MurmurHash3 (seed `0xFFFFFFFF`) of the file in the last 4 bytes. Encryption is Blowfish CBC with a zero IV over 32-bit little-endian words.

- PS4 saves come off the console already decrypted; PS5 saves use the key `p4cRj0xD9zN7SeMQgH531ufW6k8Tv2Yb`; PC uses `dK7w3CX1y60OJaB4mr2LqsU9tnV58EiG`.
- PC adds an encrypted 8-byte Steam account id after the check block, so the body moves from `0x18` to `0x20`.
- Values in the body are aligned to absolute file offsets, so the converter walks the whole class tree (layout as in [kvasszn/ree-save-editor](https://github.com/kvasszn/ree-save-editor)) and recomputes every alignment pad for the new position, then re-encrypts with the PC key and rehashes.

The converted file is checked by decrypting it again, verifying the hash and account id, and re-walking the tree.

## Status

Working: a PS4 campaign converted this way loads and plays. PS5 Special Edition saves convert and verify offline but haven't been loaded in game directly; the Special Edition stores some things differently from the standard PC edition (Vergil's skills sit at `SkillMemory[3]` instead of `[0]`, plus Legendary Dark Knight data the PC edition doesn't have).

## Dependencies

`pycryptodome` (see the root `requirements.txt`).
