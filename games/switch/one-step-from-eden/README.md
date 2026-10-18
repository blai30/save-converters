# One Step From Eden: Switch to PC

Converts the Switch `SaveData` file (as exported by JKSV or similar) to the Steam format.

## Recipe

```sh
python convert_osfe.py <switch SaveData> out/SaveData
```

With the game closed, copy `out/SaveData` over `<Steam>\userdata\<account id>\960690\remote\SaveData` (back it up first). The save lives in Steam Cloud, so if Steam reports a conflict on the next launch, keep the local file.

## What it changes

Both platforms store one .NET BinaryFormatter `Hashtable` (profiles, unlocks, stats and the run in progress). Switch writes it unencrypted. PC encrypts it with AES-256-CBC over the Base64 text of the serialized bytes, with the key and IV derived by PBKDF2-HMAC-SHA1 (1000 iterations) from a password and salt stored in the game's `SaveDataCtrl`.

The Switch build's `SpellData` lacks the PC-only `permDamage` field. BinaryFormatter tolerates missing fields and leaves them at 0, so the payload is copied unchanged.

## Status

The encryption reproduces real PC saves byte for byte, and the converted payload deserializes against the PC game's own types.

## Dependencies

`pycryptodome` (see the root `requirements.txt`).
