# Atelier Lulua: Switch to PC

Converts Switch save folders (`GameDataNN/data.dat`, `SystemData/data.dat`, as exported by JKSV or similar) to the Steam format.

## Recipe

```sh
python convert_lulua.py <switch save folder> out
```

Copy the output folders into `Documents\KoeiTecmo\Atelier Lulua\` (back up the existing ones first).

## What it changes

A `data.dat` is a `0x100`-byte plaintext header followed by a scrambled body (Gust's EncDec, same seeds on both platforms). The plaintext game data is identical across platforms; the differences are:

- Header timestamp at `0x30`: Unix seconds on Switch, Windows FILETIME on PC.
- Compression inside the scrambled body: Switch uses Gust's LZ format ("Glaze"), PC uses a byte frequency table followed by Elias gamma coding. The PC loader only understands its own, so the converter decompresses and re-compresses.

The encoder reproduces real PC saves byte for byte.

## Status

Working: converted saves load and play.

## Other Gust games

Other Atelier titles likely use the same pipeline with different seeds and constants, which have to be read from that game's executable (look for `0x3B9A73C9` and the `EncDec::` strings).
