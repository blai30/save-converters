import json
import struct
import tempfile
import unittest
from pathlib import Path

import convert_ayce as ayce

PASSWORD = b"76561190000000000"


def entries(document: dict) -> dict:
    return {key: json.loads(entry["m_JSON"]) for key, entry in zip(document["m_Keys"], document["m_Entries"])}


def level(document: dict, level_id: int) -> dict:
    value = entries(document)[f"Level_{level_id}"]
    return dict(zip(value["m_Key"], value["m_Value"]))


class CryptoTest(unittest.TestCase):
    def test_round_trip(self):
        document = ayce.document({"VERSION": {"m_Value": 1.0}})
        self.assertEqual(ayce.decrypt(ayce.encrypt(document, PASSWORD), PASSWORD), document)

    def test_crc_mismatch(self):
        data = bytearray(ayce.encrypt(ayce.document({}), PASSWORD))
        data[20] ^= 1
        with self.assertRaises(ValueError):
            ayce.decrypt(bytes(data), PASSWORD)


class ConvertTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write_oc1(self, records: list[tuple[int, int, int, int]], last_level: int = -1) -> Path:
        data = struct.pack("<BbB", 1, last_level, len(records))
        data += b"".join(struct.pack("<BBiB", *record) for record in records)
        path = self.dir / "oc1"
        path.write_bytes(data + bytes(64))
        return path

    def write_oc2(self, values: dict) -> Path:
        switch = {
            "m_Version": 1,
            "m_What": "OC2GSGD",
            "m_Keys": list(values),
            "m_Entries": [{"m_JSON": json.dumps(value)} for value in values.values()],
        }
        path = self.dir / "oc2"
        path.write_text(json.dumps(switch), encoding="utf-8")
        return path

    def test_oc1_base_remap(self):
        # Original id 41 is the finale, which AYCE puts at index 29.
        records = [(level_id, 0, -(2**31), 0) for level_id in range(44)]
        records[41] = (41, ayce.COMPLETED | ayce.PURCHASED | ayce.REVEALED, 478, 3)
        levels, last_level, extra = ayce.read_oc1(self.write_oc1(records, last_level=41))
        document = ayce.campaign(levels, ayce.OC1_BASE_IDS, last_level, extra)
        finale = level(document, 29)
        self.assertEqual(finale["LevelID"], "29")
        self.assertEqual((finale["Completed"], finale["Purchased"], finale["Revealed"]), ("True", "True", "True"))
        self.assertEqual((finale["HighScore"], finale["ScoreStars"], finale["FailedAttempts"]), ("478", "3", "0"))
        self.assertEqual(entries(document)["Level_Count"]["m_Value"], 37)
        self.assertEqual(entries(document)["LastLevelEntered"]["m_Value"], 29)

    def test_oc1_base_ids_are_unique(self):
        self.assertEqual(len(set(ayce.OC1_BASE_IDS)), 37)

    def test_oc2_seasonal_split(self):
        values = {"VERSION": {"m_Value": 1.0}, "Level_Count": {"m_Value": 8}}
        keys = ["LevelID", "Completed", "Purchased", "Revealed", "HighScore", "SurvivalModeTime",
                "ScoreStars", "ObjectivesCompleted", "NGPEnabled"]
        for level_id in range(8):
            values[f"Level_{level_id}"] = {
                "m_Key": keys,
                "m_Value": [str(level_id), "True", "True", "True", str(100 + level_id), "0", "3", "True", "False"],
            }
        values["Teleportal_DLC3_One"] = {"m_Key": [], "m_Value": []}
        values["Teleportals_Revealed"] = {"m_Value": [1]}
        values["LastLevelEntered"] = {"m_Value": 6}
        values["NewGamePlusEnabled"] = {"m_Value": True}
        values["CRC32"] = {"m_Value": 123}
        levels, last_level, extra = ayce.read_oc2(self.write_oc2(values))
        document = ayce.campaign(levels, [5, 6, 7], last_level, extra)
        result = entries(document)
        self.assertEqual(list(result), [
            "VERSION", "Level_Count", "Level_0", "Level_1", "Level_2", "LastLevelEntered",
            "AssistModeEnabled", "NewGamePlusEnabled", "NewGamePlusDialogShown",
        ])
        self.assertEqual(level(document, 0)["HighScore"], "105")
        self.assertEqual(result["LastLevelEntered"]["m_Value"], 1)
        self.assertTrue(result["NewGamePlusEnabled"]["m_Value"])


if __name__ == "__main__":
    unittest.main()
