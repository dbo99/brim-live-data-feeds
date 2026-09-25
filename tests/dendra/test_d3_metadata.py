"""Synthetic provider-shaped metadata tests; no dispatch, sockets or sleeps.

Vocabulary term contents and scientific claims derive from the accepted saved
catalog. The synthetic outer vocabulary nesting is not a new provider capture.
"""
import copy
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

NETWORK_ATTEMPTS = []
SLEEP_ATTEMPTS = []


def deny_network(event, args):
    if event.startswith("socket."):
        NETWORK_ATTEMPTS.append(event)
        raise AssertionError("D3 metadata tests forbid sockets and DNS")


def deny_sleep(*args, **kwargs):
    SLEEP_ATTEMPTS.append(True)
    raise AssertionError("D3 metadata tests forbid real sleep")


# Deny before importing any repository module, including its transport imports.
sys.addaudithook(deny_network)
SLEEP_PATCH = patch.object(time, "sleep", deny_sleep)
SLEEP_PATCH.start()
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from dendra.history_acquisition.model import Inventory, INVENTORY_SHA256, metadata_view
from dendra.history_acquisition.safety import Hold, encode, digest
from dendra.history_acquisition.provider_metadata import (
    CATALOG_SHA256, DICTIONARY_SHA256, SELECTED, load_authority, validate_authority,
    parse_vocabulary, parse_station, parse_datastreams,
)

KNOWN = "63531a67a9b61453fa1ca4ed"
UNKNOWN_DEPTH = "5d8e42e72da5c3cc53f6531d"
NOW = "2026-09-25T21:00:00Z"
CATALOG = Path(__file__).resolve().parents[2] / "data/input/dendra/pilot_catalog.json"


def setUpModule():
    global INVENTORY, AUTHORITY
    INVENTORY = Inventory.load(os.environ["DENDRA_INVENTORY"], INVENTORY_SHA256)
    AUTHORITY = load_authority(INVENTORY, CATALOG)


def tearDownModule():
    SLEEP_PATCH.stop()
    if NETWORK_ATTEMPTS or SLEEP_ATTEMPTS:
        raise AssertionError("Forbidden network or sleep attempt occurred")


def station_record(station_id):
    return dict(_id=station_id, name="Synthetic public station", public_level=3,
                is_hidden=False, is_geo_protected=False, is_active=True,
                geo=dict(type="Point", coordinates=[-116.5, 34.5]),
                revision="synthetic-r1", updated_at="2026-09-25T20:00:00Z")


def stream_record(sid):
    scientific = AUTHORITY["scientific"][sid]
    return dict(_id=sid, station_id=SELECTED[sid], name="Synthetic public sensor",
                public_level=3, is_hidden=False, is_geo_protected=False,
                is_enabled=True, state="ready", updated_at="2026-09-25T20:00:00Z",
                terms=copy.deepcopy(scientific["source_terms"]),
                attributes=copy.deepcopy(scientific["source_attributes"]),
                datapoints_config=[dict(interval=scientific["cadence_seconds"] * 1000)])


def vocabulary_record():
    return dict(_id="dt-unit", terms=list(copy.deepcopy(AUTHORITY["dictionary_terms"]).values()))


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.sid = KNOWN
        self.identity = INVENTORY.identity(self.sid)
        self.station = station_record(SELECTED[self.sid])
        self.stream = stream_record(self.sid)
        self.vocabulary = vocabulary_record()

    def parse_station(self, record=None, **kwargs):
        options = dict(checked_at=NOW, now=NOW)
        options.update(kwargs)
        return parse_station(encode(self.station if record is None else record),
                             SELECTED[self.sid], **options)

    def parse(self, *, rows=None, station=None, vocabulary=None, **envelope):
        payload = dict(data=[self.stream] if rows is None else rows, limit=500, skip=0)
        payload.update(envelope)
        return parse_datastreams(encode(payload), self.identity,
                                 self.parse_station() if station is None else station,
                                 parse_vocabulary(encode(self.vocabulary), AUTHORITY) if vocabulary is None else vocabulary,
                                 AUTHORITY, checked_at=NOW, now=NOW)

    def test_authority_binds_saved_catalog_and_inventory(self):
        self.assertEqual(AUTHORITY["catalog_sha256"], CATALOG_SHA256)
        self.assertEqual(AUTHORITY["dictionary_sha256"], DICTIONARY_SHA256)
        self.assertEqual(set(AUTHORITY["scientific"]), set(SELECTED))
        self.assertEqual(AUTHORITY["scientific"][KNOWN]["cadence_seconds"], 3600)
        self.assertEqual(AUTHORITY["scientific"][UNKNOWN_DEPTH]["cadence_seconds"], 600)

    def test_authority_mutation_holds(self):
        authority = copy.deepcopy(AUTHORITY)
        authority["scientific"][KNOWN]["cadence_seconds"] = 600
        authority["metadata_bindings"][KNOWN] = digest(authority["scientific"][KNOWN])
        with self.assertRaises(Hold):
            parse_vocabulary(encode(self.vocabulary), authority)

    def test_authority_exact_keys_inventory_and_selection(self):
        for field in ("extra", "inventory", "selection"):
            authority = copy.deepcopy(AUTHORITY)
            if field == "extra":
                authority["unexpected"] = True
            elif field == "inventory":
                authority["inventory_sha256"] = "a" * 64
            else:
                authority["scientific"].pop(KNOWN)
            with self.subTest(field=field), self.assertRaises(Hold):
                validate_authority(authority)

    def test_vocabulary_exact_terms_and_nested_shape(self):
        self.vocabulary["terms"] = {"children": [{"branch": term} for term in self.vocabulary["terms"]]}
        result = parse_vocabulary(encode(self.vocabulary), AUTHORITY)
        self.assertEqual(result["terms"], AUTHORITY["dictionary_terms"])

    def test_vocabulary_wrong_identity_holds(self):
        self.vocabulary["_id"] = "other"
        with self.assertRaises(Hold):
            parse_vocabulary(encode(self.vocabulary), AUTHORITY)

    def test_vocabulary_wrong_semantics_holds(self):
        self.vocabulary["terms"][0]["abbreviation"] = "fraction"
        with self.assertRaises(Hold):
            parse_vocabulary(encode(self.vocabulary), AUTHORITY)

    def test_vocabulary_duplicate_selected_label_holds(self):
        self.vocabulary["terms"].append(copy.deepcopy(self.vocabulary["terms"][0]))
        with self.assertRaises(Hold):
            parse_vocabulary(encode(self.vocabulary), AUTHORITY)

    def test_vocabulary_missing_selected_term_holds(self):
        self.vocabulary["terms"].pop()
        with self.assertRaises(Hold):
            parse_vocabulary(encode(self.vocabulary), AUTHORITY)

    def test_vocabulary_extra_claim_inside_selected_term_holds(self):
        self.vocabulary["terms"][0]["scale"] = 100
        with self.assertRaises(Hold):
            parse_vocabulary(encode(self.vocabulary), AUTHORITY)

    def test_vocabulary_unselected_terms_and_unrelated_fields_not_retained(self):
        self.vocabulary["terms"].append(dict(label="SyntheticOtherUnit", abbreviation="x"))
        self.vocabulary["unrestricted"] = {"header": "synthetic untrusted text"}
        result = parse_vocabulary(encode(self.vocabulary), AUTHORITY)
        self.assertEqual(set(result), {"dictionary_sha256", "terms"})
        self.assertEqual(set(result["terms"]), {"Percent", "VolumetricWaterContent"})

    def test_station_public_shape_passes_and_sanitizes(self):
        self.station["unrestricted"] = {"headers": "synthetic untrusted text"}
        parsed = self.parse_station()
        self.assertEqual(parsed["exact_id"], SELECTED[KNOWN])
        self.assertEqual(parsed["geometry"], [-116.5, 34.5])
        self.assertNotIn("unrestricted", parsed)

    def test_station_resolved_access_shape_passes(self):
        self.station.pop("public_level")
        self.station["access_levels_resolved"] = {"public_level": 3}
        self.assertEqual(self.parse_station()["public_level"], 3)

    def test_station_conflicting_access_holds(self):
        self.station["access_levels_resolved"] = {"public_level": 1}
        with self.assertRaises(Hold):
            self.parse_station()

    def test_station_identity_mismatch_holds(self):
        self.station["_id"] = "a" * 24
        with self.assertRaises(Hold):
            self.parse_station()

    def test_station_private_hidden_unknown_or_deleted_holds(self):
        for update in (dict(public_level=1), dict(is_hidden=True), dict(is_hidden=None),
                       dict(public_level=True), dict(is_deleted=True), dict(deleted_at=NOW),
                       dict(state="deleted")):
            with self.subTest(update=update), self.assertRaises(Hold):
                self.parse_station({**self.station, **update})

    def test_station_error_object_is_not_absence(self):
        with self.assertRaises(Hold):
            self.parse_station(dict(code=404, message="Synthetic absent response"))

    def test_protected_or_unknown_geometry_omitted(self):
        for protection in (True, None):
            value = {**self.station, "is_geo_protected": protection,
                     "geo": {"arbitrary_private_geometry": "not retained"}}
            self.assertIsNone(self.parse_station(value)["geometry"])

    def test_bad_public_geometry_holds(self):
        self.station["geo"]["coordinates"] = [181, 35]
        with self.assertRaises(Hold):
            self.parse_station()

    def test_station_stale_future_or_malformed_check_holds(self):
        for checked in ("2026-09-24T20:59:59Z", "2026-09-25T21:00:01Z", "not-a-timestamp"):
            with self.subTest(checked=checked), self.assertRaises(Hold):
                self.parse_station(checked_at=checked)

    def test_metadata_update_timestamp_is_validated(self):
        self.station["updated_at"] = "2026-02-30T00:00:00Z"
        with self.assertRaises(Hold):
            self.parse_station()

    def test_station_inactive_is_represented(self):
        self.station["is_active"] = False
        self.assertEqual(self.parse_station()["activity"], "inactive/ended")

    def test_both_scientific_identities_match_metadata_view(self):
        for sid in SELECTED:
            self.sid = sid
            self.identity = INVENTORY.identity(sid)
            self.station = station_record(SELECTED[sid])
            self.stream = stream_record(sid)
            result = self.parse()
            view = metadata_view(self.identity, result, checked_at=NOW, now=NOW,
                                 scientific_sha256=AUTHORITY["metadata_bindings"][sid],
                                 dictionary_sha256=AUTHORITY["dictionary_sha256"])
            self.assertTrue(view["raw_eligible"])
            self.assertEqual(view["identity"], self.identity)

    def test_descriptive_rename_preserves_scientific_binding(self):
        before = self.parse()
        self.stream["name"] = "Synthetic renamed public sensor"
        self.station["name"] = "Synthetic renamed station"
        after = self.parse()
        self.assertEqual(before["scientific_sha256"], after["scientific_sha256"])
        self.assertNotEqual(before["display_name"], after["display_name"])

    def test_stream_association_or_identity_holds(self):
        for update in (dict(station_id="a" * 24), dict(_id="a" * 24), dict(_id="bad")):
            with self.subTest(update=update), self.assertRaises(Hold):
                self.parse(rows=[{**self.stream, **update}])

    def test_stream_private_hidden_missing_public_holds(self):
        for update in (dict(public_level=1), dict(is_hidden=True), dict(is_hidden=None), dict(is_deleted=True)):
            with self.subTest(update=update), self.assertRaises(Hold):
                self.parse(rows=[{**self.stream, **update}])

    def test_empty_selected_stream_list_holds(self):
        with self.assertRaises(Hold):
            self.parse(rows=[], total=0)

    def test_full_unknown_limit_incomplete_offset_or_total_holds(self):
        for update in (dict(limit=1), dict(limit=None), dict(limit=0), dict(limit=True),
                       dict(limit=501), dict(total=2), dict(total=0), dict(total=True),
                       dict(skip=1), dict(skip=False)):
            with self.subTest(update=update), self.assertRaises(Hold):
                self.parse(**update)

    def test_duplicate_list_id_holds(self):
        with self.assertRaises(Hold):
            self.parse(rows=[self.stream, copy.deepcopy(self.stream)])

    def test_unselected_private_objects_only_id_diagnostic(self):
        extra = dict(_id="a" * 24, station_id=SELECTED[KNOWN], is_hidden=True,
                     name="Synthetic private name", geo=dict(coordinates=[1, 2]))
        parsed = self.parse(rows=[self.stream, extra])
        self.assertEqual(parsed["unexpected_ids"], ["a" * 24])
        self.assertNotIn("Synthetic private name", encode(parsed).decode())

    def test_stream_protection_suppresses_station_geometry(self):
        self.stream["is_geo_protected"] = True
        self.stream["geo"] = dict(coordinates=[1, 2])
        self.assertIsNone(self.parse()["geometry"])

    def test_missing_stream_protection_suppresses_geometry(self):
        self.stream.pop("is_geo_protected")
        self.assertIsNone(self.parse()["geometry"])

    def test_inactive_and_ended_identity_preserved(self):
        self.stream["is_enabled"] = False
        self.stream["datapoints_config"][0]["ends_before"] = "2025-01-01T00:00:00Z"
        parsed = self.parse()
        self.assertEqual(parsed["activity"], "inactive/ended")
        self.assertEqual(parsed["ended_at"], "2025-01-01T00:00:00Z")
        self.assertEqual(parsed["stream_id"], KNOWN)

    def test_explicit_inactive_state_is_represented(self):
        self.stream.pop("is_enabled")
        self.stream["state"] = "ended"
        self.assertEqual(self.parse()["activity"], "inactive/ended")

    def test_invalid_station_overlay_geometry_holds(self):
        station = self.parse_station()
        station["geometry"] = {"unrestricted": "synthetic value"}
        with self.assertRaises(Hold):
            self.parse(station=station)

    def test_science_term_attribute_or_cadence_change_holds(self):
        for field in ("terms", "attributes", "datapoints_config"):
            stream = copy.deepcopy(self.stream)
            if field == "terms":
                stream[field]["ds"]["Aggregate"] = "Instantaneous"
            elif field == "attributes":
                stream[field]["depth"]["value"] = 600
            else:
                stream[field][0]["interval"] = 600000
            with self.subTest(field=field), self.assertRaises(Hold):
                self.parse(rows=[stream])

    def test_ambiguous_or_missing_cadence_holds(self):
        for configs in (None, [], [{"interval": 0}], [{"interval": True}],
                        [{"interval": 3600000}, {"interval": 3600000}]):
            with self.subTest(configs=configs), self.assertRaises(Hold):
                self.parse(rows=[{**self.stream, "datapoints_config": configs}])

    def test_identity_cannot_be_rewritten_by_caller(self):
        self.identity["depth_cm"] = 60
        with self.assertRaises(Hold):
            self.parse()

    def test_station_overlay_requires_freshness_again(self):
        station = self.parse_station()
        station["checked_at"] = "2026-09-20T21:00:00Z"
        with self.assertRaises(Hold):
            self.parse(station=station)

    def test_dictionary_overlay_cannot_be_substituted(self):
        with self.assertRaises(Hold):
            self.parse(vocabulary=dict(terms={}, dictionary_sha256=DICTIONARY_SHA256))

    def test_metadata_duplicate_json_key_rejected(self):
        with self.assertRaises(Hold):
            parse_station(b'{"_id":"a","_id":"b"}', SELECTED[KNOWN], checked_at=NOW, now=NOW)

    def test_metadata_body_and_traversal_bounds(self):
        with self.assertRaises(Hold):
            parse_vocabulary(b" " * (8 * 1024**2 + 1), AUTHORITY)
        value = vocabulary_record()
        for _ in range(17):
            value = dict(nested=value)
        with self.assertRaises(Hold):
            parse_vocabulary(encode(value), AUTHORITY)

    def test_no_network_or_sleep_was_attempted(self):
        self.assertEqual(NETWORK_ATTEMPTS, [])
        self.assertEqual(SLEEP_ATTEMPTS, [])


if __name__ == "__main__":
    unittest.main()
