"""Checksum-bound exact identities for native-only acquisition, never admission.

The program catalog is private reviewed input. Original public provider records
independently establish the query identity; display names cannot supply geometry.
"""
from collections import OrderedDict
import json
import math
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .safety import Root, decode, require, sha
from ..transport import parse_utc, format_utc

VERSION = "dendra-program-master-stream-catalog-1"
ID = re.compile(r"[0-9a-f]{24}")
HASH = re.compile(r"[0-9a-f]{64}")
STATES = {"SCIENCE_READY", "NATIVE_ONLY_UNRESOLVED_DEPTH",
          "NATIVE_ONLY_UNRESOLVED_UNIT", "NATIVE_ONLY_CONFIGURATION_AMBIGUITY",
          "NATIVE_ONLY_MULTIPLE_UNRESOLVED", "EXCLUDED_NOT_TARGET_SOIL_MOISTURE"}
BOUND = 64 * 1024**2
_CHECKED_JSON = OrderedDict()


def checked_decode(body, checksum):
    """Skip repeated strict JSON hooks only for identical, rehashed bytes.

    Cache validation facts, never mutable decoded objects or filesystem stats.
    Every caller still reads the file and verifies SHA-256. Each result is fresh.
    """
    if checksum in _CHECKED_JSON:
        _CHECKED_JSON.move_to_end(checksum)
        return json.loads(body)
    value = decode(body)
    _CHECKED_JSON[checksum] = True
    if len(_CHECKED_JSON) > 128:
        _CHECKED_JSON.popitem(last=False)
    return value


def reference(ref, cache=None):
    require(isinstance(ref, dict) and {'path', 'sha256'} <= set(ref) and
            isinstance(ref['path'], str) and isinstance(ref['sha256'], str) and
            HASH.fullmatch(ref['sha256']), "Exact evidence reference")
    path = Path(ref['path'])
    require(path.is_absolute() and path == path.resolve(), "Absolute original evidence path")
    key = (str(path), ref['sha256'])
    if cache is not None and key in cache:
        return cache[key]
    with Root(path.parent) as fs:
        body = fs.read(path.name, BOUND)
    require(sha(body) == ref['sha256'], "Metadata/archive reference hash changed")
    value = checked_decode(body, ref['sha256'])
    if cache is not None:
        cache[key] = value
    return value


def pointer(value, path):
    require(isinstance(path, str) and (path == '' or path.startswith('/')), "Exact JSON pointer")
    for part in path.split('/')[1:]:
        key = part.replace('~1', '/').replace('~0', '~')
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def record(ref, cache=None):
    return pointer(reference(ref, cache), ref.get('json_pointer', ''))


def same_identity(value, row, fields=('station_id', 'stream_id', 'native_unit')):
    return isinstance(value, dict) and all(value.get(k) == row.get(k) for k in fields)


def same_source_field(ref, source_ref, suffix):
    return (isinstance(ref, dict) and
            all(ref.get(k) == source_ref.get(k) for k in ('path', 'sha256')) and
            ref.get('json_pointer') == source_ref.get('json_pointer', '') + suffix)


def accepted_science(review, row):
    require(same_identity(review, row), 'Scientific review exact stream/station/unit mismatch')
    if review.get('review_acceptance') == 'PRIOR_BASELINE_PRESERVED':
        geometry = review.get('depth_geometry', {})
        require(review.get('provider') == 'Dendra' and
                review.get('subprovider_key') == row['organization_id'] and
                geometry.get('state') == 'ALREADY_ACCEPTED_UNCHANGED' and
                geometry.get('accepted_depth_cm') == row['depth_cm'] and
                review.get('frozen_depth_cm_unchanged') == row['depth_cm'],
                'Preserved scientific geometry review mismatch')
    else:
        basis = review.get('applicability_basis', {})
        require(review.get('disposition') == 'READY_TO_SOURCE_START' and
                review.get('contradiction_found', False) is False and
                review.get('source_organization', {}).get('subprovider_key') == row['organization_id'] and
                basis.get('kind') == 'exact_stream_identity_continuity' and
                basis.get('conflicting_identity_or_depth_change_found') is False and
                basis.get('stream_id') == row['stream_id'] and
                basis.get('source_supported_depth_cm') == row['depth_cm'] and
                basis.get('supported_start') == review.get('exact_supported_start') == row['query_start'] and
                review.get('depth_cm', review.get('source_supported_depth_cm')) == row['depth_cm'],
                'Historical scientific applicability review mismatch')
        if 'identity' in review:
            require(same_identity(review['identity'], row,
                                  ('station_id', 'stream_id', 'native_unit', 'depth_cm')),
                    'Historical scientific identity mismatch')


def reviewed_start(evidence, row, science_review):
    require(isinstance(evidence, dict), 'Reviewed first-observation evidence object required')
    witness = evidence.get('journal_evidence', evidence)
    require(isinstance(witness, dict) and
            witness.get('schema_version') == 'dendra-journal-first-evidence-1' and
            same_identity(witness.get('identity'), row) and
            witness.get('result', {}).get('state') == 'FIRST_OBSERVATION' and
            witness.get('result', {}).get('timestamp') == row['query_start'],
            'First-observation witness exact identity or bound changed')
    response = witness.get('response_object', {})
    response_hash = row.get('source_start_response_sha256')
    require(isinstance(response_hash, str) and HASH.fullmatch(response_hash) and
            response.get('sha256') == response_hash and
            type(response.get('bytes')) is int and response['bytes'] > 0,
            'First-observation response binding changed')
    request = witness.get('request', {}).get('request', {})
    require(request.get('kind') == 'authority-witness' and request.get('method') == 'GET' and
            same_identity(request.get('identity'), row) and isinstance(request.get('url'), str),
            'First-observation request identity changed')
    url = urlsplit(request['url'])
    require(url.scheme == 'https' and url.netloc == 'api.dendra.science' and
            url.path == '/v2/datapoints' and not url.fragment and
            parse_qs(url.query, keep_blank_values=True) ==
            {'datastream_id': [row['stream_id']], '$sort[time]': ['1'], '$limit': ['1']},
            'First-observation query meaning changed')
    if 'journal_evidence' in evidence:
        require(evidence.get('state') == 'REVIEWED_SOURCE_START' and
                evidence.get('start') == row['query_start'] and
                evidence.get('response_sha256') == response_hash,
                'Reviewed first-observation wrapper changed')
    else:
        require(isinstance(science_review, dict) and
                science_review.get('disposition') == 'READY_TO_SOURCE_START' and
                science_review.get('exact_supported_start') == row['query_start'],
                'Unreviewed first-observation witness cannot establish reviewed start')


def target(row):
    terms = row.get('terms', {})
    ds, dq = terms.get('ds', {}), terms.get('dq', {})
    return (ds.get('Medium') == 'Soil' and
            (ds.get('Variable') == 'VolumetricWaterContent' or
             dq.get('Measurement') in ('SoilMoisture', 'VolumetricWaterContent')))


def public(row):
    level = row.get('access_levels_resolved', {}).get('public_level', row.get('public_level'))
    return (level == 3 and row.get('is_hidden') is False and
            not any(row.get(k) for k in ('is_deleted', 'deleted', 'deleted_at')))


def interval(value):
    require(isinstance(value, dict) and set(value) == {'start', 'end'} and
            all(isinstance(v, str) and format_utc(v) == v for v in value.values()),
            "Canonical exact half-open interval")
    a, b = parse_utc(value['start']), parse_utc(value['end'])
    require(a < b, "Positive interval")
    return a, b


class Snapshot:
    def __init__(self, ref):
        self.ref = ref
        # A fresh Snapshot is created for each package validation invocation.
        # Within that invocation, hundreds of exact pointers share a few bodies.
        self._references = {}
        self.document = reference(ref, self._references)
        require(self.document.get('schema_version') == VERSION and
                isinstance(self.document.get('records'), list) and
                0 < len(self.document['records']) <= 10000, "Bounded program metadata snapshot")
        self.rows = {r['stream_id']: r for r in self.document['records']}
        require(len(self.rows) == len(self.document['records']), "Unique exact stream keys")

    def identity(self, sid):
        r = self.rows[sid]
        return {k: r.get(k) for k in ('provider', 'organization_id', 'station_id', 'stream_id',
                'depth_cm', 'depth_status', 'native_unit', 'orientation', 'science_status')}

    def validate(self, sid, family):
        require(sid in self.rows and ID.fullmatch(sid), "Stream outside exact snapshot")
        r = self.rows[sid]
        require(r.get('provider') == 'Dendra' and r.get('organization_id') == family['organization_id'] and
                r.get('organization_name') == family['organization_name'] and ID.fullmatch(r['station_id']),
                "Exact provider/family/station identity")
        require(r.get('science_status') in STATES - {'EXCLUDED_NOT_TARGET_SOIL_MOISTURE'} and
                r.get('acquisition_eligible') is True and
                r.get('target_soil_moisture') is True, "Stream held or outside target measurement")
        raw = record(r['metadata_reference'], self._references)
        station = record(r['station_metadata_reference'], self._references)
        organization = record(r['organization_reference'], self._references)
        label = ('Dendra-CDFW' if organization.get('_id') == '6092b070492ae15e05876ed8'
                 else 'Dendra-' + organization.get('name', ''))
        require(organization.get('_id') == family['organization_id'] and
                organization.get('name') == family['organization_name'] and
                r.get('subprovider_key') == organization['_id'] and
                r.get('subprovider_name') == organization['name'] and
                r.get('subprovider_label') == family.get('subprovider_label') == label,
                'Original organization/subprovider label binding changed')
        require(raw.get('_id') == sid and raw.get('station_id') == r['station_id'] and
                raw.get('organization_id') == family['organization_id'] and
                station.get('_id') == r['station_id'] and
                station.get('organization_id') == family['organization_id'] and
                station.get('name') == r['station_name'] and public(raw) and public(station) and target(raw),
                "Original provider identity/access/measurement mismatch")
        require(r.get('native_unit') == raw.get('terms', {}).get('dt', {}).get('Unit'),
                "Native unit identity changed; interpretation cannot replace source unit")
        require(r.get('orientation') == raw.get('attributes', {}).get('orientation'),
                'Native orientation identity changed')
        require(isinstance(raw.get('datapoints_config'), list) and raw['datapoints_config'],
                "Missing provider query configuration")
        science_review = None
        if r['science_status'] == 'SCIENCE_READY':
            require(type(r.get('depth_cm')) in (int, float) and math.isfinite(r['depth_cm']) and
                    r['depth_cm'] >= 0 and r.get('science_review_reference'), "Prior reviewed numerical geometry required")
            science_review = record(r['science_review_reference'], self._references)
            accepted_science(science_review, r)
            depth = raw.get('attributes', {}).get('depth', {})
            scale = {'dt_Unit_Millimeter': .1, 'dt_Unit_Centimeter': 1,
                     'dt_Unit_Meter': 100}.get(depth.get('unit_tag'))
            require(type(depth.get('value')) in (int, float) and
                    math.isfinite(depth['value']) and scale is not None and
                    depth['value'] * scale == r['depth_cm'],
                    'Current source depth differs from accepted scientific assignment')
        else:
            require(r.get('depth_cm') is None, "Quarantine depth must remain NULL; no inferred depth")
        require(isinstance(r.get('query_start'), str) and format_utc(r['query_start']) == r['query_start'] and
                r.get('query_start_basis'), "Evidence-backed bounded query start required")
        start_evidence = record(r['query_start_reference'], self._references)
        if r['query_start_basis'] == 'RETAINED_REVIEWED_FIRST_OBSERVATION':
            reviewed_start(start_evidence, r, science_review)
        else:
            require(r['query_start_basis'] ==
                    'PROVIDER_CONFIGURATION_BOUNDED_QUERY_START_NOT_OBSERVATION_POR_PROOF',
                    'Unknown query lower-bound evidence class')
            begins = [format_utc(x['begins_at']) for x in raw['datapoints_config'] if x.get('begins_at')]
            suffix = '/datapoints_config' if begins else '/extent'
            expected = min(begins) if begins else format_utc(raw['extent']['begins_at'])
            require(same_source_field(r['query_start_reference'], r['metadata_reference'], suffix) and
                    start_evidence == (raw['datapoints_config'] if begins else raw['extent']) and
                    r['query_start'] == expected,
                    'Provider configuration lower bound changed')
        require(same_source_field(r['cadence_reference'], r['metadata_reference'],
                                  '/general_config_resolved/sample_interval'),
                'Cadence must reference this exact stream source field')
        cadence = raw.get('general_config_resolved', {}).get('sample_interval')
        if cadence is None:
            require(r.get('cadence_ms') is None and r.get('cadence_seconds') is None and
                    r['science_status'] != 'SCIENCE_READY',
                    'Cadence-free acquisition requires unresolved native quarantine')
            return r
        cadence = record(r['cadence_reference'], self._references)
        require(type(cadence) in (int, float) and math.isfinite(cadence) and cadence > 0 and
                r.get('cadence_ms') == cadence and r['cadence_seconds'] == cadence/1000,
                'Source cadence binding changed')
        return r
