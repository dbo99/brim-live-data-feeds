"""One finite offline range planner/merge path; no scheduler or network fallback."""
from copy import deepcopy
from datetime import date, timedelta

from .history_acquisition.safety import require
from .live_sampling import window
from .transport import parse_utc


def diagnostic_window(plan, now):
    """Run-bound input only; never a family default or operational target set."""
    d = plan['diagnostic_interval']
    require(type(d) is dict and set(d) == {'version','start_day','through_day','comparison_days'} and
            d['version'] == 'dendra-single-stream-interval-diagnostic-1' and
            'range_targets' not in plan and plan.get('admission') is False and
            plan.get('publication') is False and len(plan['roster']) == 1,
            'Isolated single-stream comparison-only diagnostic')
    selected = dates(plan['water_year'],now,start=d['start_day'],through=d['through_day'])
    require(len(selected) <= 28 and plan['target_day'] == selected[-1] and
            type(d['comparison_days']) is list and 0 < len(d['comparison_days']) <= 2 and
            d['comparison_days'] == sorted(set(d['comparison_days'])) and
            all(day in selected for day in d['comparison_days']), 'Finite diagnostic dates/comparators')
    return dict(start=window(selected[0],now)['start'],end=window(selected[-1],now)['end'])


def diagnostic_native_rows(pages):
    """Collapse inclusive page overlap, retaining true native multiplicities.

    Called only after the existing paginator establishes interval completion.
    The next page must replay every member of the prior trailing group; it may
    complete a split group. Changed/missing occurrences hold, never deduplicate
    differing source records or infer a provider correction.
    """
    from collections import Counter
    from .history_acquisition.safety import encode
    rows = []
    for page in pages:
        if rows:
            cursor = parse_utc(rows[-1]['t'])
            old = [r for r in rows if parse_utc(r['t']) == cursor]
            overlap = [r for r in page if parse_utc(r['t']) == cursor]
            before, after = Counter(map(encode,old)), Counter(map(encode,overlap))
            require(not (before-after), 'Diagnostic inclusive boundary group changed or disappeared')
            rows = [r for r in rows if parse_utc(r['t']) < cursor]
        rows.extend(deepcopy(page))
    return rows


def diagnostic_projection(raw, admission, plan, evidence):
    """Pure diagnostic candidates; no cell, accepted pointer or coverage writer."""
    from .live_product import candidate_sample_v2
    broad = diagnostic_window(plan,plan['not_before'])
    require(evidence.get('query_complete') is True and evidence.get('diagnostic_only') is True and
            all(parse_utc(broad['start']) <= parse_utc(r['t']) < parse_utc(broad['end']) for r in raw),
            'Complete bounded diagnostic evidence required')
    require(admission['metadata']['identity']['stream_id'] == next(iter(plan['roster'])) and
            parse_utc(admission['metadata']['scope']['start']) <= parse_utc(broad['start']) and
            parse_utc(admission['metadata']['scope']['end']) >= parse_utc(broad['end']),
            'Whole-interval metadata qualification required')
    result = {}
    for day in dates(plan['water_year'],plan['not_before'],start=plan['diagnostic_interval']['start_day'],
                     through=plan['diagnostic_interval']['through_day']):
        win = window(day,plan['not_before'])
        selected = [r for r in raw if parse_utc(win['start']) <= parse_utc(r['t']) < parse_utc(win['end'])]
        result[day] = dict(native_records=deepcopy(selected),
            candidate=candidate_sample_v2(selected,admission,day,plan['not_before'],evidence))
    return dict(diagnostic_only=True,admission=False,publication_eligible=False,
                operational_coverage_changed=False,daily_diagnostics=result)


def series_binding(roster, water_year, attempt_limit=4096, *, version=1):
    from .live_metadata import validate_roster
    from .live_state import P1,SEASON
    validate_roster(roster)
    require(type(version) is int and version in (1,2),'Explicit persistence version')
    if version==2:
        require(water_year==2027 and attempt_limit==SEASON['attempts'], 'Exact P1 WY/attempt contract')
        return dict(lineage=P1,roster=deepcopy(roster),water_year=water_year,
                    attempt_limit=attempt_limit,season_limits=deepcopy(SEASON))
    require(type(water_year) is int and 2000 <= water_year <= 2100 and
            type(attempt_limit) is int and 1 <= attempt_limit <= 4096, 'Finite WY/ledger bound')
    return dict(lineage='dendra-offline-range-series-1',roster=deepcopy(roster),
                water_year=water_year,attempt_limit=attempt_limit)


def dates(water_year, now, *, days_back=None, start=None, through=None):
    at = parse_utc(now); latest = at.date()
    if at < parse_utc(latest.isoformat()+'T08:30:00Z'): latest -= timedelta(days=1)
    lo, hi = date(water_year-1,10,1), date(water_year,9,30)
    require(lo <= latest <= hi, 'Run cutoff outside explicit current WY')
    if days_back is not None:
        require(start is None and through is None and type(days_back) is int and 1 <= days_back <= 366,
                'Choose days-back OR explicit inclusive dates')
        first, last = max(lo,latest-timedelta(days=days_back-1)), latest
    elif start is not None or through is not None:
        require(type(start) is str and type(through) is str, 'Both inclusive dates required')
        first,last = date.fromisoformat(start),date.fromisoformat(through)
        require(first.isoformat()==start and last.isoformat()==through, 'Canonical dates')
    else: first,last = lo,latest
    require(lo <= first <= last <= hi and last <= latest, 'Reversed/cross-WY/future target range')
    result = [(first+timedelta(days=i)).isoformat() for i in range((last-first).days+1)]
    for day in result: window(day,now)
    return result


def targets(roster, water_year, now, cells, *, days_back=None, start=None, through=None, empty_cooldown=7):
    require(type(empty_cooldown) is int and 1 <= empty_cooldown <= 31, 'Finite empty cooldown')
    all_days = dates(water_year,now)
    if days_back is not None or start is not None or through is not None:
        selected = dates(water_year,now,days_back=days_back,start=start,through=through)
        return [[sid,d] for d in selected for sid in sorted(roster)]
    recent = all_days[-7:]; gaps = []
    for sid in sorted(roster):
        for day in all_days[:-7]:
            c = cells.get(sid+'/'+day);a = None if c is None else c['latest_attempt']
            outcome = 'UNQUERIED' if a is None else a['outcome']
            if outcome in ('AVAILABLE','INACTIVE'): continue
            if outcome == 'COMPLETE_EMPTY' and (parse_utc(now)-parse_utc(a['clock'])).days < empty_cooldown: continue
            gaps.append([sid,day]);break
    # Oldest repair gets capacity before recent overlap, so small runs cannot starve it.
    return sorted(gaps,key=lambda x:(x[1],x[0]))+[[sid,d] for d in recent for sid in sorted(roster)]


def validate_range_plan(plan, now, *, live=False):
    series_binding(plan['roster'],plan['water_year'],plan['ledger_attempt_limit'],version=plan.get('persistence_version',1))
    work = plan['range_targets']; eligible = set(dates(plan['water_year'],plan['not_before']))
    require(type(work) is list and 0 < len(work) <= 732 and
            all(type(x) is list and len(x)==2 and x[0] in plan['roster'] and x[1] in eligible for x in work) and
            len({tuple(x) for x in work}) == len(work), 'Finite exact stream/day target set')
    require(type(plan['stream_day_budget']) is int and 1 <= plan['stream_day_budget'] <= 732 and
            plan['target_day'] == max(x[1] for x in work), 'Finite stream/day budget/latest target')
    if live:
        require(plan.get('persistence_version')==2 and plan['representation']=='LIVE_PROVIDER',
                'Live range requires explicit format 2 and native provenance')
    else:
        require(plan['representation'] in ('SYNTHETIC_INJECTED','MIXED_SAVED_AND_SYNTHETIC'), 'Offline provenance label required')
    require(plan['status']==('APPROVED' if live else 'OFFLINE_ONLY'), 'Separate range authority required')


def validate_family_campaign(campaign, *, live=False):
    """Conserve four child reservations across independent, unchanged P1 pairs.

    Structural validation is not grant issuance. Each child needs its own exact
    external approval and actual clock checks immediately before execution.
    """
    from pathlib import Path
    from .live_provider import validate_plan,source_fingerprint
    from .live_metadata import FAMILY_PAIR
    require(campaign['version']=='dendra-family-campaign-1' and
            campaign['source_sha256']==source_fingerprint(), 'Exact family runtime')
    root=Path(campaign['output_root'])
    require(root.is_absolute() and root==root.resolve(), 'Canonical family root')
    children=campaign['children'];days=campaign['target_dates']
    require(type(children) is list and 1<=len(children)<=4 and
            type(days) is list and len(days) in (1,2) and all(type(d) is str for d in days) and
            days==sorted(set(days)), 'Finite family windows/dates')
    positions=2*len(days)
    seen_roots=set();seen_streams=set();grants=set();family=None;previous=None;total_gets=0;total_bytes=0
    for child in children:
        require(child['version']=='dendra-family-child-1' and child['output_root']==str(root) and
                child['source_sha256']==campaign['source_sha256'], 'Child source/root binding')
        lo,hi=map(parse_utc,(child['not_before'],child['expires_at']))
        require(0<(hi-lo).total_seconds()<=300 and
                (previous is None or (lo-previous).total_seconds()>=1), 'Serial finite child windows')
        previous=hi;members=child['members']
        require(type(members) is list and 1<=len(members)<=8, 'Finite child pair count')
        gets=body=rows=0
        for p in members:
            validate_plan(p,p['not_before'],live=live)
            require(p['not_before']==child['not_before'] and p['expires_at']==child['expires_at'] and
                    p.get('persistence_version')==2 and p['stream_day_budget']==positions,
                    'Shared child deadline and unchanged P1 persistence')
            target=Path(p['output_root'])
            require(target.parent==root and target==target.resolve() and str(target) not in seen_roots,
                    'Distinct immediate pair roots')
            require(p['grant_id'] not in grants, 'Distinct member run identity')
            seen_roots.add(str(target));grants.add(p['grant_id'])
            require(p['range_targets']==[[sid,d] for d in days for sid in sorted(p['roster'])],
                    'Exact selected-date member targets')
            for sid,r in p['roster'].items():
                require(r.get('selection',{}).get('policy')==FAMILY_PAIR and sid not in seen_streams,
                        'Explicit family membership without duplicate stream writes')
                if family is None:family=r['family']
                require(r['family']==family, 'Single campaign family')
                seen_streams.add(sid)
            limits=p['limits']
            require(limits['metadata']==5 and limits['attempts']==5+positions*limits['pages_per_stream'] and
                    limits['observation_rows']==positions*limits['pages_per_stream']*2016,
                    'Complete finite member reservation')
            gets+=limits['attempts'];body+=limits['aggregate_bytes'];rows+=limits['observation_rows']
        require(child['reservation']==dict(gets=gets,body_bytes=body,observation_rows=rows) and
                gets<=150 and body<=16*1024**2, 'Child reservation conservation')
        total_gets+=gets;total_bytes+=body
    require(total_gets<=500 and total_bytes<=64*1024**2 and campaign['reservation']==
            dict(gets=total_gets,body_bytes=total_bytes,streams=len(seen_streams),cells=len(days)*len(seen_streams)),
            'Aggregate family reservation conservation')
    return campaign['reservation']
