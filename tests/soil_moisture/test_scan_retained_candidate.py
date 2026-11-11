"""Contained tests for the finite data-only Actions bridge; no provider access."""
import datetime as dt
import hashlib
import importlib.util
import json
import os
import shlex
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import scan_retained_candidate as bridge


class RetainedTests(unittest.TestCase):
    def env(self):
        return dict(GITHUB_ACTIONS="true", GITHUB_REPOSITORY=bridge.REPOSITORY,
            GITHUB_EVENT_NAME="workflow_dispatch", GITHUB_REF="refs/heads/main",
            GITHUB_REF_TYPE="branch", GITHUB_RUN_NUMBER="2", GITHUB_RUN_ATTEMPT="1",
            GITHUB_WORKFLOW_REF=bridge.REPOSITORY + "/" + bridge.WORKFLOW + "@refs/heads/main",
            GITHUB_SHA="a"*40, RETAINED_EVENT_SHA="a"*40,
            RETAINED_STAGING_SHA=bridge.STAGING_SHA, GITHUB_RUN_ID="123")

    def test_actual_admission_and_refusals(self):
        now = bridge.EXPIRES - dt.timedelta(hours=1)
        self.assertEqual(bridge.admit(self.env(), "a"*40, now), "a"*40)
        for key, value in [("GITHUB_REF", "refs/heads/fixture"), ("GITHUB_EVENT_NAME", "push"),
                ("GITHUB_RUN_NUMBER", "1"), ("GITHUB_RUN_NUMBER", "3"), ("GITHUB_RUN_ATTEMPT", "2"),
                ("GITHUB_REPOSITORY", "example/other"), ("GITHUB_ACTIONS", "false"),
                ("GITHUB_REF_TYPE", "tag"), ("GITHUB_WORKFLOW_REF", "other"),
                ("RETAINED_STAGING_SHA", "b"*40), ("RETAINED_EVENT_SHA", "b"*40),
                ("GITHUB_RUN_ID", ""), ("GITHUB_SHA", "main")]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                env = self.env(); env[key] = value
                bridge.admit(env, "a"*40, now)
        for when in (bridge.EXPIRES, bridge.EXPIRES-dt.timedelta(seconds=899)):
            with self.assertRaises(ValueError):bridge.admit(self.env(), "a"*40, when)
        with self.assertRaises(ValueError):bridge.admit(self.env(), "b"*40, now)

    def test_tree_refuses_extra_missing_duplicate_links_and_unsafe_paths(self):
        records = [b"100644 blob " + b"a"*40 + b"\t" + n.encode() for n in bridge.EXPECTED]
        self.assertEqual(set(bridge.tree_entries(b"\0".join(records)+b"\0", exact=True)), set(bridge.EXPECTED))
        bad = [records[:-1], records+[records[0]], records+[b"100644 blob "+b"a"*40+b"\t../escape"],
               [records[0].replace(b"100644", b"120000")]+records[1:],
               [records[0].replace(b"100644", b"100755")]+records[1:]]
        for entries in bad:
            with self.assertRaises(ValueError):bridge.tree_entries(b"\0".join(entries), exact=True)

    def test_actual_git_data_export_and_digest_refusal(self):
        # Real Git objects, no mocks of tree/blob reads. Only fixture identities vary.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = str(Path(tmp).resolve())
            repo = Path(tmp)/"repo";repo.mkdir()
            def git(*args, data=None):
                return subprocess.check_output(["git", "-C", str(repo), *args], input=data,
                    env=dict(os.environ, GIT_AUTHOR_NAME="Synthetic", GIT_AUTHOR_EMAIL="fixture@example.invalid",
                             GIT_COMMITTER_NAME="Synthetic", GIT_COMMITTER_EMAIL="fixture@example.invalid"), stderr=subprocess.DEVNULL).decode().strip()
            git("init", "-q")
            bodies = {n: ("SYNTHETIC " + n).encode() for n in bridge.EXPECTED}
            entries = []
            for n,b in sorted(bodies.items()):
                oid=git("hash-object", "-w", "--stdin", data=b)
                entries.append(f"100644 blob {oid}\t{Path(n).name}\n")
            data=git("mktree", data="".join(entries).encode())
            docs=git("mktree", data=f"040000 tree {data}\tdata\n".encode())
            tree=git("mktree", data=f"040000 tree {docs}\tdocs\n".encode())
            commit=git("commit-tree", tree, data=b"Synthetic fixture\n")
            pins={n:hashlib.sha256(b).hexdigest() for n,b in bodies.items()}
            with patch.object(bridge,"STAGING_SHA",commit), patch.object(bridge,"EXPECTED",pins):
                dest=Path(tmp)/"candidate";bridge.export_blobs(repo,commit,dest,pinned=True)
                self.assertEqual({n:(dest/n).read_bytes() for n in pins},bodies)
                with self.assertRaises(ValueError):bridge.export_blobs(repo,"0"*40,Path(tmp)/"wrong",pinned=True)
                pins[next(iter(pins))]="0"*64
                with self.assertRaises(ValueError):bridge.export_blobs(repo,commit,Path(tmp)/"bad",pinned=True)
                self.assertFalse((Path(tmp)/"bad").exists())
            link=Path(tmp)/"linked";link.symlink_to(repo, target_is_directory=True)
            with self.assertRaises(ValueError):bridge.new_directory(link/"child")

    def test_workflow_actual_structure_permissions_and_no_acquisition(self):
        path=ROOT/bridge.WORKFLOW
        workflow=json.loads(subprocess.check_output(["ruby","-ryaml","-rjson","-e",
            "puts JSON.generate(YAML.load_file(ARGV[0]))",str(path)],text=True))
        self.assertEqual(set(workflow["on"]),{"workflow_dispatch"})
        self.assertEqual(workflow["permissions"],{})
        jobs=workflow["jobs"];self.assertEqual(set(jobs),{"prepare-candidate","publish-to-main"})
        self.assertEqual(jobs["prepare-candidate"]["permissions"],{"contents":"read"})
        self.assertEqual(jobs["publish-to-main"]["permissions"],{"contents":"write"})
        self.assertEqual(jobs["publish-to-main"]["concurrency"],{"group":"brim-live-main-publish","cancel-in-progress":False,"queue":"max"})
        self.assertEqual(jobs["publish-to-main"]["needs"],"prepare-candidate")
        for job in jobs.values():
            for needed in ("github.event_name == 'workflow_dispatch'","github.ref == 'refs/heads/main'","github.run_number == 2","github.run_attempt == 1"):
                self.assertIn(needed,job["if"])
            self.assertEqual(job["steps"][0]["with"]["ref"],"${{ github.sha }}")
            self.assertIn("scan_retained_candidate.py admit",job["steps"][1]["run"])
        steps=jobs["prepare-candidate"]["steps"]
        upload=steps[-1];paths=upload["with"]["path"].splitlines()
        self.assertEqual(set(paths),{"${{ runner.temp }}/scan-retained-candidate/candidate/"+n for n in bridge.EXPECTED}|{"${{ runner.temp }}/scan-retained-candidate/candidate-metadata.json"})
        self.assertEqual(upload["with"]["if-no-files-found"],"error")
        raw=path.read_text()
        for forbidden in ("Rscript", "build_scan_soil_moisture_latest.R", "curl ", "wget ", "workflow_run:", "schedule:", "pull_request:"):
            self.assertNotIn(forbidden,raw)
        self.assertEqual(raw.count("main_publisher.py publish"),1)
        import re
        self.assertEqual(re.findall(r"--allowlist (docs/data/[^\s\\]+)",raw),list(bridge.ORDERED_ALLOWLIST))
        self.assertEqual(set(bridge.ORDERED_ALLOWLIST),set(bridge.EXPECTED))

    def test_missing_tampered_and_linked_candidate_refuse_before_validator(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            pins={n:hashlib.sha256(b"SYNTHETIC").hexdigest() for n in bridge.EXPECTED}
            for n in pins:
                path=root/n;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b"SYNTHETIC")
            target=root/next(iter(pins))
            with patch.object(bridge,"EXPECTED",pins):
                protected=root/"private-receipt.json";protected.write_text("SYNTHETIC PRIVATE")
                with self.assertRaises(ValueError):bridge.verify_candidate(root)
                protected.unlink()
                target.write_bytes(b"TAMPERED")
                with self.assertRaises(ValueError):bridge.verify_candidate(root)
                target.unlink()
                with self.assertRaises(ValueError):bridge.verify_candidate(root)
                target.symlink_to(root/next(n for n in pins if root/n!=target))
                with self.assertRaises(ValueError):bridge.verify_candidate(root)

    def test_shared_metadata_source_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();candidate=root/"candidate";candidate.mkdir()
            paths=[]
            for n in bridge.EXPECTED:
                p=candidate/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_text("SYNTHETIC")
                paths.append(n)
            import argparse
            meta=root/"candidate-metadata.json"
            bridge.main_publisher.prepare_metadata(argparse.Namespace(candidate_root=str(candidate),
                output=str(meta), product_id=bridge.scan.PRODUCT_ID, semantic_key_type="feed_build_time_utc",
                semantic_key="2026-11-11T00:00:00Z", source_event_sha="a"*40, allowlist=paths, owned_root=None))
            args=dict(root=candidate,metadata_path=meta,product_id=bridge.scan.PRODUCT_ID,allowlist=paths,expected_source_sha="a"*40)
            bridge.main_publisher.validate_candidate_metadata(**args)
            with self.assertRaises(bridge.main_publisher.PublisherError):
                bridge.main_publisher.validate_candidate_metadata(**dict(args,expected_source_sha="b"*40))

    def test_actual_prepare_verify_and_workflow_publisher_order(self):
        # Real synthetic Git objects, bridge CLI preparation and unmodified
        # publisher parser/metadata validation. No mock source/hash/data reads.
        spec=importlib.util.spec_from_file_location("scan_fixture",ROOT/"tests/test_phase2_product_publishers.py")
        fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
        workflow=json.loads(subprocess.check_output(["ruby","-ryaml","-rjson","-e",
            "puts JSON.generate(YAML.load_file(ARGV[0]))",str(ROOT/bridge.WORKFLOW)],text=True))
        script=workflow["jobs"]["publish-to-main"]["steps"][-1]["run"]
        argv=["publish"]+shlex.split(script.split("python3 scripts/main_publisher.py publish",1)[1].replace("\\\n",""))
        publish_args=bridge.main_publisher.build_parser().parse_args(argv)
        self.assertEqual(publish_args.allowlist,list(bridge.ORDERED_ALLOWLIST))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();repo=root/"repo";repo.mkdir()
            env=dict(os.environ,GIT_AUTHOR_NAME="Synthetic",GIT_AUTHOR_EMAIL="fixture@example.invalid",
                     GIT_COMMITTER_NAME="Synthetic",GIT_COMMITTER_EMAIL="fixture@example.invalid")
            def git(*args,data=None):
                return subprocess.check_output(["git","-C",str(repo),*args],input=data,env=env,
                    stderr=subprocess.DEVNULL).decode().strip()
            def product(build):
                fixture.write_scan_product(repo,build,station_name="SYNTHETIC")
                path=repo/bridge.scan.GEOJSON_PATH
                value=json.loads(path.read_text())
                for feature in value["features"]:
                    props=feature["properties"]
                    props.update(station_triplet=str(props["site_code"])+":XX:SCAN",
                        current_water_year=2026,current_wy_start_date="2025-10-01")
                path.write_text(json.dumps(value))
                git("add","--",*bridge.ORDERED_ALLOWLIST)
                return git("write-tree")
            git("init","-q")
            prior=git("commit-tree",product("2026-08-18T00:00:00Z"),data=b"Synthetic prior\n")
            git("update-ref","HEAD",prior)
            staging=git("commit-tree",product("2026-08-19T00:00:00Z"),data=b"Synthetic retained\n")
            pins={n:hashlib.sha256((repo/n).read_bytes()).hexdigest() for n in bridge.ORDERED_ALLOWLIST}
            admission=self.env();admission.update(GITHUB_SHA=prior,RETAINED_EVENT_SHA=prior,RETAINED_STAGING_SHA=staging)
            class FixtureClock:
                @staticmethod
                def now(tz):return bridge.EXPIRES-dt.timedelta(hours=1)
            artifact=root/"artifact";old_cwd=Path.cwd()
            try:
                os.chdir(repo)
                with patch.object(bridge,"STAGING_SHA",staging),patch.object(bridge,"EXPECTED",pins),\
                     patch.object(bridge,"datetime",FixtureClock),patch.dict(os.environ,admission):
                    with patch.object(sys,"argv",["bridge","prepare","--artifact",str(artifact),"--prior-sha",prior]):
                        bridge.main()
                    with patch.object(sys,"argv",["bridge","verify-artifact","--artifact",str(artifact)]):
                        bridge.main()
                    metadata=artifact/"candidate-metadata.json"
                    content=json.loads(metadata.read_text())
                    self.assertEqual([x["path"] for x in content["files"]],publish_args.allowlist)
                    args=dict(root=artifact/"candidate",metadata_path=metadata,
                              product_id=publish_args.product_id,allowlist=publish_args.allowlist,expected_source_sha=prior)
                    bridge.main_publisher.validate_candidate_metadata(**args)
                    content["files"]=sorted(content["files"],key=lambda x:x["path"])
                    metadata.write_text(json.dumps(content))
                    with self.assertRaisesRegex(bridge.main_publisher.PublisherError,"allowlist"):
                        bridge.main_publisher.validate_candidate_metadata(**args)
                    with patch.object(sys,"argv",["bridge","verify-artifact","--artifact",str(artifact)]):
                        with self.assertRaises(bridge.main_publisher.PublisherError):bridge.main()
            finally:
                os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main()
