"""Offline importer regressions using existing Arrow/Parquet and accepted R core."""
import csv
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("bulk", ROOT / "scripts/dendra/bulk_csv_import.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def row(unit="Percent", token="pct", candidates=None):
    return dict(candidate_stream_ids=json.dumps(candidates or ["sid"]), mapping_state="CORROBORATED_PROPOSAL",
                proposed_stream_id="sid", native_unit=unit, export_unit_token=token,
                source_parameter_terms=json.dumps({"ds": {"Variable": "VolumetricWaterContent"}}),
                unit_resolution="RETAINED_ACCEPTED_UNIT_DEFINITION", depth_cm="")


class Classification(unittest.TestCase):
    def test_units_and_unknown_depth(self):
        self.assertEqual(m.classify(row())[:2], (m.CLASSES[0], 1))
        self.assertEqual(m.classify(row("VolumetricWaterContent", "m-3-m-3"))[:2], (m.CLASSES[1], 100))
        self.assertEqual(m.classify(row("Microsecond", "usec"))[0], m.CLASSES[3])
        self.assertEqual(m.classify(row("Millimeter", "mm"))[0], m.CLASSES[4])
        self.assertEqual(m.classify(row("Dimensionless", ""))[0], m.CLASSES[2])
        self.assertEqual(m.classify(row("VolumetricWaterContent", "pct"))[0], m.CLASSES[2])
        self.assertEqual(m.classify(row(candidates=["a", "b"]))[0], m.CLASSES[5])

    def test_parameter_contradiction(self):
        r = row(); r["source_parameter_terms"] = '{"ds":{"Variable":"Temperature"}}'
        self.assertEqual(m.classify(r)[0], m.CLASSES[5])

    def test_prior_daily_comparison_detects_semantic_difference(self):
        r = dict(date="2024-02-29", water_year=2024, dowy=152, water_day_aligned=152,
                 n_valid=144, n_total=144, n_out_of_range=0, plot_eligible=True, flags=[],
                 mean_native=5., mean_percent=5., coverage_fraction=1., temporal_span_fraction=.99,
                 expected_samples=144, cadence_seconds=600)
        self.assertEqual(m.compare_daily([r], [r])["exact_matches"], 1)
        s = dict(r, mean_percent=5.000000000001)
        self.assertEqual(m.compare_daily([s], [r])["tolerance_matches"], 1)
        s["dowy"] = 153
        self.assertEqual(m.compare_daily([s], [r])["differences"], 1)

    def test_exact_real_470_column_classification(self):
        # Optional local evidence check; synthetic tests remain portable in CI.
        path = ROOT / ".l01-soil-integration/l03-bulk-csv-intake-20261005T061546Z/BULK_SERIES_CROSSWALK.csv"
        if not path.exists(): self.skipTest("Private full intake not installed")
        with path.open() as f: rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 470)
        self.assertEqual(len({r["export_local_series_key"] for r in rows}), 470)
        counts = m.collections.Counter(m.classify(r)[0] for r in rows)
        self.assertEqual([counts[c] for c in m.CLASSES], [177, 166, 109, 2, 5, 11])
        self.assertEqual(sum(int(r["non_null_count"]) for r in rows), 113807081)
        self.assertEqual(sum(int(r["non_null_count"]) == 0 for r in rows), 13)


class ArrowIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="dendra-bulk-test-")
        cls.root = Path(cls.temp.name)
        cls.helper = m.compile_helper(cls.root / "arrow-helper")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.t = tempfile.TemporaryDirectory(dir=self.root)
        self.p = Path(self.t.name)
        self.addCleanup(self.t.cleanup)

    def test_streaming_preservation_duplicate_headers_empty_and_roundtrip(self):
        source = self.p / "source.csv"
        with source.open("w", newline="") as f:
            w = csv.writer(f); w.writerow(["time", "same", "same", "empty-target", "empty-excluded"])
            from datetime import datetime, timedelta
            values = [0, -235, 7999, 28.79, .5]
            for i in range(20000):
                w.writerow([(datetime(2024, 1, 1)+timedelta(seconds=600*i)).strftime("%Y-%m-%d %H:%M:%S"), values[i % 5], values[i % 5], "", ""])
        before = m.sha(source)
        ctl = self.p / "control.csv"
        with ctl.open("w", newline="") as f:
            w = csv.writer(f)
            for ordinal, multiplier in [(2,1),(3,100),(4,1)]:
                w.writerow([ordinal, "asset"+str(ordinal), "key"+str(ordinal), "pct" if multiplier==1 else "m-3-m-3", multiplier, "sid"+str(ordinal), "", "station", "", before])
        out = self.p / "native"; out.mkdir()
        r = m.run([self.helper, "native", source, ctl, out, 16384])
        stats = list(csv.DictReader(io.StringIO(r.stdout), delimiter="\t"))
        self.assertEqual([int(x["rows"]) for x in stats], [20000,20000,0])
        self.assertEqual(stats[0]["zeros"], "4000")
        self.assertEqual(stats[0]["negative"], "4000")
        self.assertFalse((out / "asset4.parquet").exists())
        self.assertFalse((out / "asset5.parquet").exists())
        metric = json.loads(r.stderr)
        self.assertGreater(metric["batches"], 10)
        self.assertLess(metric["max_batch_rows"], 1000)
        self.assertLess(metric["arrow_peak_bytes"], 64*1024*1024)
        for ordinal, multiplier in [(2,1),(3,100)]:
            path = out / f"asset{ordinal}.parquet"
            self.assertEqual(json.loads(m.run([self.helper,"readback",path]).stdout)["rows"],20000)
            rows = list(csv.DictReader(io.StringIO(m.run([self.helper,"extract",path,"2024-01-01 00:00:00","2024-01-01 01:00:00"]).stdout)))
            self.assertEqual([float(x["exported_value"]) for x in rows[:5]], values)
            self.assertEqual([float(x["canonical_vwc_percent"]) for x in rows[:5]], [v*multiplier for v in values])
            subset = self.p / f"subset{ordinal}.parquet"
            m.run([self.helper,"subset",path,"2024-01-01 00:00:00","2024-01-01 01:00:00",subset])
            self.assertEqual(json.loads(m.run([self.helper,"readback",subset]).stdout)["rows"],6)
        self.assertEqual(before, m.sha(source))
        with self.assertRaises(m.subprocess.CalledProcessError):
            m.run([self.helper,"native",source,ctl,out,16384])  # no overwrite

    def test_catalog_parquet_null_depth(self):
        rows = [dict(export_local_series_key="key", depth_cm=None, observation_count=0, selected=True, target=True),
                dict(export_local_series_key="excluded", depth_cm=None, observation_count=0, selected=True, target=False)]
        m.table_parquet(self.helper, self.p / "catalog.csv", rows)
        self.assertEqual(json.loads(m.run([self.helper,"readback",self.p/"catalog.parquet"]).stdout)["rows"],2)

    def test_original_hash_and_all_null_catalog_closure(self):
        inp=self.p/"inputs"; inp.mkdir(); intake=self.p/"intake"; intake.mkdir()
        f=inp/"s.csv"; f.write_text("time,same,same\n2024-01-01 00:00:00,,\n")
        digest=m.sha(f); rows=[]; profiles=[]
        for ordinal, unit, token in [(2,"Percent","pct"),(3,"Microsecond","usec")]:
            key=f"csv-sha256:{digest}:column:{ordinal}"
            r=row(unit,token);r.update(relative_asset_path="s.csv",file_sha256=digest,column_ordinal_1_based=ordinal,
                export_local_series_key=key,original_header="same",non_null_count=0)
            rows.append(r);profiles.append(dict(export_local_series_key=key,non_null_count=0))
        m.write_json(intake/"BULK_EXPORT_INVENTORY.json",dict(files=[dict(basename="s.csv",sha256=digest,header=["time","same","same"],series=profiles)]))
        m.write_csv(intake/"BULK_SERIES_CROSSWALK.csv",rows)
        catalog,_,_=m.preflight(inp,intake,2,1)
        self.assertEqual([r["observation_count"] for r in catalog],[0,0])
        self.assertEqual([r["target"] for r in catalog],[True,False])
        self.assertNotEqual(catalog[0]["asset_id"],catalog[1]["asset_id"])
        f.write_text(f.read_text()+"2024-01-02 00:00:00,1,2\n")
        with self.assertRaisesRegex(ValueError,"hash mismatch"):m.preflight(inp,intake,2,1)

    def test_duplicate_selection_requires_exact_values(self):
        for name in ["a.csv","b.csv"]:(self.p/name).write_text("time,v\n2024-01-01 00:00:00,0\n")
        rows=[dict(relative_asset_path=n,column_ordinal_1_based=2,proposed_stream_id="same",product_class=m.CLASSES[0],
                   native_unit="Percent",depth_cm=None,proposed_station_id="station",export_local_series_key=n) for n in ["a.csv","b.csv"]]
        proof=m.resolve_duplicates(self.p,rows)
        self.assertEqual(len(proof),1);self.assertEqual(rows[1]["duplicate_of"],"a.csv")
        (self.p/"b.csv").write_text("time,v\n2024-01-01 00:00:00,1\n")
        with self.assertRaisesRegex(ValueError,"Conflicting"):m.resolve_duplicates(self.p,rows)
        for r in rows:r["product_class"]=m.CLASSES[5]
        self.assertEqual(m.resolve_duplicates(self.p,rows),[])

    def test_core_fixed_pst_leap_day_no_forward_fill_and_zero(self):
        from datetime import datetime,timedelta
        native=self.p/"native.csv"
        with native.open("w",newline="") as f:
            w=csv.writer(f);w.writerow(["datastream_id","t","v","value_status","duplicate_conflict","alternative_out_of_range"])
            for i in range(144):
                # UTC 08:00 starts the previous fixture's fixed-PST day.
                w.writerow([m.CAMP,(datetime(2024,2,29,8)+timedelta(minutes=10*i)).strftime("%Y-%m-%dT%H:%M:%SZ"),0,"number",False,False])
        result=m.daily_core(native,self.p/"daily.json","2024-02-29","2024-03-02",dict(version="frozen-cadence-context-1",seconds=600,source="test"))
        good,missing=result["rows"]
        self.assertEqual(good["date"],"2024-02-29");self.assertEqual(good["water_year"],2024)
        self.assertEqual(good["dowy"],152);self.assertEqual(good["water_day_aligned"],152)
        self.assertEqual(good["mean_percent"],0);self.assertTrue(good["plot_eligible"])
        self.assertEqual(missing["n_valid"],0);self.assertIsNone(missing["mean_percent"])
        self.assertFalse(missing["plot_eligible"]);self.assertIn("no_observations",missing["flags"])


if __name__ == "__main__": unittest.main()
