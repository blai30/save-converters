# Overcooked! All You Can Eat: Switch to PC

Merges Switch saves of **Overcooked! Special Edition** and **Overcooked! 2** (as exported by JKSV or similar) into one **Overcooked! All You Can Eat** save on Steam, including the DLC campaigns that AYCE lists under Extra Trimmings.

## Recipe

Start AYCE once and create a save slot, so `%USERPROFILE%\AppData\LocalLow\Team17\Overcooked All You Can Eat\<user id>` exists. The converter only reads it, for the user id that keys the encryption.

```sh
python convert_ayce.py <overcooked 1 switch folder> <overcooked 2 switch folder> "<AYCE save folder>" out
```

With the game closed, back up the AYCE save folder and copy the output files into it. Leave `Meta_SaveFile.save` (settings, key bindings, stats) as it is. Steam Cloud syncs this folder, so if Steam reports a conflict on the next launch, keep the local files.

| Switch file | AYCE file | Campaign |
| --- | --- | --- |
| OC1 `CampaignSave0` | `OC1_CoopSlot_SaveFile_0.save` | Overcooked! |
| OC1 `DLC1CAMPAIGNSAVE0` | `DLC101_CoopSlot_SaveFile_0.save` | The Lost Morsel |
| OC1 `DLC2CAMPAIGNSAVE0` | `DLC102_CoopSlot_SaveFile_0.save` | Festive Seasoning |
| OC2 `CampaignSave0` | `CoopSlot_SaveFile_0.save` | Overcooked! 2 |
| OC2 `DLC2CAMPAIGNSAVE0` | `DLC2_CoopSlot_SaveFile_0.save` | Surf 'n' Turf |
| OC2 `DLC5CAMPAIGNSAVE0` | `DLC5_CoopSlot_SaveFile_0.save` | Campfire Cook Off |
| OC2 `DLC7CAMPAIGNSAVE0` | `DLC7_CoopSlot_SaveFile_0.save` | Night of the Hangry Horde |
| OC2 `DLC8CAMPAIGNSAVE0` | `DLC8_CoopSlot_SaveFile_0.save` | Carnival of Chaos |
| OC2 `DLC3CAMPAIGNSAVE0` | `DLC3`, `DLC4`, `DLC9`, `DLC10`, `DLC11`, `DLC13` | Seasonal updates, one file per season |

Campaigns with no completed levels are skipped, so AYCE starts them with its own unlock state.

## What it changes

AYCE runs on the Overcooked 2 engine, and every campaign file is an `OC2GSGD` JSON document encrypted the same way as Overcooked 2 on PC: AES-256-CBC with a random IV prefix, a key derived with .NET `PasswordDeriveBytes` (SHA-1, 2 iterations) from the user id and a fixed salt, and a custom CRC32 suffix.

- **Overcooked 2**: the Switch files are already `OC2GSGD` JSON. Levels gain AYCE's `FailedAttempts` field, and the Switch-only `CRC32` entry is dropped. The seasonal campaign is a single 32-level file on Switch but one file per season in AYCE, so it's split, and the teleporters of the combined seasonal hub are dropped.
- **Overcooked 1**: the Switch files are a packed binary (version byte, last level byte, level count, then 7-byte records of id, flags, high score and stars). AYCE renumbered the base campaign: the original's 44 entries include 7 unused placeholders, and the rest are reordered into play order. The DLC campaigns keep their ids.

AYCE rescored Overcooked 1 (its star thresholds are roughly 3x higher), so converted high scores look low next to the new thresholds. Star counts carry over as earned, and replaying a level replaces the score as usual.

## Status

The encryption reproduces real AYCE saves byte for byte, and every converted file matches the layout of the files AYCE writes itself.

## Dependencies

`pycryptodome` (see the root `requirements.txt`).
