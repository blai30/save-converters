# Final Fantasy XVI: PS5 to PC

Converts a decrypted PS5 save dump (`PPSA10664`) into PC `NNNN.png` and `auto.png` files.

## Recipe

```sh
python convert_ff16.py <decrypted PPSA10664 folder> out
```

Copy the output into `Documents\My Games\FINAL FANTASY XVI\Steam\<steam id>\` (the 17-digit SteamID64 folder the PC game created) next to its `system.png` and `config.xml` (keep those). Back up the folder first.

## What it changes

The save payload is the same on both platforms (`<u32 header size 0x10, u32 CRC-32, u64 version>` then sections) and is carried over unchanged; only the wrapper differs. There is no Steam id binding.

- The PC save is a PNG whose image is the save icon. The save sits in a private `faTh` chunk after `IHDR`, followed by the image data split into `0x2000`-byte `IDAT` chunks and `IEND`.
- `faTh` holds a `UMIF` container: a `0x10` header, a `0x20`-byte entry per file (name length, blob length, name offset, payload length, blob offset), the file names, then the compressed blobs, each 4-byte aligned.
- Names and blobs are XORed with the 64-bit little-endian key `0x0F3F80FE5F1FC4F3`; a tail shorter than 8 bytes is XORed in 4, 2 and 1-byte pieces that each restart at the low key byte.
- Blobs are zlib streams compressed against a fixed 32 KiB preset dictionary (`ff16_zdict.bin`, SHA-256 `09cb9606666b9a218c4f3b26a0dd814e2f4de253041630f85dcaaa542a7e4ceb`), stored without the 2-byte `78 F9` zlib header.

Each manual save `sdimg_FAITHDATANNNN` becomes `NNNN.png`. The ten autosaves in `sdimg_FAITHDATAAUTO` all go into `auto.png`, ordered newest first by the save time in their UserData section, as the PC game writes them. `sdimg_FAITHDATASYSTEM` (settings) is skipped: the PC system save has extra sections the PS5 one lacks.

Every written PNG is decoded again and must give back the exact PS5 payloads.

## Status

Working: converted saves load and play.

## Credits

Container format, XOR key and dictionary from [Nenkai/FF16Tools](https://github.com/Nenkai/FF16Tools) (`FaithSaveFile.cs`, `CompressDict.cs`). `ff16_zdict.bin` is the `ZlibDict` array from `CompressDict.cs`, redistributed under its MIT license:

```text
MIT License

Copyright (c) 2025 Nenkai

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
