#!/usr/bin/env python3
"""Authoritative main-writer publication-concurrency safety checks."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"
CDEC = WORKFLOWS / "build-cdec-reservoir-feed.yml"
DELTA_OPS = WORKFLOWS / "build-delta-ops-daily-summary.yml"
SCAN = WORKFLOWS / "build-scan-soil-moisture-latest.yml"
COCORAHS = WORKFLOWS / "build-cocorahs-daily-precip-feed.yml"
STREAMFLOW = WORKFLOWS / "build-usgs-streamflow-latest-ca.yml"
GROUNDWATER = WORKFLOWS / "build-usgs-groundwater-latest-ca.yml"
ASOS_AWOS = WORKFLOWS / "build-asos-awos-wind-latest.yml"
SNOW_PILLOW = WORKFLOWS / "build-snow-pillow-latest.yml"
CNRFC = WORKFLOWS / "build-major-water-supply-basin-forecasts.yml"
CBRFC = WORKFLOWS / "build-cbrfc-major-water-supply-forecasts.yml"
GFS = WORKFLOWS / "build-gfs-wind-latest.yml"
HRRR = WORKFLOWS / "build-hrrr-wind-latest.yml"
NBM_WIND = WORKFLOWS / "build-nbm-wind-guidance-latest.yml"
NBM_QPF = WORKFLOWS / "build-nbm-qpf.yml"
WINTER_STORM = WORKFLOWS / "build-winter-storm-levels.yml"
MAIN_WRITERS = (
    "build-asos-awos-wind-latest.yml",
    "build-cbrfc-major-water-supply-forecasts.yml",
    "build-cocorahs-daily-precip-feed.yml",
    "build-cdec-reservoir-feed.yml",
    "build-delta-ops-daily-summary.yml",
    "build-gfs-wind-latest.yml",
    "build-hrrr-wind-latest.yml",
    "build-major-water-supply-basin-forecasts.yml",
    "build-nbm-wind-guidance-latest.yml",
    "build-nbm-qpf.yml",
    "build-scan-soil-moisture-latest.yml",
    "build-snow-pillow-latest.yml",
    "build-usgs-groundwater-latest-ca.yml",
    "build-usgs-streamflow-latest-ca.yml",
    "build-winter-storm-levels.yml",
)
MAIN_WRITER_PATHS = tuple(WORKFLOWS / filename for filename in MAIN_WRITERS)
PRODUCT_PATHS = {
    CDEC: {
        "docs/data/cdec_reservoir_latest.geojson",
        "docs/data/cdec_reservoir_latest_summary.json",
    },
    DELTA_OPS: {
        "docs/data/delta_ops_daily_summary.json",
        "docs/data/delta_ops_daily_summary_features.geojson",
        "docs/data/delta_ops_daily_summary_summary.json",
        "docs/data/delta_ops_x2_reference.geojson",
    },
    SCAN: {
        "docs/data/scan_soil_moisture_latest.geojson",
        "docs/data/scan_soil_moisture_latest_summary.json",
        "docs/data/scan_soil_moisture_current_wy_trace.csv",
        "docs/data/scan_soil_moisture_current_wy_trace_summary.json",
        "docs/data/scan_depth_style.csv",
    },
    COCORAHS: {
        "docs/data/cocorahs_daily_precip_ca_latest.geojson",
        "docs/data/cocorahs_daily_precip_ca_latest_summary.json",
        "docs/data/cocorahs_daily_precip_conus_latest.geojson",
        "docs/data/cocorahs_daily_precip_conus_latest_summary.json",
    },
    STREAMFLOW: {
        "docs/data/usgs_streamflow_latest_ca.geojson",
        "docs/data/usgs_streamflow_latest_ca_summary.json",
    },
    GROUNDWATER: {
        "docs/data/usgs_groundwater_latest_ca.geojson",
        "docs/data/usgs_groundwater_latest_ca_summary.json",
    },
    ASOS_AWOS: {
        "docs/data/wind/asos_awos_wind_latest.geojson",
        "docs/data/wind/asos_awos_wind_latest_summary.json",
        "docs/data/wind/asos_awos_wind_feed_manifest.json",
    },
    SNOW_PILLOW: {
        "docs/data/snow_pillow_latest.geojson",
        "docs/data/snow_pillow_latest_summary.json",
        "docs/data/snow_pillow_current_wy_trace.csv",
        "docs/data/snow_pillow_current_wy_trace_summary.json",
    },
    CNRFC: {
        "docs/data/major_water_supply_basin_forecasts.json",
    },
    CBRFC: {
        "docs/data/cbrfc_major_water_supply_forecasts.json",
    },
}


def job_level_env_blocks(text: str) -> list[str]:
    """Return job-level env blocks without mistaking step env for job env."""
    lines = text.splitlines()
    blocks: list[str] = []
    for index, line in enumerate(lines):
        if line != "    env:":
            continue
        block = [line]
        for following in lines[index + 1 :]:
            if following and len(following) - len(following.lstrip()) <= 4:
                break
            block.append(following)
        blocks.append("\n".join(block))
    return blocks


class DeltaCanaryWorkflowTests(unittest.TestCase):
    """Exercise decisions and shell programs extracted from the actual workflow."""

    @classmethod
    def setUpClass(cls):
        cls.text = DELTA_OPS.read_text(encoding="utf-8")
        prepare = cls.text.split("\n  publish-to-main:\n", 1)[0]
        cls.steps = {}
        for block in prepare.split("      - name: ")[1:]:
            name, rest = block.split("\n", 1)
            cls.steps[name] = rest

    def expression(self, expression, context):
        import ast
        def token(match):
            text = match[0]
            if text.startswith("'"):
                return text
            if text in {"true", "false"}:
                return str(text == "true")
            return repr(context.get(text, ""))
        value = re.sub(r"'[^']*'|(?:github|inputs|steps|needs)\.[A-Za-z0-9_.-]+|\b(?:true|false)\b", token, expression)
        value = value.removeprefix("${{").removesuffix("}}").strip()
        value = value.replace("&&", " and ").replace("||", " or ")
        value = re.sub(r"!(?!=)", " not ", value).strip()
        tree = ast.parse(value, mode="eval")
        allowed = (ast.Expression, ast.Constant, ast.BoolOp, ast.UnaryOp,
                   ast.Compare, ast.And, ast.Or, ast.Not, ast.Eq, ast.NotEq)
        self.assertTrue(all(isinstance(node, allowed) for node in ast.walk(tree)))
        return eval(compile(tree, "<workflow-condition>", "eval"), {"__builtins__": {}})

    def field(self, step, key):
        return re.search(r"^        " + re.escape(key) + r": (.+)$", self.steps[step], re.M)[1]

    def shell(self, step):
        import textwrap
        return textwrap.dedent(self.steps[step].split("        run: |\n", 1)[1]).rstrip() + "\n"

    def execute(self, step, root, env=None, replacements=None):
        import os, subprocess, sys
        script = self.shell(step)
        for old, new in (replacements or {}).items():
            script = script.replace(old, new)
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                           PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
                           RUNNER_TEMP=str(root), GITHUB_OUTPUT=str(root / "output"))
        for key in list(environment):
            if key.startswith(("DELTA_OPS_", "BRIM_PUBLISH_")):
                environment.pop(key)
        environment.update(env or {})
        return subprocess.run(["bash", "-e", "-o", "pipefail", "-c", script], cwd=root,
                              env=environment, capture_output=True, text=True, timeout=30)

    def test_delta_yaml_defaults_and_independent_publisher_matrix(self):
        self.assertRegex(self.text, r"gmail_canary:\n(?:.*\n){1}        type: boolean\n        required: false\n        default: false")
        self.assertRegex(self.text, r"canary_source_sha:\n(?:.*\n){1}        type: string\n        required: false\n        default: \"\"")
        self.assertEqual(re.findall(r'- cron: "([^"]+)"', self.text),
                         ["41 15 * * *", "11,41 16 * * *", "11,41 17 * * *"])
        self.assertNotRegex(self.text, r"(?m)^  (?:push|pull_request):")
        output = re.search(r"^      should_publish: (.+)$", self.text, re.M)[1]
        publish = self.text.split("\n  publish-to-main:\n", 1)[1].splitlines()[0].strip()[4:]
        checkout = self.field("Check out selected source", "uses")
        self.assertEqual(checkout, "actions/checkout@v6")
        checkout_ref = re.search(r"^          ref: (.+)$", self.steps["Check out selected source"], re.M)[1]
        request = re.search(r"^          CANARY_REQUESTED: (.+)$", self.steps["Validate Delta mode and immutable canary source"], re.M)[1]
        for event in ["schedule", "workflow_dispatch"]:
            for flag in ["", "false", "true"]:
                for canary in [None, False, True, "false", "true"]:
                    for ref in ["refs/heads/main", "refs/heads/feature"]:
                        context = {"github.event_name": event, "inputs.gmail_canary": canary,
                                   "github.ref": ref, "github.sha": "a" * 40}
                        requested = event == "workflow_dispatch" and canary is True
                        self.assertEqual(self.expression(request, context), requested)
                        self.assertEqual(self.expression(checkout_ref, context), "a" * 40 if requested else ref)
                        for run in ["false", "true"]:
                            context.update({"steps.delta_ops_mode.outputs.is_canary": str(requested).lower(),
                                            "steps.delta_ops_precheck.outputs.should_run": run,
                                            "needs.prepare-candidate.outputs.should_publish": "true"})
                            self.assertEqual(self.expression(output, context), not requested and run == "true")
                            # A corrupted upstream true must never admit the canary publisher.
                            self.assertEqual(self.expression(publish, context), ref == "refs/heads/main" and not requested)
                        use = ref == "refs/heads/main" and (requested or flag == "true")
                        context.update({"steps.delta_ops_mode.outputs.use_gmail": str(use).lower(),
                                        "steps.delta_ops_precheck.outputs.should_run": "true"})
                        for name in ["Build Delta Ops through optional Gmail reader", "Prepare optional Delta Gmail verification dependencies"]:
                            self.assertEqual(self.expression(self.field(name, "if"), context), use)
                        for name in ["Build latest Delta Ops daily summary GeoJSON", "Validate and describe Delta Ops candidate artifact"]:
                            self.assertEqual(self.expression(self.field(name, "if"), context), not use)

    def test_delta_actual_mode_shell_rejects_wrong_identity_before_secret_steps(self):
        import subprocess, tempfile
        step = "Validate Delta mode and immutable canary source"
        with tempfile.TemporaryDirectory(prefix="delta-mode-") as folder:
            root = Path(folder)
            subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
            subprocess.run(["git", "-c", "user.name=Synthetic Fixture", "-c", "user.email=fixture@example.invalid",
                            "commit", "--allow-empty", "-qm", "synthetic"], cwd=root, check=True, capture_output=True)
            sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
            base = {"CANARY_REQUESTED": "true", "CANARY_SOURCE_SHA": sha, "SOURCE_EVENT_SHA": sha,
                    "SOURCE_REF": "refs/heads/main", "EMAIL_ENABLED": "false"}
            for change in [{}, {"CANARY_SOURCE_SHA": ""}, {"CANARY_SOURCE_SHA": "a" * 39},
                           {"CANARY_SOURCE_SHA": "$(touch should-not-exist)"},
                           {"SOURCE_REF": "refs/heads/feature"}, {"CANARY_SOURCE_SHA": "b" * 40},
                           {"CANARY_SOURCE_SHA": "b" * 40, "SOURCE_EVENT_SHA": "b" * 40}]:
                (root / "output").unlink(missing_ok=True)
                result = self.execute(step, root, dict(base, **change))
                self.assertEqual(result.returncode == 0, not change)
                self.assertNotIn("$(touch", result.stdout + result.stderr)
                if not change:
                    self.assertIn("is_canary=true\nuse_gmail=true\ncheckout_sha=" + sha, (root / "output").read_text())
                else:
                    self.assertEqual(result.stderr.strip(), "DELTA_CANARY_SOURCE_REJECTED")
                    self.assertFalse((root / "output").exists())
                self.assertFalse((root / "should-not-exist").exists())
            for flag in ["", "false", "true"]:
                result = self.execute(step, root, dict(base, CANARY_REQUESTED="false", CANARY_SOURCE_SHA="", EMAIL_ENABLED=flag))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("use_gmail=" + str(flag == "true").lower(), (root / "output").read_text().splitlines()[-2])
        names = list(self.steps)
        self.assertLess(names.index(step), names.index("Build Delta Ops through optional Gmail reader"))
        self.assertNotIn("${{", self.shell(step))

    def test_delta_actual_precheck_current_day_and_manual_bypass(self):
        import json, tempfile
        from datetime import datetime
        from zoneinfo import ZoneInfo
        step = "Check if Delta Ops feed is already current"
        with tempfile.TemporaryDirectory(prefix="delta-precheck-") as folder:
            root = Path(folder); summary = root / "docs/data/delta_ops_daily_summary_summary.json"
            summary.parent.mkdir(parents=True)
            summary.write_text(json.dumps({"report_date": datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()}))
            for event, expected in [("schedule", "false"), ("workflow_dispatch", "true")]:
                (root / "output").unlink(missing_ok=True)
                result = self.execute(step, root, replacements={"${{ github.event_name }}": event})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("should_run=" + expected, (root / "output").read_text())

    def test_delta_reader_and_dependency_failure_shells_keep_secrets_isolated(self):
        import os, sys, tempfile
        with tempfile.TemporaryDirectory(prefix="delta-shell-") as folder:
            root = Path(folder); bin_dir = root / "bin"; bin_dir.mkdir()
            mock = bin_dir / "python3"
            mock.write_text("#!" + sys.executable + "\nimport sys\nraise SystemExit(1)\n")
            mock.chmod(0o700)
            env = {"PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
                   "DELTA_OPS_GMAIL_CLIENT_SECRET": "SYNTHETIC_DO_NOT_DISCLOSE"}
            dependency = self.execute("Prepare optional Delta Gmail verification dependencies", root, env)
            self.assertEqual(dependency.returncode, 0)
            reader = self.execute("Build Delta Ops through optional Gmail reader", root, env)
            self.assertNotEqual(reader.returncode, 0)
            self.assertNotIn("SYNTHETIC_DO_NOT_DISCLOSE", dependency.stdout + dependency.stderr + reader.stdout + reader.stderr)
        for name, block in self.steps.items():
            if name != "Build Delta Ops through optional Gmail reader":
                self.assertNotIn("secrets.DELTA_OPS_GMAIL_", block)
        self.assertNotIn("continue-on-error", self.steps["Build Delta Ops through optional Gmail reader"])
        upload = self.steps["Upload validated Delta Ops publication candidate"]
        self.assertNotIn("always()", upload)
        self.assertIn("retention-days: 2", upload)
        self.assertIn("path: ${{ runner.temp }}/delta-ops-candidate", upload)
        self.assertLess(list(self.steps).index("Verify exact Delta artifact closure and validation"),
                        list(self.steps).index("Upload validated Delta Ops publication candidate"))

    def test_delta_summary_only_accepts_validated_email_and_scrubs_failures(self):
        import json, tempfile
        step = "Report nonpublishing Gmail canary result"
        with tempfile.TemporaryDirectory(prefix="delta-report-") as folder:
            root = Path(folder)
            base = {"CANARY_SOURCE_SHA": "a" * 40, "SOURCE_EVENT_SHA": "a" * 40,
                    "CHECKOUT_SHA": "a" * 40, "SELECTED_TRANSPORT": "email", "REPORT_DATE": "2026-10-15",
                    "REQUIRED_FIELD_COVERAGE": "17", "VALIDATION": "PASS", "GMAIL_PROOF": "true"}
            for change in [{}, {"SELECTED_TRANSPORT": "pdf", "GMAIL_PROOF": "false"},
                           {"SELECTED_TRANSPORT": "", "VALIDATION": "", "GMAIL_PROOF": ""},
                           {"REQUIRED_FIELD_COVERAGE": "16"}, {"GMAIL_PROOF": "false"},
                           {"CANARY_SOURCE_SHA": "SYNTHETIC_SECRET", "REPORT_DATE": "SYNTHETIC_SECRET", "VALIDATION": "SYNTHETIC_SECRET", "SELECTED_TRANSPORT": "SYNTHETIC_SECRET"}]:
                result = self.execute(step, root, dict(base, **change))
                self.assertEqual(result.returncode == 0, not change)
                self.assertNotIn("SYNTHETIC_SECRET", result.stdout + result.stderr)
                summary = json.loads(result.stdout.removeprefix("DELTA_CANARY "))
                self.assertEqual(summary["publication"], "NOT_ATTEMPTED")
                self.assertEqual(summary["gmail_proof"], "HELD" if change else "PASS")
                self.assertLess(len(result.stdout), 700)

    def test_delta_actual_artifact_shell_reuses_strict_validation_and_exact_closure(self):
        import importlib.util, os, sys, tempfile
        from unittest.mock import patch
        step = "Verify exact Delta artifact closure and validation"
        # Reuse the frozen, synthetic real-builder convergence fixture; no saved
        # private messages, Gmail endpoint, dependency installation or publisher.
        spec = importlib.util.spec_from_file_location("delta_gmail_fixture", REPO / "tests/test_delta_ops_gmail.py")
        fixture = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)
        self.assertIn("validate_candidate_metadata", self.shell(step))
        self.assertIn("validate_product(root)", self.shell(step))
        self.assertNotIn("main_publisher.py publish", self.shell(step))
        # Real candidate construction is covered by the frozen integration suite.
        # Here copy its strictly validated public-shaped PDF fixture through the
        # real builder so the changed artifact shell sees actual product bytes.
        sys.path.insert(0, str(REPO / "scripts"))
        import delta_ops_gmail as gmail
        from delta_ops_email import parse_report
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory(prefix="delta-artifact-") as folder:
            root = Path(folder); (root / "scripts").symlink_to(REPO / "scripts", target_is_directory=True)
            artifact = root / "delta-ops-candidate"; candidate = artifact / "candidate"
            record = parse_report(fixture.report(), source_kind="pdf")
            with patch.dict(os.environ, {"GITHUB_SHA": "a" * 40}):
                gmail.run_builder(record, as_of=datetime(2026, 10, 15, 12, tzinfo=timezone.utc),
                                  out_dir=candidate / "docs/data", artifact_metadata=artifact / "candidate-metadata.json")
            env = {"GITHUB_SHA": "a" * 40}
            good = self.execute(step, root, env)
            self.assertEqual(good.returncode, 0, good.stderr)
            self.assertIn("gmail_proof=false", (root / "output").read_text())
            self.assertIn("validation=PASS", (root / "output").read_text())
            expected = {"candidate-metadata.json", *{"candidate/" + str(p) for p in PRODUCT_PATHS[DELTA_OPS]}}
            self.assertEqual({str(p.relative_to(artifact)) for p in artifact.rglob("*") if p.is_file()}, expected)
            for extra in ["raw.eml", "verification.json", ".hidden"]:
                path = artifact / extra; path.write_text("SYNTHETIC_PRIVATE_PAYLOAD")
                bad = self.execute(step, root, env)
                self.assertNotEqual(bad.returncode, 0)
                self.assertNotIn("SYNTHETIC_PRIVATE_PAYLOAD", bad.stdout + bad.stderr)
                self.assertEqual(bad.stderr.strip(), "DELTA_ARTIFACT_REJECTED")
                path.unlink()
            bad = self.execute(step, root, {"GITHUB_SHA": "b" * 40})
            self.assertNotEqual(bad.returncode, 0)
            values = candidate / "docs/data/delta_ops_daily_summary.json"
            values.write_text(values.read_text() + " ")
            bad = self.execute(step, root, env)
            self.assertNotEqual(bad.returncode, 0)
            # The same closure must recognize real-builder bytes from a locally
            # signed synthetic email, not just a caller-supplied success label.
            email_root = root / "email-run"; email_root.mkdir()
            artifact = email_root / "delta-ops-candidate"; candidate = artifact / "candidate"
            env = dict(env, RUNNER_TEMP=str(email_root))
            raw, keys = fixture.signed_message()
            batch = gmail.retrieve(fixture.config(), as_of=fixture.AS_OF,
                                   http=fixture.mailbox([fixture.envelope(raw)]), keys=keys)
            selected = gmail.select_retrieved(batch, lambda: self.fail("unexpected PDF"), as_of=fixture.AS_OF)
            with patch.dict(os.environ, {"GITHUB_SHA": "a" * 40}):
                gmail.run_builder(selected["record"], as_of=fixture.AS_OF,
                                  out_dir=candidate / "docs/data", verification=batch.bindings[0],
                                  artifact_metadata=artifact / "candidate-metadata.json")
            (root / "output").unlink(missing_ok=True)
            good = self.execute(step, root, env)
            self.assertEqual(good.returncode, 0, good.stderr)
            output = (root / "output").read_text()
            self.assertIn("gmail_proof=true", output)
            self.assertIn("selected_transport=email", output)
            self.assertIn("required_field_coverage=17", output)
            for path in artifact.rglob("*"):
                if path.is_file():
                    for private in [fixture.CANARY, fixture.CLIENT_SECRET, fixture.TOKEN, fixture.ACCOUNT]:
                        self.assertNotIn(private, path.read_text())


class MainPublisherWorkflowSafetyTests(unittest.TestCase):
    def test_delta_saved_input_is_disabled_without_changing_production_controls(self) -> None:
        text = DELTA_OPS.read_text(encoding="utf-8")
        self.assertIn('DELTA_OPS_SAVED_EMAIL: ""', text)
        self.assertIn('cron: "41 15 * * *"', text)
        self.assertIn('cron: "11,41 16 * * *"', text)
        self.assertIn('cron: "11,41 17 * * *"', text)
        reader = text.split("      - name: Build Delta Ops through optional Gmail reader\n", 1)[1].split("      - name:", 1)[0]
        outside_reader = text.replace(reader, "")
        self.assertNotIn("secrets.DELTA_OPS_GMAIL_", outside_reader)
        self.assertEqual(reader.count("secrets.DELTA_OPS_GMAIL_"), 3)
        self.assertIn("steps.delta_ops_mode.outputs.use_gmail == 'true'", reader)
        default_builder = text.split("      - name: Build latest Delta Ops daily summary GeoJSON\n", 1)[1].split("      - name:", 1)[0]
        self.assertIn("steps.delta_ops_mode.outputs.use_gmail != 'true'", default_builder)
        self.assertIn('source("scripts/build_delta_ops_daily_summary.R")', default_builder)
        self.assertNotIn("secrets.", default_builder)
        dependency_step = text.split("      - name: Prepare optional Delta Gmail verification dependencies\n", 1)[1].split("      - name:", 1)[0]
        self.assertNotIn("secrets.", dependency_step)
        self.assertIn("--require-hashes --no-build-isolation --no-deps", dependency_step)
        self.assertIn("--artifact-metadata", reader)
        describe = text.split("      - name: Validate and describe Delta Ops candidate artifact\n", 1)[1].split("      - name:", 1)[0]
        self.assertIn("steps.delta_ops_mode.outputs.use_gmail != 'true'", describe)
        self.assertNotIn("trust_headers", text)
        self.assertEqual(text.count("group: brim-live-main-publish"), 1)

    def test_main_writer_inventory_is_complete(self) -> None:
        discovered = set()
        for path in WORKFLOWS.glob("*.yml"):
            text = path.read_text(encoding="utf-8")
            if "  schedule:\n" in text and (
                "contents: write" in text
                or "scripts/main_publisher.py publish" in text
                or re.search(r"(?m)^\s+git push\b", text)
            ):
                discovered.add(path.name)
        self.assertEqual(len(MAIN_WRITERS), 15)
        self.assertEqual(discovered, set(MAIN_WRITERS))

    def test_all_main_writers_serialize_only_publish_to_main(self) -> None:
        for workflow in MAIN_WRITER_PATHS:
            text = workflow.read_text(encoding="utf-8")
            self.assertNotIn("live-data-feed-writes-", text, workflow.name)
            self.assertNotRegex(text, r"(?m)^concurrency:\s*$", workflow.name)
            self.assertEqual(text.count("\n  publish-to-main:\n"), 1, workflow.name)
            self.assertEqual(text.count("group: brim-live-main-publish"), 1, workflow.name)
            self.assertEqual(text.count("cancel-in-progress: false"), 1, workflow.name)
            self.assertEqual(text.count("queue: max"), 1, workflow.name)

            prepare, publish = text.split("  publish-to-main:\n", 1)
            publish_header = publish.split("    steps:\n", 1)[0]
            self.assertIn(
                "    concurrency:\n"
                "      group: brim-live-main-publish\n"
                "      cancel-in-progress: false\n"
                "      queue: max",
                publish_header,
                workflow.name,
            )
            self.assertIn("  prepare-candidate:\n", prepare, workflow.name)
            self.assertIn("      contents: read", prepare, workflow.name)
            self.assertNotIn("contents: write", prepare, workflow.name)
            self.assertNotIn("scripts/main_publisher.py publish", prepare, workflow.name)
            self.assertNotRegex(
                prepare,
                r"(?m)^\s+git (?:add|commit|push|merge|rebase)\b",
                workflow.name,
            )
            self.assertIn("github.ref == 'refs/heads/main'", publish, workflow.name)
            self.assertEqual(text.count("contents: write"), 1, workflow.name)
            self.assertIn("      contents: write", publish_header, workflow.name)
            self.assertEqual(
                prepare.count("scripts/main_publisher.py prepare"), 1, workflow.name
            )
            self.assertEqual(
                publish.count("scripts/main_publisher.py publish"), 1, workflow.name
            )
            self.assertIn("actions/upload-artifact@v7", prepare, workflow.name)
            self.assertIn("actions/download-artifact@v8", publish, workflow.name)
            callbacks = []
            for option in (
                "candidate-validator",
                "reconcile-callback",
                "staged-validator",
            ):
                matches = re.findall(
                    rf"--{option}\s+(scripts/[A-Za-z0-9_./-]+\.py)", publish
                )
                self.assertEqual(len(matches), 1, f"{workflow.name}:{option}")
                callbacks.extend(matches)
            self.assertEqual(len(set(callbacks)), 1, workflow.name)

        cdec_text = CDEC.read_text(encoding="utf-8")
        self.assertIn('source("scripts/build_cdec_reservoir_latest.R")', cdec_text)
        self.assertIn("CDEC_RESERVOIR_GEOJSON:", cdec_text)
        self.assertIn("CDEC_RESERVOIR_SUMMARY_JSON:", cdec_text)

    def test_migrated_writers_declare_only_exact_product_paths(self) -> None:
        for workflow, expected_paths in PRODUCT_PATHS.items():
            text = workflow.read_text(encoding="utf-8")
            paths = set(re.findall(r"docs/data/[A-Za-z0-9_./-]+", text))
            self.assertEqual(paths, expected_paths, workflow.name)
            self.assertIn("scripts/main_publisher.py prepare", text, workflow.name)
            self.assertIn("scripts/main_publisher.py publish", text, workflow.name)
            for path in expected_paths:
                self.assertEqual(text.count(f"--allowlist {path}"), 2, workflow.name)

    def test_product_callbacks_and_candidate_roots_are_product_specific(self) -> None:
        delta = DELTA_OPS.read_text(encoding="utf-8")
        scan = SCAN.read_text(encoding="utf-8")
        cocorahs = COCORAHS.read_text(encoding="utf-8")
        streamflow = STREAMFLOW.read_text(encoding="utf-8")
        groundwater = GROUNDWATER.read_text(encoding="utf-8")
        asos_awos = ASOS_AWOS.read_text(encoding="utf-8")
        snow_pillow = SNOW_PILLOW.read_text(encoding="utf-8")
        cnrfc = CNRFC.read_text(encoding="utf-8")
        cbrfc = CBRFC.read_text(encoding="utf-8")
        gfs = GFS.read_text(encoding="utf-8")
        hrrr = HRRR.read_text(encoding="utf-8")
        nbm_wind = NBM_WIND.read_text(encoding="utf-8")
        nbm_qpf = NBM_QPF.read_text(encoding="utf-8")
        winter_storm = WINTER_STORM.read_text(encoding="utf-8")
        for text, callback in (
            (delta, "scripts/delta_ops_publisher.py"),
            (scan, "scripts/scan_soil_moisture_publisher.py"),
            (cocorahs, "scripts/cocorahs_publisher.py"),
            (streamflow, "scripts/usgs_streamflow_publisher.py"),
            (groundwater, "scripts/usgs_groundwater_publisher.py"),
        ):
            self.assertEqual(text.count(callback), 4)
        self.assertEqual(asos_awos.count("scripts/asos_awos_wind_publisher.py"), 5)
        self.assertEqual(snow_pillow.count("scripts/snow_pillow_publisher.py"), 6)
        self.assertEqual(cnrfc.count("scripts/cnrfc_forecast_publisher.py"), 5)
        self.assertEqual(cbrfc.count("scripts/cbrfc_forecast_publisher.py"), 5)
        self.assertEqual(gfs.count("scripts/gfs_wind_publisher.py"), 5)
        self.assertEqual(hrrr.count("scripts/hrrr_wind_publisher.py"), 5)
        self.assertEqual(nbm_wind.count("scripts/nbm_wind_publisher.py"), 5)
        self.assertEqual(nbm_qpf.count("scripts/nbm_qpf_publisher.py"), 6)
        self.assertEqual(
            winter_storm.count("scripts/winter_storm_levels_publisher.py"), 5
        )
        self.assertIn("DELTA_OPS_OUT_DIR:", delta)
        self.assertIn("delta-ops-candidate/candidate/docs/data", delta)
        self.assertIn("scan-soil-moisture-candidate/candidate", scan)
        self.assertIn("SCAN_STATION_INDEX_CSV: ${{ github.workspace }}", scan)
        self.assertNotIn("SCAN_SMS_GEOJSON:", scan)
        self.assertIn("COCORAHS_CA_DAILY_PRECIP_GEOJSON:", cocorahs)
        self.assertIn("COCORAHS_CONUS_DAILY_PRECIP_SUMMARY_JSON:", cocorahs)
        self.assertIn("USGS_STREAMFLOW_GEOJSON:", streamflow)
        self.assertIn("USGS_STREAMFLOW_SUMMARY_JSON:", streamflow)
        self.assertIn("USGS_GW_GEOJSON:", groundwater)
        self.assertIn("USGS_GW_SUMMARY_JSON:", groundwater)
        self.assertIn("BRIM_OBS_PROJECT_ROOT: ${{ runner.temp }}/asos-awos-build-root", asos_awos)
        self.assertIn('candidate_root="${candidate_artifact_root}/candidate"', asos_awos)
        self.assertIn('candidate_root="${candidate_artifact_root}/candidate"', snow_pillow)
        self.assertIn("snow_provider_state_v1", snow_pillow)
        self.assertIn("SNOW_PILLOW_BUILD_ROOT:", snow_pillow)

    def test_migrated_workflows_resolve_runner_temp_only_after_allocation(self) -> None:
        for workflow in MAIN_WRITER_PATHS:
            text = workflow.read_text(encoding="utf-8")
            for block in job_level_env_blocks(text):
                self.assertNotRegex(block, r"\$\{\{\s*runner\.", workflow.name)
            self.assertNotIn("/home/runner/", text, workflow.name)
            self.assertNotRegex(text, r"(?<![A-Z_])/tmp/", workflow.name)

        text = CDEC.read_text(encoding="utf-8")
        self.assertEqual(
            text.count(
                'candidate_artifact_root="${RUNNER_TEMP}/cdec-reservoir-candidate"'
            ),
            2,
        )
        self.assertIn('candidate_root="${candidate_artifact_root}/candidate"', text)
        self.assertEqual(text.count("${{ runner.temp }}"), 4)

        for workflow, root_name in (
            (DELTA_OPS, "delta-ops-candidate"),
            (SCAN, "scan-soil-moisture-candidate"),
            (COCORAHS, "cocorahs-candidate"),
            (STREAMFLOW, "usgs-streamflow-candidate"),
            (GROUNDWATER, "usgs-groundwater-candidate"),
            (ASOS_AWOS, "asos-awos-candidate"),
            (SNOW_PILLOW, "snow-pillow-candidate"),
            (CNRFC, "cnrfc-forecast-candidate"),
            (CBRFC, "cbrfc-forecast-candidate"),
            (GFS, "gfs-wind-candidate"),
            (HRRR, "hrrr-wind-candidate"),
            (NBM_WIND, "nbm-wind-candidate"),
            (NBM_QPF, "nbm-qpf-publication-candidate"),
            (WINTER_STORM, "winter-storm-levels-candidate"),
        ):
            text = workflow.read_text(encoding="utf-8")
            self.assertIn(f'candidate_artifact_root="${{RUNNER_TEMP}}/{root_name}"', text)

    def test_phase3_schedules_and_product_health_switches_are_unchanged(self) -> None:
        cocorahs = COCORAHS.read_text(encoding="utf-8")
        streamflow = STREAMFLOW.read_text(encoding="utf-8")
        groundwater = GROUNDWATER.read_text(encoding="utf-8")
        self.assertIn('cron: "37 0,8,16 * * *"', cocorahs)
        self.assertIn('cron: "23 */4 * * *"', streamflow)
        self.assertIn('cron: "41 13 * * *"', groundwater)
        self.assertIn('USGS_STREAMFLOW_MIN_LATEST_Q_TO_PUBLISH: "100"', streamflow)
        self.assertIn('USGS_STREAMFLOW_ALLOW_DEGRADED_PUBLISH: "false"', streamflow)
        self.assertIn('USGS_GW_MIN_API_SITES_TO_PUBLISH: "300"', groundwater)
        self.assertIn('USGS_GW_MIN_FEATURES_TO_PUBLISH: "300"', groundwater)
        self.assertIn('USGS_GW_ALLOW_INDEX_FALLBACK: "true"', groundwater)

    def test_phase4_asos_freshness_and_watchdog_contract(self) -> None:
        asos_awos = ASOS_AWOS.read_text(encoding="utf-8")
        watchdog = (WORKFLOWS / "check-wind-feeds.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "17,42 * * * *"', asos_awos)
        self.assertIn("age_minutes >= 45.0", asos_awos)
        self.assertIn("manual dispatch always runs", asos_awos)
        self.assertIn("needs.prepare-candidate.outputs.should_publish == 'true'", asos_awos)
        self.assertIn('BRIM_OBS_MAX_AGE_HOURS: "3.0"', asos_awos)
        self.assertIn('BRIM_OBS_STALE_AFTER_HOURS: "2.0"', asos_awos)
        self.assertIn('"workflow": "build-asos-awos-wind-latest.yml"', watchdog)
        self.assertIn('"schedules": ["17,42 * * * *"]', watchdog)
        self.assertIn('"cooldown": 15', watchdog)

    def test_phase5_snow_pillow_schedule_and_provider_boundary_are_unchanged(self) -> None:
        snow_pillow = SNOW_PILLOW.read_text(encoding="utf-8")
        self.assertIn('cron: "52 5,13,21 * * *"', snow_pillow)
        self.assertIn('source(file.path(', snow_pillow)
        self.assertIn('"build_snow_pillow_latest.R"', snow_pillow)
        self.assertIn("scripts/snow_pillow_partial_refresh_helpers.R", snow_pillow)
        self.assertIn("data/input/snow_pillow_station_index.csv", snow_pillow)
        self.assertIn("data/input/snow_pillow_swe_normal_medians.csv", snow_pillow)
        self.assertIn("scripts/main_publisher.py publish", snow_pillow)

    def test_phase6_rfc_schedules_manual_defaults_and_record_callbacks_are_unchanged(self) -> None:
        cnrfc = CNRFC.read_text(encoding="utf-8")
        cbrfc = CBRFC.read_text(encoding="utf-8")
        self.assertIn('- cron: "56 10,16 * * *"', cnrfc)
        self.assertIn('- cron: "14 8 * * *"', cbrfc)
        for text, callback, semantic_type in (
            (cnrfc, "scripts/cnrfc_forecast_publisher.py", "cnrfc_record_state_sha256_v1"),
            (cbrfc, "scripts/cbrfc_forecast_publisher.py", "cbrfc_record_state_sha256_v1"),
        ):
            self.assertRegex(
                text,
                r"publish:\n\s+description:.*\n\s+required: false\n\s+default: false",
            )
            self.assertIn("needs.prepare-candidate.outputs.publish == 'true'", text)
            self.assertIn(semantic_type, text)
            self.assertEqual(text.count(callback), 5)
            self.assertNotRegex(text, r"(?m)^\s+git (add|commit|push)\b")

    def test_phase7_gfs_rolling_tree_ownership_and_schedule_are_bounded(self) -> None:
        text = GFS.read_text(encoding="utf-8")
        fixed = {
            "docs/data/wind/gfs_surface_wind_latest.json",
            "docs/data/wind/gfs_surface_wind_latest_summary.json",
            "docs/data/wind/gfs_surface_wind_feed_manifest.json",
        }
        self.assertIn('- cron: "47 * * * *"', text)
        self.assertIn('source("scripts/build_gfs_wind_latest.R")', text)
        self.assertIn("BRIM_RTW_PROJECT_ROOT: ${{ runner.temp }}/gfs-wind-build-root", text)
        for path in fixed:
            self.assertEqual(text.count(f"--allowlist {path}"), 2)
        self.assertEqual(text.count("--owned-root docs/data/wind/gfs/surface"), 2)
        self.assertIn("gfs_rolling_state_sha256_v1", text)
        self.assertNotRegex(text, r"(?m)^\s+git (add|commit|push)\b")

    def test_phase8_hrrr_rolling_tree_ownership_and_schedule_are_bounded(self) -> None:
        text = HRRR.read_text(encoding="utf-8")
        fixed = {
            "docs/data/wind/hrrr_surface_wind_latest.json",
            "docs/data/wind/hrrr_surface_wind_latest_summary.json",
            "docs/data/wind/hrrr_surface_wind_feed_manifest.json",
        }
        self.assertIn('- cron: "33 * * * *"', text)
        self.assertIn("Rscript scripts/build_hrrr_wind_latest.R", text)
        self.assertIn("BRIM_HRRR_PROJECT_ROOT: ${{ runner.temp }}/hrrr-wind-build-root", text)
        self.assertIn('BRIM_HRRR_RETAIN_PAST_HOURS: "4"', text)
        self.assertIn('BRIM_HRRR_RETAIN_FUTURE_HOURS: "14"', text)
        self.assertIn('BRIM_HRRR_MAX_MANIFEST_ENTRIES: "24"', text)
        for path in fixed:
            self.assertEqual(text.count(f"--allowlist {path}"), 2)
        self.assertEqual(text.count("--owned-root docs/data/wind/hrrr/surface"), 2)
        self.assertIn("hrrr_rolling_state_sha256_v1", text)
        self.assertNotRegex(text, r"(?m)^\s+git (add|commit|push)\b")

    def test_phase9_nbm_complete_cycle_ownership_and_schedule_are_bounded(self) -> None:
        text = NBM_WIND.read_text(encoding="utf-8")
        fixed = {
            "docs/data/wind/nbm_wind_guidance_feed_manifest.json",
            "docs/data/wind/nbm_wind_guidance_latest_summary.json",
        }
        self.assertIn('- cron: "23 1,7,13,19 * * *"', text)
        self.assertIn("Rscript scripts/build_nbm_wind_guidance_latest.R", text)
        self.assertIn(
            "BRIM_NBM_PROJECT_ROOT: ${{ runner.temp }}/nbm-wind-build-root",
            text,
        )
        self.assertIn('BRIM_NBM_TARGET_LEAD_HOURS: "6,12,24,48"', text)
        self.assertIn('BRIM_NBM_SUPPORT_WINDOW_HOURS: "6"', text)
        self.assertIn('BRIM_NBM_RETAIN_HOURS: "18"', text)
        for path in fixed:
            self.assertEqual(text.count(f"--allowlist {path}"), 2)
        self.assertEqual(text.count("--owned-root docs/data/wind/nbm/guidance"), 2)
        self.assertIn("nbm_wind_complete_cycle_state_sha256_v1", text)
        self.assertNotRegex(text, r"(?m)^\s+git (add|commit|push)\b")

    def test_nbm_qpf_two_cycle_ownership_schedule_and_callbacks_are_bounded(self) -> None:
        text = NBM_QPF.read_text(encoding="utf-8")
        self.assertIn('- cron: "18 2,8,14,20 * * *"', text)
        self.assertIn('- cron: "10 3,9,15,21 * * *"', text)
        self.assertEqual(text.count("- cron:"), 2)
        self.assertIn("Approved source-readiness schedule", text)
        self.assertIn('source("scripts/build_nbm_qpf_candidate.R")', text)
        self.assertEqual(text.count("qpf_discover_cycle("), 1)
        self.assertIn("canonical_cycle=\"$(jq -er '.current_cycle_utc'", text)
        self.assertIn('"${CANDIDATE_CYCLE}" < "${canonical_cycle}"', text)
        self.assertIn("should_build=false", text)
        self.assertEqual(
            text.count("--allowlist docs/data/nbm-qpf/nbm_qpf_manifest.json"), 2
        )
        self.assertEqual(text.count("--owned-root docs/data/nbm-qpf/nbm/qpf"), 2)
        self.assertIn("nbm_qpf_cycle_state_sha256_v1", text)
        self.assertIn("needs.prepare-candidate.outputs.should_build == 'true'", text)
        self.assertNotIn("--owned-root docs/data/nbm-qpf", text.replace(
            "--owned-root docs/data/nbm-qpf/nbm/qpf", ""
        ))
        self.assertNotRegex(text, r"(?m)^\s+git (add|commit|push)\b")

    def test_phase10_winter_storm_two_cycle_ownership_and_schedule_are_bounded(self) -> None:
        text = WINTER_STORM.read_text(encoding="utf-8")
        manifest = "docs/data/winter-storm-levels/winter_storm_levels_manifest.json"
        target_root = "docs/data/winter-storm-levels/nbm/snow-level"
        self.assertIn('- cron: "8 2,8,14,20 * * *"', text)
        self.assertIn('- cron: "54 2,8,14,20 * * *"', text)
        self.assertIn("Rscript scripts/preflight_winter_storm_levels.R", text)
        self.assertIn("Rscript scripts/build_winter_storm_levels.R", text)
        self.assertIn('BRIM_WSL_PUBLISH: "false"', text)
        self.assertEqual(text.count(f"--allowlist {manifest}"), 2)
        self.assertEqual(text.count(f"--owned-root {target_root}"), 2)
        self.assertIn("winter_storm_two_cycle_state_sha256_v1", text)
        self.assertNotRegex(text, r"(?m)^\s+git (add|commit|push)\b")

    def test_shared_publisher_has_no_force_push_or_merge_strategy(self) -> None:
        text = (REPO / "scripts" / "main_publisher.py").read_text(encoding="utf-8")
        self.assertNotIn('"push", "--force"', text)
        self.assertNotIn('"push", "-f"', text)
        self.assertNotRegex(text, r"\bgit (pull|merge|rebase)\b")
        self.assertIn('"git", "push", "origin", "HEAD:refs/heads/main"', text)


if __name__ == "__main__":
    unittest.main()
