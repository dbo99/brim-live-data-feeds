"""Pure Stage A native sampling; no campaigns, source discovery or I/O.

Selection/calendar semantics ported from sampled_current.window/_selected.
Metadata is an authenticated saved qualification, never fresh authorization.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import math
import re

from .transport import parse_utc, format_utc, normalize_rows
from .history_acquisition import observation_quality as quality
from .history_acquisition.safety import require

VERSION = 'dendra-offline-sample-stage-a-1'
PST = timezone(timedelta(hours=-8))
SUPPORTED_WATER_YEARS = (2027, 2028)
ROLLOVER_GRACE_DAYS = 8
METHOD = 'DAILY NATIVE SAMPLE'
LABEL = 'provider-native average; averaging interval and timestamp position unknown'


def previous_year_completion(day, now):
    """Only the prior September 30, during eight fixed-PST October days.

    Calendar eligibility is not acquisition authority. Callers still need an
    exact year/date grant; no other prior-year day is admitted by this rule.
    """
    local = now.astimezone(PST).date()
    first = date(day.year, 10, 1)
    return (day == date(day.year, 9, 30) and
            0 <= (local-first).days < ROLLOVER_GRACE_DAYS)


def window(day, as_of):
    d, now = date.fromisoformat(day), parse_utc(as_of)
    wy = d.year + (d.month >= 10)
    local = now.astimezone(PST).date()
    require(wy in SUPPORTED_WATER_YEARS, 'Unsupported native water year')
    require(wy == local.year + (local.month >= 10) or previous_year_completion(d, now),
            'Wrong current WY at saved cutoff')
    target = datetime(d.year, d.month, d.day, 8, tzinfo=timezone.utc)
    lo = max(target-timedelta(minutes=30), datetime(wy-1, 10, 1, 8, tzinfo=timezone.utc))
    hi = min(target+timedelta(minutes=30), datetime(wy, 10, 1, 8, tzinfo=timezone.utc))
    require(lo < hi <= now, 'Window not closed')
    aligned = d.replace(year=1999 if d.month >= 10 else 2000)
    return dict(target_date=day, target_timestamp_utc=format_utc(target),
                start=format_utc(lo), end=format_utc(hi), water_year=wy,
                dowy=(d-date(wy-1, 10, 1)).days+1,
                plot_day_aligned=(aligned-date(1999, 10, 1)).days+1,
                truncated_at_wy_boundary=lo != target-timedelta(minutes=30))


def qualification(meta, win):
    ident = meta['identity']
    for key in ('station_id', 'stream_id'):
        require(type(ident[key]) is str and re.fullmatch('[a-f0-9]{24}', ident[key]), 'Identity')
    depth = ident['depth_cm']
    require(depth is None or type(depth) in (int, float) and math.isfinite(depth) and depth >= 0,
            'Unsupported depth')
    require(meta['public_level'] == 3 and type(meta['public_level']) is int and
            meta['is_hidden'] is False and meta['geo_protected'] is False, 'Private metadata held')
    require(meta['purpose'] == 'ReadytoUse' and meta['aggregate'] == 'Average', 'Unsupported statistic')
    require(parse_utc(meta['scope']['start']) <= parse_utc(win['start']) <
            parse_utc(win['end']) <= parse_utc(meta['scope']['end']), 'Configuration scope')
    for key in ('authority_sha256', 'metadata_sha256', 'configuration_sha256'):
        require(re.fullmatch('[a-f0-9]{64}', meta[key]) is not None, 'Metadata evidence pin')
    factors = {'Percent': 1, 'VolumetricWaterContent': 100, 'Dimensionless': None}
    require(ident['native_unit'] in factors, 'Unsupported unit')
    factor = factors[ident['native_unit']]
    require(meta['unit_multiplier'] == factor and
            (factor is None or type(meta['unit_multiplier']) in (int, float)), 'Unresolved scale')
    return factor


def sample(raw, meta, day, as_of, evidence):
    win = window(day, as_of)
    factor = qualification(meta, win)
    rows, diagnostics = normalize_rows(raw, win['start'], win['end'], quality_policy=quality.binding())
    require(diagnostics['outside_interval_rows'] == 0, 'Native row outside half-open window')
    result = dict(version=VERSION, method=METHOD, aggregate_label=LABEL,
                  aggregate='Average', support_interval='UNKNOWN', timestamp_position='UNKNOWN',
                  identity=deepcopy(meta['identity']), metadata=deepcopy(meta), window=win,
                  evidence=deepcopy(evidence), query_complete=True, status='COMPLETE_EMPTY',
                  source_records=[], source_timestamp_utc=None, source_date_pst_fixed=None,
                  selection_offset_seconds=None, value_native=None, value_vwc_percent=None,
                  value_status=None, quality=None, normalized_eligible=False,
                  identity_attributes=dict(sensor_id=None, deployment_id=None, status='UNVERIFIED'))
    if not rows:
        return result
    target = parse_utc(win['target_timestamp_utc'])
    chosen = min(rows, key=lambda r: (abs((parse_utc(r['t'])-target).total_seconds()), parse_utc(r['t'])))
    stamp = parse_utc(chosen['t'])
    result.update(source_records=[deepcopy(r) for r in raw if parse_utc(r['t']) == stamp],
                  source_timestamp_utc=chosen['t'], source_date_pst_fixed=stamp.astimezone(PST).date().isoformat(),
                  selection_offset_seconds=(stamp-target).total_seconds(), value_native=chosen.get('v'),
                  value_status=chosen['value_status'], quality=chosen['quality'])
    if chosen.get('duplicate_conflict'):
        status = 'CONFLICT'
    elif chosen['quality']['quarantined']:
        status = 'WITHHELD'
    elif chosen['value_status'] in ('null', 'missing'):
        status = 'MISSING_VALUE'
    elif chosen['value_status'] != 'number':
        status = 'INVALID'
    elif factor is None:
        status = 'HELD'
    else:
        value = chosen['v'] * factor
        status = 'AVAILABLE' if math.isfinite(value) and 0 <= value <= 100 else 'INVALID'
        if status == 'AVAILABLE':
            result.update(value_vwc_percent=value, normalized_eligible=True)
    result['status'] = status
    return result
