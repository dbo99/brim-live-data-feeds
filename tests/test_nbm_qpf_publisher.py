#!/usr/bin/env python3
"""Contract and reconciliation tests for the public NBM QPF publisher."""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "nbm_qpf_publisher", REPO_ROOT / "scripts/nbm_qpf_publisher.py"
)
assert SPEC is not None and SPEC.loader is not None
qpf = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = qpf
SPEC.loader.exec_module(qpf)

# Locked 720x733 VP8L RGBA test raster from the Phase 1 research proof.  Every
# pixel is canonical transparent RGBA 0,0,0,0, so it exercises the real decoder
# without making the test fixture large.
DRY_WEBP = base64.b64decode(
    "UklGRgQDAABXRUJQVlA4TPcCAAAvzwK3EA8w8JM90/Mf8JCc7X/bNj8EfoRU0+k91DFH"
    "HW2nMKN4BB1T9JgYIQNkiIyAMXLECDziwCfV3Z/+gXtE/x24bRtG1GBcej/R2XtTsH"
    "WdzrUG9sRoV7E2WQLJcWWgsMsA1OzmLbUK9moJFOoBtUI99sOMztWFAjaC/eKKvzxW"
    "IqMeOwhb6rliWxuecL9unWujcM0+ck3BNNfpXHOwAayYPHNdmMpzxQDWcKUWPF0tUz"
    "17b7LtyjDWBmexYjNx29IJMI8VYuBKDVYDFiLXKpmCuE10mlEA8jtFomYJNEBtpWiB"
    "GupYT0RhCawicfN7m7K4vY2CRucsngK2ppNZjit6sIYrtSbrwM8mE1lv8rw32Quw2/"
    "YO3BOT5201W5g8L8DWdBlYM7B1sI8mz0yXdBQdV/JcubHY0FqskHU1MbVxWD0jdcjQ"
    "z2xDx5XBIpjI3oO90MWq1sHeg21XhgXYW120ynEVDxa4RpP9tBf92VusOK4BLOv8fc"
    "lxZbDBmyyYzAv7I3s1VNGllvuE1mkKNsf+3WlhsF4fqdQLmtm2wX6CjTtER7QA+yhJ"
    "H5jHa1V2H8FGZ7Gf9g7WFth0h0HctlQLpQDWgLWVoeP6AfYN7CtYbLnkdeZX8lw5cA"
    "3L4glMs6cZ7Lnd0zpMzz3yL+4x/BK2oRczus5iGXzop8nGANaYrOUqZB1XBkv9Puao"
    "7+c+Csqo6LEcmQLX3s9zFi9spTIMcOtQ2UsfqZw0QiUnLag8WJB6qkaxo+qUWnNFNg"
    "WqXszofllMvxwXeX6CbYMtTDaC/awJ8OcF2Ezn7JqDjbXBcZXa4C02gI/s9B/KccUA"
    "5sHHNV2AbQLWgnVc0WDw51VVShOuFLjy0Uv+yMSjl4OwDY3FismTApe8rmpsojPbHo"
    "C9qgy3dUGwBJYdeJwuCua54lHbOlLhyJ3X6IMRpuBjACsOzBvuBdiCaNip2CovAQcU"
    "taPBiRlc1v+sTObqQvJgwWIRTBOdhRTAVnSpQd8dVi7+QAAA"
)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def read_manifest(root: Path) -> dict[str, object]:
    return json.loads((root / qpf.MANIFEST_PATH).read_text(encoding="utf-8"))


def spatial_contract() -> dict[str, object]:
    return {
        "contract_id": "nbm_qpf_lossless_webp_v1",
        "media_type": "image/webp",
        "encoding": "lossless VP8L RGBA8 WebP",
        "crs": "EPSG:3857",
        "bounds_wgs84": copy.deepcopy(qpf.BOUNDS_WGS84),
        "extent_m": copy.deepcopy(qpf.EXTENT_3857),
        "image_width": qpf.IMAGE_WIDTH,
        "image_height": qpf.IMAGE_HEIGHT,
        "pixel_size_m": copy.deepcopy(qpf.PIXEL_SIZE_M),
        "row_order": "north_to_south",
        "column_order": "west_to_east",
        "pixel_is_area": True,
        "leaflet_bounds": [[30.0, -130.0], [44.5, -112.0]],
        "default_leaflet_opacity": 0.55,
    }


def make_cycle(root: Path, cycle: datetime, webp: bytes = DRY_WEBP) -> dict[str, object]:
    sha = hashlib.sha256(webp).hexdigest()
    targets = []
    for lead in qpf.SUPPORTED_LEADS:
        end = cycle + timedelta(hours=lead)
        start = end - timedelta(hours=qpf.ACCUMULATION_HOURS)
        relative = qpf.TARGET_ROOT / (
            f"nbm_qpf_{cycle.strftime('%Y%m%dT%H%M%SZ')}_f{lead:03d}_{sha[:12]}.webp"
        )
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(webp)
        targets.append(
            {
                "product_id": qpf.PRODUCT_ID,
                "source_id": qpf.SOURCE_ID,
                "cycle_utc": qpf._stamp(cycle),
                "lead_hours": lead,
                "lead_end_hours": lead,
                "accumulation_start_utc": qpf._stamp(start),
                "accumulation_end_utc": qpf._stamp(end),
                "valid_time_utc": qpf._stamp(end),
                "accumulation_hours": qpf.ACCUMULATION_HOURS,
                "source_parameter": "APCP",
                "source_level": "surface",
                "source_inventory_semantics": f"{lead - 6}-{lead} hour acc fcst",
                "native_units": "kg/m^2",
                "normalized_units": "mm",
                "display_units": "in",
                "image_path": relative.as_posix(),
                "image_media_type": "image/webp",
                "image_encoding": "lossless_vp8l_rgba8",
                "image_width": qpf.IMAGE_WIDTH,
                "image_height": qpf.IMAGE_HEIGHT,
                "bounds_wgs84": copy.deepcopy(qpf.BOUNDS_WGS84),
                "bytes": len(webp),
                "sha256": sha,
                "palette_id": qpf.PALETTE_ID,
                "palette_version": qpf.PALETTE_VERSION,
            }
        )
    return {
        "cycle_utc": qpf._stamp(cycle),
        "cycle_status": "complete",
        "cycle_max_qpf_in": 0.0,
        "legend_cap_in": 3,
        "legend_overflow": False,
        "target_count": 10,
        "complete_required_leads_hours": list(qpf.SUPPORTED_LEADS),
        "targets": targets,
    }


def make_candidate(root: Path, cycle: datetime) -> Path:
    cycle_value = make_cycle(root, cycle)
    write_json(
        root / qpf.MANIFEST_PATH,
        {
            "schema_version": qpf.SCHEMA_VERSION,
            "product_id": qpf.PRODUCT_ID,
            "generated_at_utc": "2026-08-18T01:23:45Z",
            "source": copy.deepcopy(qpf.SOURCE_CONTRACT),
            "palette": qpf._palette_contract(),
            "spatial_representation": spatial_contract(),
            "freshness": copy.deepcopy(qpf.FRESHNESS_CONTRACT),
            "retention_mode": "bootstrap",
            "current_cycle_utc": qpf._stamp(cycle),
            "previous_cycle_utc": None,
            "cycles": [cycle_value],
        },
    )
    return root


def make_r_candidate(public_candidate: Path, destination: Path) -> Path:
    manifest = read_manifest(public_candidate)
    cycle = copy.deepcopy(manifest["cycles"][0])
    for target in cycle["targets"]:
        relative = Path(target["image_path"])
        output = destination / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(public_candidate / relative, output)
    write_json(
        destination / qpf.R_CANDIDATE_MANIFEST,
        {
            "candidate_schema_version": "1.0.0",
            "candidate_kind": "offline_complete_cycle",
            "product_id": qpf.PRODUCT_ID,
            "publication_ready": False,
            "mutable_public_manifest_included": False,
            "source": copy.deepcopy(qpf.SOURCE_CONTRACT),
            "palette": qpf._palette_contract(),
            "spatial_representation": spatial_contract(),
            "cycle": cycle,
        },
    )
    write_json(
        destination / qpf.R_VALIDATION,
        {
            "validation_result": "passed",
            "complete_cycle": True,
            "source_record_count": 10,
            "target_count": 10,
            "numeric_reprojection_before_classification": True,
            "interpolation": "bilinear",
            "manifest_target_closure": True,
            "content_hashes_validated": True,
        },
    )
    (destination / qpf.R_PREFLIGHT).write_text(
        "cycle_utc,lead_hours\n"
        + "".join(
            f"{cycle['cycle_utc']},{lead}\n" for lead in qpf.SUPPORTED_LEADS
        ),
        encoding="utf-8",
    )
    return destination


class NbmQpfPublisherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="nbm-qpf-publisher-test-")
        self.root = Path(self.temporary.name)
        self.cycle0 = datetime(2026, 8, 18, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def candidate(self, name: str, hours: int = 0) -> Path:
        return make_candidate(self.root / name, self.cycle0 + timedelta(hours=hours))

    def mutate(self, candidate: Path, function) -> None:
        manifest = read_manifest(candidate)
        function(manifest)
        write_json(candidate / qpf.MANIFEST_PATH, manifest)

    def assert_rejected(self, candidate: Path) -> None:
        with self.assertRaises(qpf.ProductError):
            qpf.validate_candidate(candidate)

    def test_complete_candidate_and_semantic_key(self) -> None:
        candidate = self.candidate("candidate")
        state = qpf.validate_candidate(candidate)
        self.assertEqual(1, len(state.cycles))
        self.assertEqual(10, len(state.current.targets))
        first = qpf.semantic_key(candidate)
        self.mutate(candidate, lambda manifest: manifest.__setitem__(
            "generated_at_utc", "2026-08-18T02:34:56Z"
        ))
        self.assertEqual(first, qpf.semantic_key(candidate))

    def test_strict_public_validator_requires_two_seeded_cycles(self) -> None:
        candidate = self.candidate("candidate")
        with self.assertRaisesRegex(qpf.ProductError, "two-cycle seeding"):
            qpf.validate_product(candidate, allow_bootstrap=False)
        qpf.validate_product(candidate, allow_bootstrap=True)

    def test_first_publication_bootstraps_then_second_seeds_exactly_two_cycles(self) -> None:
        canonical = self.root / "canonical"
        state, _ = qpf.reconcile(self.candidate("cycle0"), canonical)
        self.assertEqual("new", state)
        bootstrap = qpf.validate_product(canonical, allow_bootstrap=True)
        self.assertEqual("bootstrap", bootstrap.manifest["retention_mode"])
        self.assertEqual(10, len(bootstrap.current.targets))

        state, _ = qpf.reconcile(self.candidate("cycle6", 6), canonical)
        self.assertEqual("new", state)
        steady = qpf.validate_product(canonical, allow_bootstrap=False)
        self.assertEqual([qpf._stamp(self.cycle0 + timedelta(hours=6)), qpf._stamp(self.cycle0)],
                         [cycle.entry["cycle_utc"] for cycle in steady.cycles])
        self.assertEqual(20, sum(len(cycle.targets) for cycle in steady.cycles))

    def test_third_cycle_rolls_over_and_prunes_old_previous(self) -> None:
        canonical = self.root / "canonical"
        qpf.reconcile(self.candidate("cycle0"), canonical)
        qpf.reconcile(self.candidate("cycle6", 6), canonical)
        oldest_paths = {
            Path(target["image_path"])
            for target in read_manifest(canonical)["cycles"][1]["targets"]
        }
        qpf.reconcile(self.candidate("cycle12", 12), canonical)
        state = qpf.validate_product(canonical, allow_bootstrap=False)
        self.assertEqual(
            ["2026-08-18T12:00:00Z", "2026-08-18T06:00:00Z"],
            [cycle.entry["cycle_utc"] for cycle in state.cycles],
        )
        self.assertTrue(all(not (canonical / path).exists() for path in oldest_paths))
        self.assertEqual(20, len(list((canonical / qpf.TARGET_ROOT).glob("*.webp"))))

    def test_new_same_and_stale_never_regress_fresh_canonical(self) -> None:
        canonical = self.root / "canonical"
        newest = self.candidate("cycle12", 12)
        self.assertEqual("new", qpf.reconcile(newest, canonical)[0])
        before = (canonical / qpf.MANIFEST_PATH).read_bytes()
        self.assertEqual("same", qpf.reconcile(newest, canonical)[0])
        self.assertEqual("stale", qpf.reconcile(self.candidate("cycle6", 6), canonical)[0])
        self.assertEqual(before, (canonical / qpf.MANIFEST_PATH).read_bytes())

    def test_same_cycle_conflicting_bytes_are_rejected(self) -> None:
        canonical = self.root / "canonical"
        original = self.candidate("original")
        qpf.reconcile(original, canonical)
        conflict = self.candidate("conflict")
        pam = self.root / "painted.pam"
        webp = self.root / "painted.webp"
        rgba = bytes.fromhex("d9f0d3") + b"\xff" + b"\x00\x00\x00\x00" * (
            qpf.IMAGE_WIDTH * qpf.IMAGE_HEIGHT - 1
        )
        pam.write_bytes(
            (
                "P7\nWIDTH 720\nHEIGHT 733\nDEPTH 4\nMAXVAL 255\n"
                "TUPLTYPE RGB_ALPHA\nENDHDR\n"
            ).encode("ascii")
            + rgba
        )
        subprocess.run(
            ["cwebp", "-quiet", "-lossless", "-exact", str(pam), "-o", str(webp)],
            check=True,
        )
        manifest = read_manifest(conflict)
        target = manifest["cycles"][0]["targets"][0]
        old_path = conflict / target["image_path"]
        payload = webp.read_bytes()
        sha = hashlib.sha256(payload).hexdigest()
        new_relative = qpf.TARGET_ROOT / (
            f"nbm_qpf_{self.cycle0.strftime('%Y%m%dT%H%M%SZ')}_f006_{sha[:12]}.webp"
        )
        old_path.unlink()
        (conflict / new_relative).write_bytes(payload)
        target["image_path"] = new_relative.as_posix()
        target["bytes"] = len(payload)
        target["sha256"] = sha
        write_json(conflict / qpf.MANIFEST_PATH, manifest)
        qpf.validate_candidate(conflict)
        with self.assertRaisesRegex(qpf.ProductError, "same-cycle"):
            qpf.reconcile(conflict, canonical)

    def test_existing_previous_content_is_reused_without_rewrite(self) -> None:
        canonical = self.root / "canonical"
        qpf.reconcile(self.candidate("cycle0"), canonical)
        existing = Path(read_manifest(canonical)["cycles"][0]["targets"][0]["image_path"])
        before = (canonical / existing).stat().st_mtime_ns
        qpf.reconcile(self.candidate("cycle6", 6), canonical)
        self.assertEqual(before, (canonical / existing).stat().st_mtime_ns)

    def test_missing_unexpected_and_product_sibling_files_are_rejected(self) -> None:
        for name, mutation in (
            ("missing", lambda root: (root / Path(read_manifest(root)["cycles"][0]["targets"][0]["image_path"])).unlink()),
            ("unexpected", lambda root: (root / qpf.TARGET_ROOT / "unexpected.webp").write_bytes(DRY_WEBP)),
            ("sibling", lambda root: (root / qpf.MANIFEST_PATH.parent / "stray.json").write_text("{}\n", encoding="utf-8")),
        ):
            with self.subTest(name=name):
                candidate = self.candidate(name)
                mutation(candidate)
                self.assert_rejected(candidate)

    def test_symlinked_target_and_product_root_are_rejected(self) -> None:
        candidate = self.candidate("target-symlink")
        target = candidate / Path(
            read_manifest(candidate)["cycles"][0]["targets"][0]["image_path"]
        )
        external = self.root / "external.webp"
        external.write_bytes(DRY_WEBP)
        target.unlink()
        target.symlink_to(external)
        self.assert_rejected(candidate)

        source = self.candidate("source")
        linked = self.root / "linked"
        (linked / "docs/data").mkdir(parents=True)
        (linked / "docs/data/nbm-qpf").symlink_to(
            source / qpf.MANIFEST_PATH.parent, target_is_directory=True
        )
        with self.assertRaisesRegex(qpf.ProductError, "symlink"):
            qpf.validate_candidate(linked)

    def test_hash_bytes_and_webp_structure_are_independently_enforced(self) -> None:
        byte_tamper = self.candidate("byte-tamper")
        path = byte_tamper / Path(read_manifest(byte_tamper)["cycles"][0]["targets"][0]["image_path"])
        path.write_bytes(path.read_bytes() + b"tamper")
        self.assert_rejected(byte_tamper)

        invalid_webp = self.candidate("invalid-webp")
        manifest = read_manifest(invalid_webp)
        target = manifest["cycles"][0]["targets"][0]
        old = invalid_webp / target["image_path"]
        payload = b"not a webp"
        sha = hashlib.sha256(payload).hexdigest()
        relative = qpf.TARGET_ROOT / (
            f"nbm_qpf_{self.cycle0.strftime('%Y%m%dT%H%M%SZ')}_f006_{sha[:12]}.webp"
        )
        old.unlink()
        (invalid_webp / relative).write_bytes(payload)
        target.update(image_path=relative.as_posix(), bytes=len(payload), sha256=sha)
        write_json(invalid_webp / qpf.MANIFEST_PATH, manifest)
        self.assert_rejected(invalid_webp)

        wrong_dimensions = self.candidate("wrong-webp-dimensions")
        pam = self.root / "wrong-dimensions.pam"
        webp = self.root / "wrong-dimensions.webp"
        pam.write_bytes(
            b"P7\nWIDTH 2\nHEIGHT 1\nDEPTH 4\nMAXVAL 255\n"
            b"TUPLTYPE RGB_ALPHA\nENDHDR\n"
            + bytes.fromhex("d9f0d3ff00000000")
        )
        subprocess.run(
            ["cwebp", "-quiet", "-lossless", "-exact", str(pam), "-o", str(webp)],
            check=True,
        )
        manifest = read_manifest(wrong_dimensions)
        target = manifest["cycles"][0]["targets"][0]
        (wrong_dimensions / target["image_path"]).unlink()
        payload = webp.read_bytes()
        sha = hashlib.sha256(payload).hexdigest()
        relative = qpf.TARGET_ROOT / (
            f"nbm_qpf_{self.cycle0.strftime('%Y%m%dT%H%M%SZ')}_f006_{sha[:12]}.webp"
        )
        (wrong_dimensions / relative).write_bytes(payload)
        target.update(image_path=relative.as_posix(), bytes=len(payload), sha256=sha)
        write_json(wrong_dimensions / qpf.MANIFEST_PATH, manifest)
        self.assert_rejected(wrong_dimensions)

    def test_locked_target_time_science_and_spatial_contracts_are_enforced(self) -> None:
        mutations = {
            "duplicate-lead": lambda m: m["cycles"][0]["targets"][1].__setitem__("lead_hours", 6),
            "wrong-lead-set": lambda m: m["cycles"][0]["targets"][-1].__setitem__("lead_hours", 66),
            "wrong-interval": lambda m: m["cycles"][0]["targets"][0].__setitem__("accumulation_hours", 3),
            "cross-cycle": lambda m: m["cycles"][0]["targets"][0].__setitem__("cycle_utc", "2026-08-18T06:00:00Z"),
            "target-bounds": lambda m: m["cycles"][0]["targets"][0].__setitem__("bounds_wgs84", [-129.0, 30.0, -112.0, 44.5]),
            "target-width": lambda m: m["cycles"][0]["targets"][0].__setitem__("image_width", 719),
            "filename-hash": lambda m: m["cycles"][0]["targets"][0].__setitem__("sha256", "0" * 64),
            "legend-cap": lambda m: m["cycles"][0].__setitem__("legend_cap_in", 4),
            "spatial": lambda m: m["spatial_representation"].__setitem__("image_height", 732),
            "palette": lambda m: m["palette"].__setitem__("palette_id", "wrong"),
        }
        for name, mutation in mutations.items():
            with self.subTest(name=name):
                candidate = self.candidate(name)
                self.mutate(candidate, mutation)
                self.assert_rejected(candidate)

    def test_unknown_manifest_cycle_and_target_fields_are_rejected(self) -> None:
        mutations = {
            "manifest": lambda m: m.__setitem__("unknown", True),
            "cycle": lambda m: m["cycles"][0].__setitem__("unknown", True),
            "target": lambda m: m["cycles"][0]["targets"][0].__setitem__("unknown", True),
        }
        for name, mutation in mutations.items():
            with self.subTest(name=name):
                candidate = self.candidate(name)
                self.mutate(candidate, mutation)
                self.assert_rejected(candidate)

    def test_incomplete_and_malformed_canonical_states_fail_closed(self) -> None:
        canonical = self.root / "canonical"
        qpf.reconcile(self.candidate("cycle0"), canonical)
        manifest = read_manifest(canonical)
        manifest["unexpected"] = True
        write_json(canonical / qpf.MANIFEST_PATH, manifest)
        with self.assertRaises(qpf.ProductError):
            qpf.reconcile(self.candidate("cycle6", 6), canonical)

    def test_r_candidate_is_revalidated_and_assembled_into_public_shape(self) -> None:
        source = self.candidate("public-source")
        r_candidate = make_r_candidate(source, self.root / "r-candidate")
        output = self.root / "assembled"
        state = qpf.assemble_candidate(r_candidate, output)
        self.assertEqual("bootstrap", state.manifest["retention_mode"])
        self.assertEqual(10, len(state.current.targets))
        self.assertEqual({qpf.MANIFEST_PATH, *{
            target.relative_path for target in state.current.targets
        }}, qpf._inventory(output))

        malformed = make_r_candidate(source, self.root / "malformed-r")
        validation = json.loads((malformed / qpf.R_VALIDATION).read_text(encoding="utf-8"))
        validation["manifest_target_closure"] = False
        write_json(malformed / qpf.R_VALIDATION, validation)
        with self.assertRaises(qpf.ProductError):
            qpf.assemble_candidate(malformed, self.root / "rejected-output")

    def test_callback_rejects_any_ownership_expansion(self) -> None:
        old = dict(qpf.os.environ)
        try:
            qpf.os.environ.update(
                {
                    "BRIM_PUBLISH_PRODUCT_ID": qpf.PRODUCT_ID,
                    "BRIM_PUBLISH_FIXED_PATHS": json.dumps([qpf.MANIFEST_PATH.as_posix()]),
                    "BRIM_PUBLISH_OWNED_ROOTS": json.dumps(["docs/data/nbm-qpf"]),
                }
            )
            with self.assertRaisesRegex(qpf.ProductError, "ownership"):
                qpf._callback_contract()
        finally:
            qpf.os.environ.clear()
            qpf.os.environ.update(old)

    def test_callback_validates_reconciles_and_validates_staged_tree(self) -> None:
        candidate = self.candidate("candidate")
        canonical = self.root / "canonical"
        metadata = self.root / "metadata.json"
        result = self.root / "result.json"
        write_json(
            metadata,
            {
                "semantic_key": {
                    "type": qpf.SEMANTIC_KEY_TYPE,
                    "value": qpf.semantic_key(candidate),
                }
            },
        )
        old = dict(qpf.os.environ)
        try:
            qpf.os.environ.update(
                {
                    "BRIM_PUBLISH_PRODUCT_ID": qpf.PRODUCT_ID,
                    "BRIM_PUBLISH_FIXED_PATHS": json.dumps(
                        [qpf.MANIFEST_PATH.as_posix()]
                    ),
                    "BRIM_PUBLISH_OWNED_ROOTS": json.dumps(
                        [qpf.TARGET_ROOT.as_posix()]
                    ),
                    "BRIM_PUBLISH_CANDIDATE_ROOT": str(candidate),
                    "BRIM_PUBLISH_WORKTREE": str(canonical),
                    "BRIM_PUBLISH_METADATA": str(metadata),
                    "BRIM_PUBLISH_RESULT": str(result),
                }
            )
            qpf.os.environ["BRIM_PUBLISH_PHASE"] = "validate-candidate"
            self.assertEqual(0, qpf._callback())
            qpf.os.environ["BRIM_PUBLISH_PHASE"] = "reconcile"
            self.assertEqual(0, qpf._callback())
            self.assertEqual("publish", json.loads(result.read_text())["decision"])
            qpf.os.environ["BRIM_PUBLISH_PHASE"] = "validate-staged"
            self.assertEqual(0, qpf._callback())
            qpf.os.environ["BRIM_PUBLISH_PHASE"] = "reconcile"
            self.assertEqual(0, qpf._callback())
            self.assertEqual("no-op", json.loads(result.read_text())["decision"])
        finally:
            qpf.os.environ.clear()
            qpf.os.environ.update(old)


if __name__ == "__main__":
    unittest.main()
