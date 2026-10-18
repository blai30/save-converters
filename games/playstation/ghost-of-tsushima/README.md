# Ghost of Tsushima Director's Cut: PS5 to PC

Converts a decrypted PS5 save dump (`PPSA02225`) into PC `auto.sav` and `manual_NNNN.sav` files.

## Recipe

```sh
python convert_tsushima.py <decrypted PPSA02225 folder> out
```

Copy the output into `Documents\Ghost of Tsushima DIRECTOR'S CUT\<steam id>\` next to the `user.sav` the PC game created (keep that one). Back up the folder first.

## What it changes

Each `dis.dat` is `<u32 magic, u32 length, u32 checksum, u32 count>`, then `count` 8-byte save-format update IDs, then the body, zero-padded to a fixed slot size. The PS5 list has a PS5-only ID (`a7d25b6bb1667f30`) and a PlayStation marker (`7e8a516a45ed4447`); the PC list has the PC marker (`d5cf106b395e5d10`) plus a PC-only ID (`bfe1bd115c5c51a2`). The converter swaps those, drops the padding, and recomputes the length and the checksum (`sum(bytes after the header) + length + magic + count`). Based on [KOVRlN/ghostoftsushima-save-converter](https://github.com/KOVRlN/ghostoftsushima-save-converter).

Manual saves (including `sce_bu_` backups) are deduplicated and numbered oldest first using the save time in each folder's `param.sfo`, which also becomes the file time. Trimming the padding alone is not enough; the PC game rejects saves with the PS5 ID list.

## Legends progress

PlayStation saves have no separate profile file: the Legends profile (plus shared cosmetic unlocks) is embedded in every campaign save, and the converter carries it over unchanged. On PC the profile also lives in `user.sav`, but loading a campaign save replaces the in-memory profile with the one embedded in that save. To play with your console Legends progress, load a converted save first, then open Legends from that session.

## Status

Working: converted saves load and play.
