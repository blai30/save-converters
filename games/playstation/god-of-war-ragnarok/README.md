# God of War Ragnarok: PS5 to PC

Converts decrypted PS5 checkpoints (`GOWRSAVE<n>`) so the Steam version lists and loads them where you left off. No PC save or template is needed.

## Recipe

```sh
python gowr_ps5_to_pc.py GOWRSAVE20 --repack --player-hash --out out/GOWRSAVE20
python verify_output.py out/GOWRSAVE20
```

Copy the output into `%USERPROFILE%\Saved Games\God of War Ragnarök\<steam id>\`, keeping the `GOWRSAVE<n>` name. Back up anything you'd overwrite.

`verify_output.py` parses every stream of the converted file with the PC grammar and prints `OK` when everything walks exactly to its end.

## What it changes

The PS5 build serializes many structures **packed**; the PC build pads them to natural alignment. `--repack` rebuilds each one in the PC layout and verifies it by re-parsing:

- State stream: section s3 (entry headers 20 to 24 bytes, elements 6 to 8), loot s5 (22 to 32), pairs s6 (12 to 16, or 11 to 16 in the 2022 format), s8 (17-byte header to 24, transform records 33 to 48), plus a trailing string table the PC grammar never reads.
- Second stream: blocks b, c and d (12/13/10-byte entries to 16, 20 to 24); the transient block f is dropped.
- Tail stream: player entity headers (`0x15` to `0x18`), the entity blob inside every zone record, and the resource bundle list (`0x140` to `0x148`-byte entries).
- Older (generation 0/1) files are moved to the generation 2 stream offsets, and the stream sizes in the header are recomputed.

`--player-hash` writes the PC layout hash (`0xD31AD457`) over the PS5 one (`0xE0ECF911`) at the start of the tail stream. The build number is updated by default.

The other flags (`--template`, `--swap`, `--set`, `--versions`, `--tag`) were used for diagnostics. Don't use `--tag`: it overwrites real stream data.

## Status

Tested in game: a post-game October 2023 checkpoint loads at the exact spot it was saved on PS5. All 21 checkpoints from that console (game builds 5232113 and 5320768) convert and pass `verify_output.py`; the mid-game ones haven't been loaded in game yet.

## Format notes

- `0x40` file header (u64 time, u32 version 8, u32 build, u32 `0x2B8`, u32 slot size, u32 total size), record header at `0x40` (version 35) with the three stream sizes at `0x198` / `0x19C` / `0x1A0` and a 32-byte section version table at `0x1C4`.
- Three streams at offsets set by the generation byte at `0x1E2`: state at `0x440`; second stream at `0x2F440` (generation 0/1) or `0x80440` (generation 2); tail (player block, zone records, resource list) at `0x44440` or `0xC0440`, running to end of file.
- The writer never clears gaps, so stale data from older saves can sit between streams and after the tail. The converter drops it.
- `parse_state.py`, `parse_stream1.py`, `parse_player.py` and `records.py` model the PC loader's grammar for each stream and can be run directly on a file to dump its structure.

## Tests

```sh
python -m unittest discover -s .
```
