"""Private offline bridge: immutable Candidate-3 bulk rows + sealed API history.

register() pins a delivery without creating API state. bootstrap() requires an
explicit per-stream [start, end) acknowledgement and an independently prepared
routine seed. Plan/assemble/prepare/resume with routine_update, then reconcile()
checkpoints that preparation. No provider transport or publication is supplied.

history() is an authority-tagged local view, NOT a new browser/delivery schema.
Bulk records remain byte-equivalent JSON values with all their original fields;
API records retain the existing R daily shape. The original bulk record locator
remains attached even when superseded. Bulk query_complete and quality fields
are never interpreted as API coverage. Only real sealed views establish that.

Pins attest integrity inside trusted local storage, not authenticity. Callers
must supply reviewed pins and fresh task-owned output roots. The real cutover,
live acquisition, consumer mapping and publication remain separate decisions.
"""
from collections import Counter
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

from . import candidate3_delivery as delivery, routine_update as routine
from .safety import Root, decode, digest, require
from ..transport import format_utc, parse_utc

VERSION = "dendra-candidate3-routine-1"
# Numerical diagnostics/calendar shared by both existing daily representations.
# Source provenance and query completion deliberately are NOT material values.
MATERIAL = ("date", "water_year", "dowy", "water_day", "water_year_days",
            "water_day_aligned", "mean_native", "mean_percent", "mean_value",
            "n_valid", "n_total", "n_null", "n_invalid", "n_missing",
            "n_duplicate_conflicts", "n_duplicate_rows", "n_out_of_range",
            "expected_samples", "cadence_seconds", "cadence_source",
            "coverage_fraction", "temporal_span_fraction", "plot_eligible", "flags")


class Baseline:
    """Read existing delivery metadata; read/rehash one stream on demand.

    No source export or Parquet rebuild. audit() checks all frozen rows locally.
    A pinned manifest and its component hashes are required on every opening.
    """
    def __init__(self, manifest_ref, candidate_manifest_sha256):
        self.ref = deepcopy(manifest_ref)
        self.root = Path(manifest_ref["path"]).parent
        m = self.manifest = routine.read(manifest_ref)
        require(m["schema_version"] == delivery.CONTRACT and
                m["candidate_manifest_sha256"] == candidate_manifest_sha256 and
                m["publication_eligible"] is False and
                0 < len(m["files"]) < delivery.MAX_FILES and
                m["closure_sha256"] == digest(m["files"]), "Candidate-3 manifest binding")
        self.files = {f["path"]: f for f in m["files"]}
        require(len(self.files) == len(m["files"]), "Duplicate delivery path")
        with Root(self.root) as fs:
            files, directories = delivery.output_inventory(fs)
        expected = set(self.files) | {Path(manifest_ref["path"]).name}
        parents = {str(p) for f in expected for p in Path(f).parents if str(p) != "."}
        require(files == expected and directories == parents, "Delivery closure changed")
        self.source = self.component(m["source_binding"])
        require(self.source["candidate_manifest"]["sha256"] == candidate_manifest_sha256,
                "Bulk source manifest differs")
        self.streams = {}
        index = self.component(m["station_index"])
        stations, states, count, shards = set(), Counter(), 0, 0
        used = {m["source_binding"]["path"], m["station_index"]["path"]}
        for station in index["stations"]:
            require(station["station_id"] not in stations, "Duplicate baseline station")
            stations.add(station["station_id"])
            for entry in station["streams"]:
                sid = entry["stream_id"]
                s = self.component(entry["descriptor"])
                require(sid not in self.streams and s["identity"]["stream_id"] == sid and
                        s["identity"]["station_id"] == station["station_id"], "Baseline identity closure")
                self.streams[sid] = s
                used.add(entry["descriptor"]["path"])
                totals = Counter()
                for part in s["history"]:
                    require(part["file"] == self.files.get(part["file"]["path"]), "History pin closure")
                    require(part["file"]["path"] not in used, "Duplicate history shard")
                    used.add(part["file"]["path"])
                    totals.update(part["states"])
                    require(sum(part["states"].values()) == part["rows"], "Shard census")
                require(dict(totals) == s["states"] and sum(totals.values()) == s["rows"], "Stream census")
                states.update(totals)
                count += s["rows"]
                shards += len(s["history"])
        require(used == set(self.files) and shards == m["wy_shards"] and
                m["census"] == dict(stations=len(stations), streams=len(self.streams), rows=count,
                                    states={s: states[s] for s in delivery.STATES}), "Baseline census/closure")

    def component(self, descriptor):
        require(descriptor == self.files.get(descriptor["path"]), "Component not in pinned delivery")
        with Root(self.root) as fs:
            body = fs.read(descriptor["path"], delivery.MAX_FILE)
        require(delivery.file_descriptor(descriptor["path"], body) == descriptor, "Delivery component changed")
        return decode(body)

    def history(self, sid):
        spec = self.streams[sid]
        rows = {}
        for part in spec["history"]:
            shard = self.component(part["file"])
            require(shard["stream_id"] == sid and shard["station_id"] == spec["identity"]["station_id"] and
                    shard["water_year"] == part["water_year"] and len(shard["records"]) == part["rows"],
                    "History shard identity/census")
            states = Counter()
            for row in shard["records"]:
                delivery.validate_record(row, self.source["row_schema"], spec["identity"])
                require(row["date"] not in rows and row["water_year"] == part["water_year"], "Duplicate/mispartitioned day")
                states[row["daily_status"]] += 1
                rows[row["date"]] = dict(authority="CANDIDATE3_BULK", record=row,
                    bulk_evidence=dict(manifest=self.ref, shard=part["file"], date=row["date"], row_sha256=digest(row)))
            require(dict(states) == part["states"], "Baseline row state parity")
        return rows

    def audit(self):
        states, rows = Counter(), 0
        for sid in self.streams:
            records = self.history(sid)
            rows += len(records)
            states.update(x["record"]["daily_status"] for x in records.values())
        require(rows == self.manifest["census"]["rows"] and
                dict(states) == self.manifest["census"]["states"], "Baseline state parity")
        return deepcopy(self.manifest["census"])


def _save(state, output_root):
    routine.protect_parent(output_root, state["baseline"])
    if state["parent"]:
        routine.protect_parent(output_root, state["parent"])
    state = dict(state, bridge_id=digest(state))
    routine.fresh(output_root, {"bridge.json": state})
    return routine.reference(Path(output_root) / "bridge.json")


def register(manifest_ref, *, candidate_manifest_sha256, output_root):
    """Record bulk authority only. No implicit cutover, API flags or frontier."""
    baseline = Baseline(manifest_ref, candidate_manifest_sha256)
    return _save(dict(schema_version=VERSION, baseline=manifest_ref,
        candidate_manifest_sha256=candidate_manifest_sha256, census=baseline.manifest["census"],
        parent=None, bootstrap=None, api=None, changes={}, publication_eligible=False), output_root)


def _identity(baseline, sid, identity):
    require(sid in baseline.streams, "API stream absent from Candidate-3")
    bulk = baseline.streams[sid]["identity"]
    admission = _admission(bulk, identity)
    require(admission == "EXACT_IDENTITY_MATCH", admission)


def _admission(bulk, identity):
    if bulk["native_unit"] == "Dimensionless":
        return "DIMENSIONLESS_OUTSIDE_NUMERIC_UPDATE_ROUTE"
    if identity is None:
        return "API_INVENTORY_ADMISSION_REQUIRED"
    if (bulk["native_unit"] not in ("Percent", "VolumetricWaterContent") or
            any(bulk[k] != identity[k] for k in ("station_id", "stream_id", "depth_cm", "native_unit")) or
            bulk["conversion_multiplier"] != (1 if identity["native_unit"] == "Percent" else 100)):
        return "BULK_API_IDENTITY_REVIEW_REQUIRED"
    return "EXACT_IDENTITY_MATCH"


def load(bridge_ref, *, inventory):
    state = routine.read(bridge_ref)
    require(state["schema_version"] == VERSION and state["publication_eligible"] is False and
            state["bridge_id"] == digest({k: v for k, v in state.items() if k != "bridge_id"}), "Bridge integrity")
    baseline = Baseline(state["baseline"], state["candidate_manifest_sha256"])
    require(state["census"] == baseline.manifest["census"], "Bridge baseline census differs")
    if state["api"] is None:
        require(state["bootstrap"] is None, "Bulk registration cannot claim API bootstrap")
        return state, baseline, None, None
    g, _ = routine.load(state["api"], inventory)
    _, daily = routine.prepared(g["daily"], inventory)
    seed = routine.read(state["bootstrap"]["seed"])
    require(seed["schema_version"] == routine.VERSION and seed["parent"] is None and seed["cycle"] is None and
            seed["generation_id"] == digest({k: v for k, v in seed.items() if k != "generation_id"}) and
            all(g["sources"].get(k) == v for k, v in seed["sources"].items()), "Bootstrap seed/source lineage differs")
    require(set(g["streams"]) == set(state["bootstrap"]["intervals"]), "Explicit bootstrap stream closure")
    for sid, s in g["streams"].items():
        _identity(baseline, sid, s["identity"])
        initial = seed["streams"][sid]
        require(state["bootstrap"]["intervals"][sid] == {k: initial[k] for k in ("start", "end")} and
                initial["identity"] == s["identity"] and s["start"] == initial["start"],
                "API identity/start differs from explicit bootstrap")
    return state, baseline, g, daily


def history(baseline, generation, daily, sid):
    """One record per day; actual complete API days supersede only their dates."""
    rows = baseline.history(sid)
    if generation is None or sid not in generation["streams"]:
        return rows
    stream = generation["streams"][sid]
    for row in daily["rows"].get(sid, []):
        lo = routine.boundary(row["date"] + "T08:00:00.000Z")
        hi = lo + timedelta(days=1)
        views = [dict(v, start=format_utc(max(lo, parse_utc(v["start"]))),
                      end=format_utc(min(hi, parse_utc(v["end"])))) for v in stream["views"]
                 if parse_utc(v["start"]) < hi and parse_utc(v["end"]) > lo]
        complete = not routine.coverage(views, format_utc(lo), format_utc(hi))["gaps"]
        require(row["query_complete"] == complete and row["identity"] == stream["identity"] and
                hi <= routine.completed_boundary(daily["as_of"]), "Prepared day coverage/identity/boundary differs")
        if not complete:
            continue
        original = rows.get(row["date"])
        rows[row["date"]] = dict(authority="SEALED_API", record=row,
            state="DAILY_VALUE_WITHHELD" if "provider_quality_unreviewed" in row["flags"] else
                  "ACCEPTED" if row["presentation_eligible"] else "MISSING_OR_REJECTED",
            bulk_evidence=None if original is None else original["bulk_evidence"],
            api_evidence=dict(daily=generation["daily"], source_intervals=row["source_intervals"]))
    return dict(sorted(rows.items()))


def _changes(baseline, before, old_daily, after, new_daily):
    result = {}
    for sid, s in after["streams"].items():
        old = history(baseline, before, old_daily, sid)
        new = history(baseline, after, new_daily, sid)
        changed = [day for day in new if day not in old or
                   {k: old[day]["record"][k] for k in MATERIAL} !=
                   {k: new[day]["record"][k] for k in MATERIAL}]
        result[sid] = dict(material_changed_dates=changed, outcome=s["outcome"],
            last_attempted_source_check=s["last_attempted_source_check"],
            last_successful_complete_query=s["last_successful_complete_query"],
            coverage=s["coverage"], attempt_evidence=s.get("attempt_evidence"),
            replacement=after["changes"].get(sid),
            routine_recomputed_dates=(new_daily.get("routine_recomputed_dates") or {}).get(sid, []))
    return result


def bootstrap(bridge_ref, seed_ref, intervals, *, inventory, output_root):
    """Acknowledge exact independently sealed intervals, never a bulk-row date."""
    state, baseline, old, _ = load(bridge_ref, inventory=inventory)
    require(old is None, "Already bootstrapped; use routine cycles")
    g, _ = routine.load(seed_ref, inventory)
    handoff, daily = routine.prepared(g["daily"], inventory)
    require(g["parent"] is None and g["cycle"] is None and not g["changes"] and
            set(intervals) == set(g["streams"]) and intervals, "Explicit initial API seed/intervals required")
    require(handoff["evidence_binding"]["evidence_manifest_sha256"] in g["sources"], "Seed preparation source binding")
    for sid, s in g["streams"].items():
        _identity(baseline, sid, s["identity"])
        require(set(intervals[sid]) == {"start", "end"} and
                intervals[sid] == dict(start=s["start"], end=s["end"]) and
                routine.boundary(s["start"]) < routine.boundary(s["end"]) <= routine.completed_boundary(daily["as_of"]) and
                not s["coverage"]["gaps"] and s["outcome"] == "VERIFIED_SEED", "Explicit bootstrap coverage differs")
    routine.protect_parent(output_root, seed_ref)
    routine.protect_output(output_root, g["sources"], inventory)
    routine.protect_parent(output_root, dict(path=str(Path(g["daily"]["root"]) / "handoff.json")))
    state.pop("bridge_id")
    state.update(parent=bridge_ref, api=seed_ref, bootstrap=dict(seed=seed_ref, intervals=deepcopy(intervals)),
                 changes=_changes(baseline, None, None, g, daily))
    return _save(state, output_root)


def reconcile(bridge_ref, generation_ref, daily_ref, *, inventory, output_root):
    """Admit the existing engine's prepared child; never run acquisition here."""
    state, baseline, old, old_daily = load(bridge_ref, inventory=inventory)
    require(old is not None, "EXPLICIT_BOOTSTRAP_REQUIRED")
    g, _ = routine.load(generation_ref, inventory)
    cycle, _, _ = routine.resume(g["cycle"], inventory)
    handoff, daily = routine.prepared(daily_ref, inventory)
    require(g["parent"] == state["api"] == cycle["parent"] and
            set(g["streams"]) == set(old["streams"]) and
            handoff["evidence_binding"]["evidence_manifest_sha256"] == generation_ref["sha256"],
            "Routine preparation is not a child of this bridge")
    routine.protect_parent(output_root, bridge_ref)
    routine.protect_parent(output_root, state["baseline"])
    routine.protect_parent(output_root, generation_ref)
    routine.protect_parent(output_root, dict(path=str(Path(daily_ref["root"]) / "handoff.json")))
    routine.protect_output(output_root, g["sources"], inventory)
    # Keep all original R publication-loss assessments in the pinned daily file.
    # This local checkpoint NEVER grants publication eligibility or approval.
    candidate = dict(g, daily=daily_ref)
    changes = _changes(baseline, old, old_daily, candidate, daily)
    routine.fresh(output_root, {})
    api = routine.checkpoint_preparation(generation_ref, daily_ref, inventory=inventory,
                                        output_root=Path(output_root) / "api")
    state.pop("bridge_id")
    state.update(parent=bridge_ref, api=api, changes=changes)
    return _save(state, Path(output_root) / "state")


def status(state, baseline, generation, *, inventory):
    """Keep non-selected/native-only streams represented without invented API state."""
    roster, result = inventory.roster(), {}
    for sid, s in baseline.streams.items():
        admission = _admission(s["identity"], roster.get(sid))
        api = None if generation is None else generation["streams"].get(sid)
        result[sid] = dict(baseline_rows=s["rows"], baseline_states=s["states"], api_admission=admission,
            api_status=admission if admission != "EXACT_IDENTITY_MATCH" else
                       "EXPLICIT_BOOTSTRAP_REQUIRED" if api is None else api["outcome"],
            api_coverage=None if api is None else api["coverage"])
    return result
