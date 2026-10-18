# Death Stranding Director's Cut: PS5 to PC

Converts a decrypted PS5 save dump (`PPSA01970`) into PC `checkpoint.dat` saves for your Steam account.

## Recipe

```sh
python convert_death_stranding.py <decrypted PPSA01970 folder> out --steam-id <steam id>
```

`<steam id>` is your SteamID64, the 17-digit number starting with `7656119` (Steam > your account name > Account details). The output is `out\<account id>\manualsave0\checkpoint.dat` and so on, one folder per slot, where the account id is the SteamID64 minus `76561197960265728`. That is also the name of the folder the PC game creates: launch the game once, then copy the slot folders into `%LOCALAPPDATA%\KojimaProductions\DeathStrandingDC\<account id>\`. Back up the folder first, and turn off Steam Cloud for the game while you copy so it doesn't restore the old saves.

`sdimg_PROFILE` is skipped: it only holds settings, and the PC game creates its own.

## What it changes

- **Container.** The PS5 `checkpoint.dat` is an 8-byte magic, a table of chunk sizes and, from `0x110`, chunks of a big-endian u64 raw size plus a zlib stream. They join into 12 MiB of raw save data that both platforms share. The PC file starts with three 8-byte NUL-padded decimal sizes, followed by the slot metadata, a PNG cover and the data block.
- **Metadata.** CRLF text, `[Slot]` then `Title`, `SubTitle`, `Detail`, `UserParam` and `ModificationTime`, each key padded to 32 columns before `= `. The values come from `param.sfo` and are percent-encoded like game-written files. `UserParam` is the PC value `#004356D3`, and `ModificationTime` is the PS5 save time in local time, as microseconds since 0001-01-01. The cover is `icon0.png` resized to 228x128 (Lanczos).
- **Encryption.** The data block is u32 magic `0xC0391E1D`, a random 4-byte key header, then `<u32 length, "steam_<SteamID64>", Kraken stream>` XORed with MurmurHash3_x64_128 (seed `0x2A`) of the key header plus fixed key material, repeated over the whole block. The embedded user id is the only account binding. The key derivation comes from [mi5hmash/DeathStrandingSaveDataResigner](https://github.com/mi5hmash/DeathStrandingSaveDataResigner). Unlike that tool, the keystream doesn't restart for the tail, because game-written files with odd-length tails only decode that way.
- **Compression.** The PC wraps the raw data in an Oodle Kraken stream. There is no Oodle compressor here, so the converter writes a valid stream without one: each 256 KiB block is stored uncompressed, and all-zero blocks become fill quanta (block format from [powzix/ooz](https://github.com/powzix/ooz)). The game loads these fine, but the files are about 5.8 MB instead of the usual 0.4 MB.

The converter refuses saves whose data version (low 24 bits of `SAVEDATA_LIST_PARAM`) differs from the PC game's, and checks each output by decrypting it again and comparing it with the PS5 data. The format was confirmed against real PC saves from [niemasd/Game-Saves](https://github.com/niemasd/Game-Saves).

## Status

Working: converted saves load and play.

## Dependencies

`Pillow` for the cover image (see the root `requirements.txt`).
