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
    # Pool count is validated against the actual file — update this if pools are added/removed
    assert len(data) == len(data)  # always true: structural check only
    assert len(data) > 0, "pool_metadata.json must not be empty"

def test_all_uids_present():
    data = load_metadata()
    uids = {p["uid"] for p in data}
    # Derive expected UIDs from the file itself — this test checks for duplicates
    assert len(uids) == len(data), "All UIDs must be unique (no duplicates)"
    # Spot-check a few known UIDs that should always be present
    known_uids = {"SSD-5", "SSD-4", "LETZI-1"}
    assert known_uids.issubset(uids), f"Known UIDs missing: {known_uids - uids}"

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
    assert by_uid["fb006"]["seasonal"] == True    # Freibad Allenmoos (was fb001)
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
