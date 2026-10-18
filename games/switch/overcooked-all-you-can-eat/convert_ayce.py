"""Merge Overcooked! Special Edition and Overcooked! 2 Switch saves into Overcooked! All You Can Eat (PC).

AYCE runs on the Overcooked 2 engine, so every campaign file is an OC2GSGD JSON document
(parallel m_Keys / m_Entries, each entry a nested JSON string), encrypted like Overcooked 2
on PC: AES-256-CBC with a random IV prefix, key from .NET PasswordDeriveBytes (SHA-1, 2
iterations) over the save folder's user id, and a custom CRC32 suffix.

- Overcooked 2 (Switch) is already OC2GSGD JSON. Levels gain AYCE's FailedAttempts field
  and the Switch-only CRC32 entry is dropped. The seasonal campaign is one 32-level file on
  Switch but one file per season in AYCE.
- Overcooked 1 (Switch) is a packed binary: version byte, last level byte, level count,
  then 7-byte records (id, flags, high score, stars). The base campaign's level ids were
  renumbered for AYCE (see OC1_BASE_IDS); the DLC campaigns keep theirs.

Usage: python convert_ayce.py <oc1 switch dir> <oc2 switch dir> <ayce save dir> <output dir>

The AYCE save dir is LocalLow/Team17/Overcooked All You Can Eat/<user id>. It is only read,
for the user id that keys the encryption.
"""

import json
import struct
import sys
from hashlib import sha1
from pathlib import Path

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad, unpad

SALT = b"jjo+Ffqil5bdpo5VG82kLj8Ng1sK7L/rCqFTa39Zkom2/baqf5j9HMmsuCr0ipjYsPrsaNIOESWy7bDDGYWx1eA=="
CRC_POLYNOMIAL = 0x58E6D9AF
CRC_SEED = 0xD6EAF23C
LEVEL_FIELDS = ["LevelID", "Completed", "Purchased", "Revealed", "HighScore", "SurvivalModeTime",
                "ScoreStars", "ObjectivesCompleted", "NGPEnabled", "FailedAttempts"]
# Switch-only entries: a payload checksum, and teleporters of the combined seasonal hub,
# which AYCE splits into separate maps.
DROPPED_KEYS = ("CRC32", "Teleportal")

# OC1 record flags.
COMPLETED = 1
PURCHASED = 2
REVEALED = 4

# AYCE OC1 base level index -> original Overcooked level id, matched through the original
# game's scene directory (labels, star costs and scene themes line up one to one).
# Original ids 0, 14-16, 18, 30 and 36 are unused placeholders with no AYCE counterpart.
OC1_BASE_IDS = [
    39,  # tutorial
    1, 43, 5, 2, 3, 17,  # world 1
    19, 13, 6, 8,  # world 2
    25, 12, 28, 4,  # world 3
    35, 20, 9, 29,  # world 4
    7, 21, 42, 22, 23, 26,  # world 5
    11, 27, 24, 10, 41,  # world 6 (41 is the finale)
    40, 31, 32, 33, 34, 37, 38,  # onion house 0-6
]

# (source game, Switch file, AYCE file, Switch level ids in AYCE order or None for all).
CAMPAIGNS = [
    ("oc1", "CampaignSave0", "OC1_CoopSlot_SaveFile_0.save", OC1_BASE_IDS),
    ("oc1", "DLC1CAMPAIGNSAVE0", "DLC101_CoopSlot_SaveFile_0.save", None),
    ("oc1", "DLC2CAMPAIGNSAVE0", "DLC102_CoopSlot_SaveFile_0.save", None),
    ("oc2", "CampaignSave0", "CoopSlot_SaveFile_0.save", None),
    ("oc2", "DLC2CAMPAIGNSAVE0", "DLC2_CoopSlot_SaveFile_0.save", None),
    ("oc2", "DLC5CAMPAIGNSAVE0", "DLC5_CoopSlot_SaveFile_0.save", None),
    ("oc2", "DLC7CAMPAIGNSAVE0", "DLC7_CoopSlot_SaveFile_0.save", None),
    ("oc2", "DLC8CAMPAIGNSAVE0", "DLC8_CoopSlot_SaveFile_0.save", None),
    # Seasonal updates, in the order of the Switch campaign.
    ("oc2", "DLC3CAMPAIGNSAVE0", "DLC3_CoopSlot_SaveFile_0.save", list(range(0, 5))),
    ("oc2", "DLC3CAMPAIGNSAVE0", "DLC4_CoopSlot_SaveFile_0.save", list(range(5, 12))),
    ("oc2", "DLC3CAMPAIGNSAVE0", "DLC9_CoopSlot_SaveFile_0.save", list(range(12, 17))),
    ("oc2", "DLC3CAMPAIGNSAVE0", "DLC10_CoopSlot_SaveFile_0.save", list(range(17, 22))),
    ("oc2", "DLC3CAMPAIGNSAVE0", "DLC11_CoopSlot_SaveFile_0.save", list(range(22, 27))),
    ("oc2", "DLC3CAMPAIGNSAVE0", "DLC13_CoopSlot_SaveFile_0.save", list(range(27, 32))),
]


def crc32(data: bytes) -> int:
    table = []
    for index in range(256):
        value = index
        # Unlike standard CRC32, odd values are XORed without being shifted.
        for _ in range(8):
            value = value ^ CRC_POLYNOMIAL if value & 1 else value >> 1
        table.append(value)
    value = CRC_SEED
    for byte in data:
        value = value >> 8 ^ table[(byte ^ value) & 0xFF]
    return value


def derive_key(password: bytes) -> bytes:
    # .NET PasswordDeriveBytes: two SHA-1 rounds, then counter-prefixed blocks past 20 bytes.
    base = sha1(password + SALT).digest()
    key = sha1(base).digest()
    counter = 1
    while len(key) < 32:
        key += sha1(str(counter).encode() + base).digest()
        counter += 1
    return key[:32]


def encrypt(document: dict, password: bytes, iv: bytes | None = None) -> bytes:
    iv = iv or get_random_bytes(16)
    plain = json.dumps(document, separators=(",", ":")).encode()
    data = iv + AES.new(derive_key(password), AES.MODE_CBC, iv).encrypt(pad(plain, AES.block_size))
    return data + struct.pack("<I", crc32(data))


def decrypt(data: bytes, password: bytes) -> dict:
    if struct.unpack("<I", data[-4:])[0] != crc32(data[:-4]):
        raise ValueError("CRC mismatch")
    cipher = AES.new(derive_key(password), AES.MODE_CBC, data[:16])
    return json.loads(unpad(cipher.decrypt(data[16:-4]), AES.block_size))


def document(entries: dict) -> dict:
    return {
        "m_Version": 1,
        "m_What": "OC2GSGD",
        "m_Keys": list(entries),
        "m_Entries": [{"m_JSON": json.dumps(value, separators=(",", ":"))} for value in entries.values()],
    }


def campaign(levels: dict[int, dict], source_ids: list[int], last_level: int, extra: dict) -> dict:
    """Build an AYCE campaign from Switch level fields keyed by Switch level id."""
    entries = {"VERSION": {"m_Value": 1.0}, "Level_Count": {"m_Value": len(source_ids)}}
    for level_id, source_id in enumerate(source_ids):
        fields = {"FailedAttempts": "0", **levels[source_id], "LevelID": str(level_id)}
        entries[f"Level_{level_id}"] = {"m_Key": LEVEL_FIELDS, "m_Value": [fields[name] for name in LEVEL_FIELDS]}
    new_game_plus = {key: extra.get(key, {"m_Value": False}) for key in ("NewGamePlusEnabled", "NewGamePlusDialogShown")}
    entries.update({key: value for key, value in extra.items() if key not in new_game_plus})
    entries["LastLevelEntered"] = {"m_Value": source_ids.index(last_level) if last_level in source_ids else 0}
    entries["AssistModeEnabled"] = {"m_Value": False}
    entries.update(new_game_plus)
    return document(entries)


def read_oc1(path: Path) -> tuple[dict[int, dict], int, dict]:
    data = path.read_bytes()
    last_level, count = struct.unpack_from("<bB", data, 1)
    levels = {}
    for index in range(count):
        level_id, flags, score, stars = struct.unpack_from("<BBiB", data, 3 + index * 7)
        completed = str(bool(flags & COMPLETED))
        levels[level_id] = {
            "Completed": completed,
            "Purchased": str(bool(flags & PURCHASED)),
            "Revealed": str(bool(flags & REVEALED)),
            "HighScore": str(score),
            "SurvivalModeTime": "0",
            "ScoreStars": str(stars),
            "ObjectivesCompleted": completed,
            "NGPEnabled": "False",
        }
    return levels, last_level, {}


def read_oc2(path: Path) -> tuple[dict[int, dict], int, dict]:
    switch = json.loads(path.read_text(encoding="utf-8"))
    levels, last_level, extra = {}, 0, {}
    for key, entry in zip(switch["m_Keys"], switch["m_Entries"]):
        value = json.loads(entry["m_JSON"])
        if key.startswith("Level_") and key != "Level_Count":
            fields = dict(zip(value["m_Key"], value["m_Value"]))
            levels[int(fields["LevelID"])] = fields
        elif key == "LastLevelEntered":
            last_level = value["m_Value"]
        elif key not in ("VERSION", "Level_Count") and not key.startswith(DROPPED_KEYS):
            extra[key] = value
    return levels, last_level, extra


def main() -> None:
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    oc1_dir, oc2_dir, ayce_dir, output_dir = map(Path, sys.argv[1:])
    password = ayce_dir.name.encode()
    # Fail early if the folder name isn't the id that keys the existing saves.
    decrypt((ayce_dir / "Meta_SaveFile.save").read_bytes(), password)
    output_dir.mkdir(parents=True, exist_ok=True)
    for game, switch_name, ayce_name, source_ids in CAMPAIGNS:
        source = (oc1_dir if game == "oc1" else oc2_dir) / switch_name
        if not source.exists():
            continue
        levels, last_level, extra = (read_oc1 if game == "oc1" else read_oc2)(source)
        source_ids = source_ids or sorted(levels)
        # Leave campaigns without progress to AYCE, so they keep its own unlock state.
        if not any(levels[source_id]["Completed"] == "True" for source_id in source_ids):
            print(f"{switch_name} -> {ayce_name}: skipped, nothing completed")
            continue
        data = encrypt(campaign(levels, source_ids, last_level, extra), password)
        # Sanity check: the output must decrypt and pass the CRC check.
        decrypt(data, password)
        (output_dir / ayce_name).write_bytes(data)
        print(f"{switch_name} -> {ayce_name}")


if __name__ == "__main__":
    main()
