#!/usr/bin/env python3
"""Offline record-reconciliation tests for the two RFC publishers."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
import sys

sys.path.insert(0, str(SCRIPTS))
import cbrfc_forecast_publisher as cbrfc  # noqa: E402
import cnrfc_forecast_publisher as cnrfc  # noqa: E402
from rfc_publisher_utils import RFCProductError, write_json  # noqa: E402


def load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def bump_timestamp(value: str, minutes: int) -> str:
    parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    bumped = (parsed + timedelta(minutes=minutes)).isoformat(timespec="seconds")
    return bumped.replace("+00:00", "Z") if value.endswith("Z") else bumped


def signature(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


class PublisherFixture:
    module = None
    product_path = None

    def setUp(self) -> None:
        assert self.module is not None and self.product_path is not None
        self.baseline = load(REPO / self.product_path)

    def write_root(self, root: Path, payload: dict[str, object]) -> None:
        write_json(root / self.product_path, payload)

    def metadata(self, root: Path, payload: dict[str, object]) -> Path:
        path = root / "candidate-metadata.json"
        write_json(
            path,
            {
                "semantic_key": {
                    "type": self.module.SEMANTIC_TYPE,
                    "value": self.module.semantic_key(payload),
                }
            },
        )
        return path

    def reconcile(
        self,
        candidate_payload: dict[str, object],
        canonical_payload: dict[str, object] | None,
        *,
        metadata: bool = True,
    ) -> tuple[dict[str, object], dict[str, object] | None]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_root = root / "candidate"
            worktree = root / "worktree"
            self.write_root(candidate_root, candidate_payload)
            if canonical_payload is not None:
                self.write_root(worktree, canonical_payload)
            metadata_path = self.metadata(root, candidate_payload) if metadata else root / "missing.json"
            result_path = root / "result.json"
            env = {
                "BRIM_PUBLISH_CANDIDATE_ROOT": str(candidate_root),
                "BRIM_PUBLISH_WORKTREE": str(worktree),
                "BRIM_PUBLISH_METADATA": str(metadata_path),
                "BRIM_PUBLISH_RESULT": str(result_path),
                "BRIM_PUBLISH_PRODUCT_ID": self.module.PRODUCT_ID,
            }
            with mock.patch.dict(os.environ, env, clear=False):
                self.module.reconcile()
            result = load(result_path)
            final_path = worktree / self.product_path
            final = load(final_path) if final_path.exists() else None
            return result, final


class CNRFCPublisherTests(PublisherFixture, unittest.TestCase):
    module = cnrfc
    product_path = cnrfc.PRODUCT_PATH

    @staticmethod
    def advance_success(record: dict[str, object], minutes: int, label: str) -> None:
        anchor_name = (
            "source_data_updated_at"
            if record["product_type"] == "ten_day_streamflow_volume_accumulation"
            and record.get("source_data_updated_at")
            else "forecast_issued_at"
        )
        record[anchor_name] = bump_timestamp(str(record[anchor_name]), minutes)
        record["last_successful_retrieval_at"] = bump_timestamp(
            str(record["last_successful_retrieval_at"]), minutes
        )
        record["last_attempt_at"] = bump_timestamp(str(record["last_attempt_at"]), minutes)
        record["source_page_signature"] = signature(label)
        for state in record["metric_state"].values():
            state["source_issue_at"] = record.get("forecast_issued_at")
            state["source_data_updated_at"] = (
                record.get("source_data_updated_at")
                if record["product_type"] == "ten_day_streamflow_volume_accumulation"
                else None
            )

    @staticmethod
    def failure_from(
        record: dict[str, object], minutes: int, label: str, outcome: str = "fetch_failed"
    ) -> dict[str, object]:
        failure = copy.deepcopy(record)
        failure["attempt_outcome"] = outcome
        failure["failure_stage"] = cnrfc.FAILURE_STAGES[outcome]
        failure["last_attempt_at"] = bump_timestamp(str(record["last_attempt_at"]), minutes)
        failure["missing_reason"] = f"{outcome}: {label}"
        failure["diagnostic"] = copy.deepcopy(record["diagnostic"])
        failure["diagnostic"].update(
            {
                "error_class": outcome,
                "error_message": label,
                "failure_stage": cnrfc.FAILURE_STAGES[outcome],
            }
        )
        return cnrfc._carry_failure(failure, record)

    def rebuilt(self, payload: dict[str, object]) -> dict[str, object]:
        cnrfc.rebuild_aggregates(payload)
        return payload

    def test_all_records_newer(self) -> None:
        canonical = copy.deepcopy(self.baseline)
        candidate = copy.deepcopy(self.baseline)
        for index, record in enumerate(candidate["records"]):
            if record["attempt_outcome"] == "success":
                self.advance_success(record, 1, f"all-new-{index}")
            else:
                candidate["records"][index] = self.failure_from(record, 1, f"failure-{index}")
        candidate["generated_at"] = bump_timestamp(str(candidate["generated_at"]), 1)
        self.rebuilt(candidate)
        result, final = self.reconcile(candidate, canonical)
        self.assertEqual(result["decision"], "publish")
        self.assertEqual(result["candidate_state"], "new")
        self.assertEqual(len(final["records"]), 51)

    def test_subset_newer_remainder_same(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        self.advance_success(candidate["records"][0], 1, "one-new")
        candidate["generated_at"] = bump_timestamp(str(candidate["generated_at"]), 1)
        self.rebuilt(candidate)
        result, final = self.reconcile(candidate, self.baseline)
        self.assertEqual(result["decision"], "publish")
        self.assertEqual(final["records"][1], self.baseline["records"][1])

    def test_subset_newer_and_stale_candidate_preserves_fresh_main(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        canonical = copy.deepcopy(self.baseline)
        self.advance_success(candidate["records"][0], 2, "candidate-new")
        self.advance_success(canonical["records"][1], 2, "canonical-new")
        candidate["generated_at"] = bump_timestamp(str(candidate["generated_at"]), 3)
        canonical["generated_at"] = bump_timestamp(str(canonical["generated_at"]), 2)
        self.rebuilt(candidate)
        self.rebuilt(canonical)
        result, final = self.reconcile(candidate, canonical)
        self.assertEqual(result["decision"], "publish")
        self.assertEqual(final["records"][1], canonical["records"][1])

    def test_source_gap_carries_fresh_main_lkg(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        canonical = copy.deepcopy(self.baseline)
        event_record = copy.deepcopy(candidate["records"][0])
        self.advance_success(canonical["records"][0], 1, "fresh-main")
        candidate["records"][0] = self.failure_from(
            event_record, 2, "source gap", outcome="source_unavailable"
        )
        candidate["generated_at"] = bump_timestamp(str(candidate["generated_at"]), 2)
        canonical["generated_at"] = bump_timestamp(str(canonical["generated_at"]), 1)
        self.rebuilt(candidate)
        self.rebuilt(canonical)
        result, final = self.reconcile(candidate, canonical)
        record = final["records"][0]
        self.assertEqual(result["decision"], "publish")
        self.assertEqual(record["source_page_signature"], canonical["records"][0]["source_page_signature"])
        self.assertEqual(record["value_origin"], "last_known_good")
        self.assertEqual(record["attempt_outcome"], "source_unavailable")

    def test_expired_canonical_remains_expired_on_source_gap(self) -> None:
        record = self.baseline["records"][-1]
        failure = self.failure_from(record, 60, "still unavailable")
        carried = cnrfc._carry_failure(failure, record)
        self.assertEqual(carried["status"], "expired")
        self.assertEqual(carried["source_page_signature"], record["source_page_signature"])

    def test_older_candidate_is_stale(self) -> None:
        canonical = copy.deepcopy(self.baseline)
        self.advance_success(canonical["records"][0], 1, "fresh-canonical")
        selected, action = cnrfc.select_record(self.baseline["records"][0], canonical["records"][0])
        self.assertEqual(action, "stale")
        self.assertEqual(selected, canonical["records"][0])

    def test_same_issue_and_signature_is_same(self) -> None:
        selected, action = cnrfc.select_record(self.baseline["records"][0], self.baseline["records"][0])
        self.assertEqual(action, "same")
        self.assertEqual(selected, self.baseline["records"][0])

    def test_same_issue_legitimate_revised_signature_is_new(self) -> None:
        candidate = copy.deepcopy(self.baseline["records"][0])
        candidate["forecast_volume"] += 1
        candidate["source_page_signature"] = signature("legitimate-revision")
        candidate["last_successful_retrieval_at"] = bump_timestamp(
            str(candidate["last_successful_retrieval_at"]), 1
        )
        candidate["last_attempt_at"] = bump_timestamp(str(candidate["last_attempt_at"]), 1)
        _, action = cnrfc.select_record(candidate, self.baseline["records"][0])
        self.assertEqual(action, "new")

    def test_same_issue_same_signature_different_source_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.baseline["records"][0])
        candidate["forecast_volume"] += 1
        candidate["last_successful_retrieval_at"] = bump_timestamp(
            str(candidate["last_successful_retrieval_at"]), 1
        )
        candidate["last_attempt_at"] = bump_timestamp(str(candidate["last_attempt_at"]), 1)
        with self.assertRaisesRegex(RFCProductError, "same CNRFC source issue/signature"):
            cnrfc.select_record(candidate, self.baseline["records"][0])

    def test_malformed_candidate_record_is_rejected(self) -> None:
        payload = copy.deepcopy(self.baseline)
        del payload["records"][0]["forecast_volume"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_root(root, payload)
            with self.assertRaisesRegex(RFCProductError, "missing forecast_volume"):
                cnrfc.validate_product(root)

    def test_missing_candidate_metadata_is_rejected(self) -> None:
        with self.assertRaisesRegex(RFCProductError, "missing plain candidate metadata"):
            self.reconcile(self.baseline, self.baseline, metadata=False)

    def test_malformed_canonical_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_root, worktree = root / "candidate", root / "worktree"
            self.write_root(candidate_root, self.baseline)
            path = worktree / self.product_path
            path.parent.mkdir(parents=True)
            path.write_text("{bad", encoding="utf-8")
            metadata = self.metadata(root, self.baseline)
            env = {
                "BRIM_PUBLISH_CANDIDATE_ROOT": str(candidate_root),
                "BRIM_PUBLISH_WORKTREE": str(worktree),
                "BRIM_PUBLISH_METADATA": str(metadata),
                "BRIM_PUBLISH_RESULT": str(root / "result.json"),
                "BRIM_PUBLISH_PRODUCT_ID": cnrfc.PRODUCT_ID,
            }
            with mock.patch.dict(os.environ, env, clear=False):
                with self.assertRaisesRegex(RFCProductError, "unreadable CNRFC"):
                    cnrfc.reconcile()

    def test_unknown_and_duplicate_record_ids_are_rejected(self) -> None:
        for duplicate in (False, True):
            payload = copy.deepcopy(self.baseline)
            payload["records"][0]["forecast_key"] = (
                payload["records"][1]["forecast_key"] if duplicate else "CNRFC:UNKNOWN:WY_FNF"
            )
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.write_root(root, payload)
                with self.assertRaises(RFCProductError):
                    cnrfc.validate_product(root)

    def test_candidate_bootstrap_is_new(self) -> None:
        result, final = self.reconcile(self.baseline, None)
        self.assertEqual(result["candidate_state"], "new")
        self.assertEqual(final, self.baseline)


class CBRFCPublisherTests(PublisherFixture, unittest.TestCase):
    module = cbrfc
    product_path = cbrfc.PRODUCT_PATH

    @staticmethod
    def advance_success(record: dict[str, object], days: int, label: str) -> None:
        issue = datetime.fromisoformat(str(record["forecast_issue_date"])) + timedelta(days=days)
        record["forecast_issue_date"] = issue.date().isoformat()
        record["source_page_signature"] = signature(label)
        record["last_successful_retrieval_at"] = bump_timestamp(
            str(record["last_successful_retrieval_at"]), days * 24 * 60
        )
        record["last_attempt_at"] = bump_timestamp(str(record["last_attempt_at"]), days * 24 * 60)
        if record["product_type"] == "lake_mead_local_intervening_monthly_forecast":
            for item in record["monthly_forecasts"]:
                for state in item["metric_state"].values():
                    state["source_issue_date"] = record["forecast_issue_date"]
        else:
            for state in record["metric_state"].values():
                state["source_issue_date"] = record["forecast_issue_date"]

    @staticmethod
    def failure_from(record: dict[str, object], minutes: int, label: str) -> dict[str, object]:
        failure = copy.deepcopy(record)
        failure["attempt_outcome"] = "fetch_failed"
        failure["failure_stage"] = "fetch"
        failure["last_attempt_at"] = bump_timestamp(str(record["last_attempt_at"]), minutes)
        failure["missing_reason"] = f"fetch_failed: {label}"
        failure["diagnostic"] = copy.deepcopy(record["diagnostic"])
        failure["diagnostic"].update(
            {"error_class": "fetch_failed", "error_message": label, "failure_stage": "fetch"}
        )
        return cbrfc._carry_failure(failure, record)

    def rebuilt(self, payload: dict[str, object]) -> dict[str, object]:
        cbrfc.rebuild_aggregates(payload)
        return payload

    def test_all_records_newer(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        for index, record in enumerate(candidate["records"]):
            if record["attempt_outcome"] == "success":
                self.advance_success(record, 1, f"all-new-{index}")
            else:
                candidate["records"][index] = self.failure_from(record, 24 * 60, f"failure-{index}")
        candidate["generated_at"] = bump_timestamp(str(candidate["generated_at"]), 24 * 60)
        self.rebuilt(candidate)
        result, final = self.reconcile(candidate, self.baseline)
        self.assertEqual(result["decision"], "publish")
        self.assertEqual(len(final["records"]), 3)

    def test_partial_newer_remainder_same(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        self.advance_success(candidate["records"][1], 1, "partial")
        candidate["generated_at"] = bump_timestamp(str(candidate["generated_at"]), 24 * 60)
        self.rebuilt(candidate)
        result, final = self.reconcile(candidate, self.baseline)
        self.assertEqual(result["decision"], "publish")
        self.assertEqual(final["records"][2], self.baseline["records"][2])

    def test_stale_candidate_preserves_fresh_main(self) -> None:
        canonical = copy.deepcopy(self.baseline)
        self.advance_success(canonical["records"][1], 1, "fresh")
        selected, action = cbrfc.select_record(self.baseline["records"][1], canonical["records"][1])
        self.assertEqual(action, "stale")
        self.assertEqual(selected, canonical["records"][1])

    def test_source_gap_carries_fresh_main_lkg(self) -> None:
        candidate = copy.deepcopy(self.baseline)
        canonical = copy.deepcopy(self.baseline)
        event_record = copy.deepcopy(candidate["records"][1])
        self.advance_success(canonical["records"][1], 1, "fresh-main")
        candidate["records"][1] = self.failure_from(event_record, 2 * 24 * 60, "source gap")
        candidate["generated_at"] = bump_timestamp(str(candidate["generated_at"]), 2 * 24 * 60)
        canonical["generated_at"] = bump_timestamp(str(canonical["generated_at"]), 24 * 60)
        self.rebuilt(candidate)
        self.rebuilt(canonical)
        result, final = self.reconcile(candidate, canonical)
        record = final["records"][1]
        self.assertEqual(result["decision"], "publish")
        self.assertEqual(record["source_page_signature"], canonical["records"][1]["source_page_signature"])
        self.assertEqual(record["value_origin"], "last_known_good")

    def test_monthly_source_gap_uses_cbrfc_monthly_lkg_semantics(self) -> None:
        record = self.baseline["records"][2]
        failure = self.failure_from(record, 1, "monthly source gap")
        carried = cbrfc._carry_failure(failure, record)
        self.assertEqual(carried["attempt_outcome"], "fetch_failed")
        self.assertEqual(carried["status"], "stale_last_known_good")
        self.assertTrue(all(
            item["value_origin"] == "last_known_good"
            for item in carried["monthly_forecasts"]
        ))

    def test_expiry_is_preserved_during_failure_carry(self) -> None:
        record = self.baseline["records"][0]
        failure = self.failure_from(record, 60, "still unavailable")
        carried = cbrfc._carry_failure(failure, record)
        self.assertEqual(carried["status"], "expired")
        self.assertEqual(carried["source_page_signature"], record["source_page_signature"])

    def test_same_issue_and_signature_is_same(self) -> None:
        selected, action = cbrfc.select_record(self.baseline["records"][1], self.baseline["records"][1])
        self.assertEqual(action, "same")
        self.assertEqual(selected, self.baseline["records"][1])

    def test_same_issue_legitimate_revision_is_new(self) -> None:
        candidate = copy.deepcopy(self.baseline["records"][1])
        candidate["forecast_volume"] += 1
        candidate["source_page_signature"] = signature("cbrfc-revision")
        candidate["last_successful_retrieval_at"] = bump_timestamp(
            str(candidate["last_successful_retrieval_at"]), 1
        )
        candidate["last_attempt_at"] = bump_timestamp(str(candidate["last_attempt_at"]), 1)
        _, action = cbrfc.select_record(candidate, self.baseline["records"][1])
        self.assertEqual(action, "new")

    def test_same_issue_invalid_revision_is_rejected(self) -> None:
        candidate = copy.deepcopy(self.baseline["records"][1])
        candidate["forecast_volume"] += 1
        candidate["last_successful_retrieval_at"] = bump_timestamp(
            str(candidate["last_successful_retrieval_at"]), 1
        )
        candidate["last_attempt_at"] = bump_timestamp(str(candidate["last_attempt_at"]), 1)
        with self.assertRaisesRegex(RFCProductError, "same CBRFC issue/signature"):
            cbrfc.select_record(candidate, self.baseline["records"][1])

    def test_malformed_candidate_and_canonical_are_rejected(self) -> None:
        malformed = copy.deepcopy(self.baseline)
        del malformed["records"][1]["forecast_volume"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_root(root, malformed)
            with self.assertRaisesRegex(RFCProductError, "missing forecast_volume"):
                cbrfc.validate_product(root)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_root, worktree = root / "candidate", root / "worktree"
            self.write_root(candidate_root, self.baseline)
            path = worktree / self.product_path
            path.parent.mkdir(parents=True)
            path.write_text("[]", encoding="utf-8")
            metadata = self.metadata(root, self.baseline)
            env = {
                "BRIM_PUBLISH_CANDIDATE_ROOT": str(candidate_root),
                "BRIM_PUBLISH_WORKTREE": str(worktree),
                "BRIM_PUBLISH_METADATA": str(metadata),
                "BRIM_PUBLISH_RESULT": str(root / "result.json"),
                "BRIM_PUBLISH_PRODUCT_ID": cbrfc.PRODUCT_ID,
            }
            with mock.patch.dict(os.environ, env, clear=False):
                with self.assertRaisesRegex(RFCProductError, "root must be an object"):
                    cbrfc.reconcile()

    def test_unknown_and_duplicate_record_ids_are_rejected(self) -> None:
        for duplicate in (False, True):
            payload = copy.deepcopy(self.baseline)
            payload["records"][0]["forecast_key"] = (
                payload["records"][1]["forecast_key"] if duplicate else "CBRFC:UNKNOWN:BAD"
            )
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.write_root(root, payload)
                with self.assertRaises(RFCProductError):
                    cbrfc.validate_product(root)

    def test_missing_metadata_is_rejected(self) -> None:
        with self.assertRaisesRegex(RFCProductError, "missing plain candidate metadata"):
            self.reconcile(self.baseline, self.baseline, metadata=False)

    def test_bootstrap_is_new(self) -> None:
        result, final = self.reconcile(self.baseline, None)
        self.assertEqual(result["candidate_state"], "new")
        self.assertEqual(final, self.baseline)


class RFCSharedTransactionTests(unittest.TestCase):
    def test_both_workflows_use_the_bounded_shared_retry_primitive(self) -> None:
        shared = (SCRIPTS / "main_publisher.py").read_text(encoding="utf-8")
        self.assertIn("for attempt in (1, 2):", shared)
        self.assertIn("main advanced during both publication attempts", shared)
        for workflow, callback in (
            ("build-major-water-supply-basin-forecasts.yml", "scripts/cnrfc_forecast_publisher.py"),
            ("build-cbrfc-major-water-supply-forecasts.yml", "scripts/cbrfc_forecast_publisher.py"),
        ):
            text = (REPO / ".github/workflows" / workflow).read_text(encoding="utf-8")
            self.assertIn("scripts/main_publisher.py publish", text)
            self.assertEqual(text.count(f"--candidate-validator {callback}"), 1)
            self.assertEqual(text.count(f"--reconcile-callback {callback}"), 1)
            self.assertEqual(text.count(f"--staged-validator {callback}"), 1)


if __name__ == "__main__":
    unittest.main()
