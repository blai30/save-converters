# Mega Man 11: Switch to PC

Converts Switch saves (`SYSTEM.bin`, `GAMEDATA.bin`, `REPLAY.bin`) to the Steam format.

## Recipe

You need the PC save folder from your own Steam account as a template (start the game once so it exists). It supplies the SteamID entries, data versions and PC-only settings. It's only read.

```sh
python convert_mm11.py <switch save folder> "<Steam>\userdata\<account id>\742300\remote" out
```

Copy the output over the files in that `remote` folder (back them up first).

## What it changes

- Both platforms store each file as a table of named entries (`~crc32(name)` hash, kind, value or struct blob).
- Switch files are plaintext with a `0x10`-byte header. PC files have a `0x40`-byte header (data version, table size, word-swapped SHA-1 of the payload, payload size) and are Blowfish ECB encrypted with the key `!!robomono_ororhooooon!!` over byte-swapped 32-bit words.
- PC adds SteamID entries, taken from the template.
- The PC loader silently skips blobs whose size differs from its own struct, so platform-specific structs (game slots, system data) are remapped field by field.

Slot blobs carry no slot index, so a slot can be moved to another one by renaming its entry (`gamedata0` is slot 1).

## Status

Working: converted saves load and play.

## Dependencies

`pycryptodome` (see the root `requirements.txt`).
