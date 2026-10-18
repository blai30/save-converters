# God of War (2018): PS4 to PC

Converts a decrypted PS4 `memory.dat` into a `game.sav` that the Steam version can load directly (Continue and Load Game), with every checkpoint intact.

A PS4 save renamed to `game.sav` only works for New Game+ on PC; loading a checkpoint crashes. This converter fixes that.

## Recipe

You need a `game.sav` written by your own PC copy of the game (start the game once and save). It supplies the PC file header, your Steam account id and the PC settings layout. It's only read, never modified.

```sh
python gow_ps4_to_pc.py memory.dat --template "<path to your PC game.sav>" --repack-resources --out game.sav
```

Then copy the output over `%USERPROFILE%\Saved Games\God of War\<steam id>\game.sav` (back up the original first).

## What it changes

- Keeps the PC template's file header and profile record, and transplants all PS4 checkpoint slots.
- Per checkpoint: build number, the platform marker byte at stats `+0x128`, and the hash that opens the player-state block (`0x8DD0B211` PS4, `0xD31AD457` PC).
- Zone-state record version `0x133` to `0x135`.
- `--repack-resources`: the PS4 build writes the resource bundle list packed (`0x68`-byte entries: u16 id, u8 flags, 33-byte name), and the PC build expects natural alignment (`0x70`-byte entries: u32 id, u32 flags, 36-byte name). This was the only remaining incompatibility; the zone-state payloads are byte-compatible across platforms.

The remaining flags (`--transplant`, `--zones-from-template`, `--ps4-zones`, and so on) were used to bisect the format and aren't needed for a normal conversion.

## Format notes

- 32 MiB container: `0x40` file header (u64 time, u32 version 4, u32 build, u32 `0xD0`, u32 slot size, u32 file size; PC adds u32 3 and the Steam3 account id), a profile record at `0x40` (version `0x11` PS4, `0x1B` PC), then 20 checkpoint slots of `0x1998C8` bytes.
- Checkpoint: `0x10` header, `0x100` location name, stats at `+0x110`, a fixed 64 KiB world block at `+0x400`, the inventory table at `+0x10409`, then u16-size framed sections ending with `FF FF FF FF`.
- Tail (read by the PC function at `0x14082cf00`): u32 zone count, then per zone a `0xA8`-byte struct followed by a u16-length MSB-first bit-stream payload; then the resource bundle list; then a fixed 396-byte block, u32 0 and `FF FF FF FF`.

## Tests

```sh
python -m unittest discover -s .
```
