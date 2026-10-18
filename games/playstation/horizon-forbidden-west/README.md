# Horizon Forbidden West: PS5 to PC

Converts a decrypted PS5 save dump (`PPSA01521`) into PC `<slot>.dat` files for Horizon Forbidden West Complete Edition. Base edition saves work: the PC game only ships as the Complete Edition and loads them as-is.

## Recipe

```sh
python convert_horizon.py <decrypted PPSA01521 folder> out
```

Launch the PC game once so it creates its save folder, then close it. Copy the output into `Documents\Horizon Forbidden West Complete Edition\<steam id64>\` (the 17-digit SteamID64). Back up the folder first: the converted files replace any of your saves with the same name. Turn off Steam Cloud for the game while copying, or it may restore the old files.

## What it changes

The PS5 `checkpoint.dat` is the same checkpoint the PC game writes: a `0x200`-byte header (`HRZ2-Release ...` build string, save type, MD5 of the body at `0x1EC`, body length at `0x1FC`) followed by the body. It is carried over unchanged; there is no account binding or checksum to redo. The PC file wraps it in a `GGDS` container:

| Offset | Type | Value |
|---|---|---|
| `0x000` | `char[4]` | `GGDS` |
| `0x004` | `u32` | `1` |
| `0x008` | `u32` | save list param: `0x0644F4A9` on PC (`param.sfo` has `0x0444F4A9` on PS5; the low 24 bits are the data version and must match) |
| `0x00C` | `u32` | checkpoint length |
| `0x010` | `u32` | thumbnail length |
| `0x014` | `char[0x80]` | title, `param.sfo` `MAINTITLE` |
| `0x094` | `char[0x100]` | subtitle, `param.sfo` `SUBTITLE` without `Horizon Forbidden West - ` |
| `0x194` | | checkpoint, then a 480x270 PNG thumbnail made from `icon0.png` |

Files are named after `SAVEDATA_DIRECTORY` in lowercase (`sdimg_QUICKSAVE4` becomes `quicksave4.dat`). The `sce_bu_` backups are identical copies and are skipped. The profile is skipped too: it only holds PS5 options (difficulty included, so set it again on PC), and the PC game creates its own.

The GGDS layout comes from [Timo654's PS4 to PC converter](https://gist.github.com/Timo654/1b6f28f19df9c6b5b084fdfe9b1e5e71) and [zakuciael/hfw-save-converter](https://github.com/zakuciael/hfw-save-converter).

## Status

Working: converted saves load and play.

## Dependencies

`Pillow` for the thumbnail (see the root `requirements.txt`).
