import json
from pathlib import Path

METADATA_PATH = Path(__file__).parent.parent.parent / "ml" / "pool_metadata.json"

def load_metadata():
    with open(METADATA_PATH) as f:
        return json.load(f)

def test_metadata_file_exists():
    assert METADATA_PATH.exists()

def test_all_22_pools_present():
    data = load_metadata()
    assert len(data) == 22

def test_all_uids_present():
    data = load_metadata()
    uids = {p["uid"] for p in data}
    expected = {
        "fb001", "fb008", "fb012", "LETZI-1", "SSD-11", "fb018",
        "SSD-1", "SSD-2", "SSD-3", "SSD-4", "SSD-6", "SSD-7", "SSD-8",
        "LIDO-1", "RISCH-1", "SSD-10",
        "seb6946", "seb6947", "seb6948",
        "SSD-5", "WEN-1", "HUENENBERG-1"
    }
    assert uids == expected

def test_all_types_valid():
    data = load_metadata()
    valid_types = {"hallenbad", "freibad", "strandbad", "seebad", "other"}
    for pool in data:
        assert pool["type"] in valid_types, f"{pool['uid']} has invalid type: {pool['type']}"

def test_seasonal_flags_correct():
    data = load_metadata()
    by_uid = {p["uid"]: p for p in data}
    # Hallenbäder are not seasonal
    assert by_uid["SSD-5"]["seasonal"] == False   # Wärmebad Käferberg
    assert by_uid["SSD-4"]["seasonal"] == False   # Hallenbad City
    # Freibäder are seasonal
    assert by_uid["fb001"]["seasonal"] == True    # Freibad Allenmoos
    assert by_uid["LETZI-1"]["seasonal"] == True  # Freibad Letzigraben

def test_kaeferberg_capacity():
    data = load_metadata()
    kaeferberg = next(p for p in data if p["uid"] == "SSD-5")
    assert kaeferberg["max_capacity"] == 70

def test_all_required_fields_present():
    data = load_metadata()
    required_fields = {"uid", "name", "type", "seasonal", "city", "max_capacity"}
    for pool in data:
        missing = required_fields - set(pool.keys())
        assert not missing, f"{pool['uid']} missing fields: {missing}"
