"""Run with: python -m pytest tests -q   (or: python tests/test_logic.py)"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402
import i18n  # noqa: E402
import matching  # noqa: E402

LANGS = list(i18n.LANGUAGES)


def fresh_db():
    path = os.path.join(tempfile.mkdtemp(), "test.db")
    db.init_db(path)
    return path


def test_all_translations_complete():
    for table in (i18n.UI, i18n.RESIDUES, i18n.CONDITIONS, i18n.REUSE, i18n.CAUTIONS, i18n.BUYER_TYPES):
        for key, entry in table.items():
            for lang in LANGS:
                assert entry.get(lang), f"missing {lang} for {key}"


def test_dataset_keys_have_translations():
    path = fresh_db()
    for k in db.residue_keys(path):
        assert k in i18n.RESIDUES
        for rule in db.get_reuse_rules(k, path):
            assert rule["reuse_key"] in i18n.REUSE
            for c in rule["conditions"].split("|"):
                assert c in i18n.CONDITIONS
            for c in rule["cautions"].split("|"):
                assert not c or c in i18n.CAUTIONS
    for b in db.list_buyers(path):
        assert b["buyer_type"] in i18n.BUYER_TYPES
        assert b["is_demo"] == 1
    for reason in ("same_district", "other_district", "qty_ok", "qty_over",
                   "qty_under", "price_ok", "price_high", "price_unknown"):
        assert "reason_" + reason in i18n.UI


def test_seed_is_idempotent():
    path = fresh_db()
    n = len(db.list_buyers(path))
    db.init_db(path)
    assert len(db.list_buyers(path)) == n


def test_moldy_only_compost_or_nothing_unsafe():
    path = fresh_db()
    for k in db.residue_keys(path):
        uses = {s.reuse_key for s in matching.suggest_reuse(k, "moldy", path)}
        assert uses <= {"composting"}, (k, uses)
    # groundnut shells must never be offered as feed when moldy or wet
    for cond in ("moldy", "wet", "fresh_green"):
        uses = {s.reuse_key for s in matching.suggest_reuse("groundnut_shells", cond, path)}
        assert "animal_feed" not in uses


def test_suggestions_carry_cautions():
    path = fresh_db()
    feed = [s for s in matching.suggest_reuse("groundnut_shells", "dry", path) if s.reuse_key == "animal_feed"][0]
    assert "aflatoxin" in feed.cautions and "feed_check" in feed.cautions


def test_matching_filters_and_scores():
    path = fresh_db()
    district = db.get_buyers_for("rice_straw", path)[0]["district"]
    m = matching.match_buyers("rice_straw", 500, "dry", district, None, path)
    assert m and [x.score for x in m] == sorted((x.score for x in m), reverse=True)
    assert all(x.buyer["residue_key"] == "rice_straw" for x in m)
    assert all("dry" in x.buyer["accepted_conditions"].split("|") for x in m)
    assert all(x.score <= 100 for x in m)
    assert "same_district" in m[0].reasons  # same-district buyers rank first
    # wet / moldy residue only matches buyers that accept it
    for cond in ("wet", "moldy"):
        for x in matching.match_buyers("rice_straw", 500, cond, district, None, path):
            assert cond in x.buyer["accepted_conditions"].split("|")
    # price above every buyer's cap is flagged, never scored for price
    hi = matching.match_buyers("rice_straw", 500, "dry", district, 999, path)
    assert all("price_high" in x.reasons for x in hi)
    # quantity below minimum / above maximum are flagged
    tiny = matching.match_buyers("rice_straw", 1, "dry", district, None, path)
    assert any("qty_under" in x.reasons for x in tiny)
    huge = matching.match_buyers("rice_straw", 10_000_000, "dry", district, None, path)
    assert all("qty_over" in x.reasons for x in huge)


def test_request_workflow_and_contact_consent():
    path = fresh_db()
    lid = db.add_listing("Ravi", "9876543210", True, "rice_straw", 1000, "dry", "Thanjavur", 2.0, path)
    other = db.add_listing("Anu", "9123456780", False, "rice_straw", 500, "dry", "Erode", None, path)
    buyer = matching.match_buyers("rice_straw", 1000, "dry", "Thanjavur", 2.0, path)[0].buyer["id"]
    assert db.create_request(lid, buyer, path) is True
    assert db.create_request(lid, buyer, path) is False  # no duplicates
    db.create_request(other, buyer, path)
    assert db.requested_buyer_ids(lid, path) == {buyer}
    rows = db.requests_for_buyer(buyer, path)
    assert {r["status"] for r in rows} == {"pending"}
    db.set_request_status(rows[0]["id"], "accepted", path)
    assert db.requests_for_listings([lid, other], path)[0]["status"] in ("accepted", "pending")
    # consent flag stored only when a phone exists
    l2 = db.add_listing("", "", True, "rice_straw", 10, "dry", "X", None, path)
    assert db.get_listing(l2, path)["share_contact"] == 0
    try:
        db.set_request_status(1, "hacked", path)
        assert False
    except ValueError:
        pass


def test_accounts_and_buyer_profile():
    path = fresh_db()
    farmer = db.create_user("farmer", "Ravi", "9876543210", "Thanjavur", True, path)
    assert farmer and farmer["role"] == "farmer" and farmer["share_contact"] == 1
    assert db.create_user("farmer", "Dup", "9876543210", "X", False, path) is None  # phone is unique per role
    assert db.create_user("buyer", "Same phone, other role", "9876543210", "Erode", False, path) is not None
    assert db.user_by_token(farmer["token"], "farmer", path)["id"] == farmer["id"]
    assert db.user_by_token(farmer["token"], "buyer", path) is None  # token only works for its own role
    assert db.user_by_token("nope", None, path) is None
    assert db.user_by_phone("farmer", "9876543210", path)["name"] == "Ravi"
    assert db.user_by_phone("farmer", "9000000000", path) is None

    buyer = db.create_user("buyer", "Mushroom Co", "9123456780", "Thanjavur", False, path)
    db.save_buyer_profile(buyer["id"], "Mushroom Co", "mushroom_grower", "Thanjavur", "9123456780",
                          ["rice_straw", "wheat_straw"], ["dry"], 100, 5000, 3.0, path)
    prof = db.get_buyer_profile(buyer["id"], path)
    assert sorted(prof["residues"]) == ["rice_straw", "wheat_straw"] and prof["conds"] == ["dry"]
    # a registered buyer is offered to farmers, ahead of demo buyers with the same score
    top = matching.match_buyers("rice_straw", 500, "dry", "Thanjavur", 2.0, path)
    real = [m for m in top if not m.buyer["is_demo"]]
    assert len(real) == 1 and real[0].score == 100
    # dropping a residue switches it off but keeps the row (old requests still resolve)
    db.save_buyer_profile(buyer["id"], "Mushroom Co", "mushroom_grower", "Thanjavur", "9123456780",
                          ["rice_straw"], ["dry", "wet"], 100, 5000, 3.0, path)
    assert db.get_buyer_profile(buyer["id"], path)["residues"] == ["rice_straw"]
    assert not [b for b in db.get_buyers_for("wheat_straw", path) if b["user_id"] == buyer["id"]]


def test_two_sided_flow_and_permissions():
    path = fresh_db()
    farmer = db.create_user("farmer", "Ravi", "9876543210", "Thanjavur", True, path)
    private = db.create_user("farmer", "Anu", "9876500000", "Thanjavur", False, path)
    b1 = db.create_user("buyer", "Buyer One", "9123456780", "Thanjavur", False, path)
    b2 = db.create_user("buyer", "Buyer Two", "9123456781", "Thanjavur", False, path)
    for b in (b1, b2):
        db.save_buyer_profile(b["id"], b["name"], "compost_maker", "Thanjavur", b["phone"],
                              ["rice_straw"], ["dry"], 0, 10000, 5.0, path)
    l1 = db.add_listing("Ravi", farmer["phone"], True, "rice_straw", 500, "dry", "Thanjavur", 2.0, path, user_id=farmer["id"])
    l2 = db.add_listing("Anu", private["phone"], False, "rice_straw", 800, "dry", "Thanjavur", None, path, user_id=private["id"])
    assert [r["id"] for r in db.listings_for_user(farmer["id"], path)] == [l1]

    # buyer sees only listings that fit, farmer requests the buyer row
    matches = matching.match_listings(db.get_buyer_profile(b1["id"], path), path)
    assert {m.listing["id"] for m in matches} == {l1, l2}
    b1_row = db.get_buyers_for("rice_straw", path)
    b1_id = next(b["id"] for b in b1_row if b["user_id"] == b1["id"])
    assert db.create_request(l1, b1_id, path) and db.create_request(l2, b1_id, path)
    inbox = db.requests_for_buyer_user(b1["id"], path)
    assert len(inbox) == 2 and db.requests_for_buyer_user(b2["id"], path) == []

    # only the owning buyer can decide a request
    rid = inbox[0]["id"]
    assert db.set_request_status(rid, "accepted", path, buyer_user_id=b2["id"]) is False
    assert db.requests_for_buyer_user(b1["id"], path)[0]["status"] == "pending"
    assert db.set_request_status(rid, "accepted", path, buyer_user_id=b1["id"]) is True

    # contact consent: Ravi shared his phone, Anu did not
    by_phone = {r["phone"]: r for r in db.requests_for_buyer_user(b1["id"], path)}
    assert by_phone[farmer["phone"]]["share_contact"] == 1
    assert by_phone[private["phone"]]["share_contact"] == 0
    # farmer sees the registered buyer's request status and contact
    mine = db.requests_for_listings([l1], path)
    assert mine[0]["buyer_name"] == "Buyer One" and mine[0]["is_demo"] == 0


def test_old_database_is_upgraded():
    import sqlite3
    path = fresh_db()
    conn = sqlite3.connect(path)  # strip the new columns to mimic a database from the first version
    conn.executescript("""
        ALTER TABLE buyers DROP COLUMN user_id;
        ALTER TABLE buyers DROP COLUMN active;
        ALTER TABLE listings DROP COLUMN user_id;
        DROP TABLE users;
    """)
    conn.commit()
    assert "active" not in {r[1] for r in conn.execute("PRAGMA table_info(buyers)")}
    conn.close()
    db.init_db(path)
    assert db.get_buyers_for("rice_straw", path)[0]["active"] == 1
    assert db.create_user("farmer", "A", "9876543210", "X", False, path) is not None
    assert db.add_listing("A", "9876543210", False, "rice_straw", 5, "dry", "X", None, path, user_id=1)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
