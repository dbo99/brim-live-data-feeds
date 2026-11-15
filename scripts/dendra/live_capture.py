"""Offline injected finite pages ONLY. No network client constructor or fallback."""
from dataclasses import dataclass
import io
from copy import deepcopy

from .transport import DendraFetcher, parse_utc
from .history_acquisition import observation_quality as quality
from .history_acquisition.safety import decode, sha, require


@dataclass(frozen=True)
class Page:
    body: bytes
    status: int = 200
    complete: bool = True


def shape(body, sid):
    p = decode(body)
    require(type(p) is dict and set(p) <= {'data','limit','total','skip'}, 'Observation envelope')
    require(type(p.get('data')) is list and type(p.get('limit')) is int and
            0 < p['limit'] <= 2016 and len(p['data']) <= p['limit'], 'Effective page limit')
    for k in ('total','skip'):
        require(k not in p or type(p[k]) is int and p[k] >= 0, 'Pagination metadata')
    for row in p['data']:
        require(type(row) is dict and set(row) <= {'_id','datastream_id','t','v','lt','q'}, 'Row shape')
        require(row.get('datastream_id', sid) == sid, 'Wrong stream')
        parse_utc(row.get('t'))
        quality.classify(row)
        for k,v in row.items():
            if k != 'q':
                require(v is None or type(v) in (str,int,float,bool), 'Nested scalar')
                require(not isinstance(v,str) or len(v) <= 256, 'Scalar bound')
    return p


def capture(state, key, sid, win, pages, clock, *, body_cap=8*1024**2):
    require(type(pages) is list and 0 < len(pages) <= 3 and all(type(p) is Page for p in pages),
            'Finite offline Page inputs required')
    raw, receipts = [], []
    class Response(io.BytesIO):
        status, headers = 200, {}
    def opener(request, timeout):
        require(len(receipts) < len(pages), 'Missing saved page')
        ordinal = state.reserve(key, request.full_url, clock, body_cap)
        page = pages[len(receipts)]
        require(type(page.body) is bytes, 'Bytes required')
        body = page.body[:body_cap]
        try:
            require(len(page.body) <= body_cap and page.complete, 'Partial or over-cap body')
            require(page.status == 200, 'Non-200 including redirect refused')
            payload = shape(body, sid)
        except Exception:
            state.receive(ordinal, body, status='AMBIGUOUS')
            raise
        state.receive(ordinal, body, status='RECEIVED', rows=len(payload['data']))
        receipts.append(dict(ordinal=ordinal, sha256=sha(body), bytes=len(body), url=request.full_url))
        raw.extend(deepcopy(payload['data']))
        return Response(body)
    try:
        envelope = DendraFetcher(opener=opener, max_attempts=1, max_pages=3, page_size=2016,
                                 now_fn=lambda: parse_utc(clock), sleep_fn=lambda _: None).fetch_interval(
                                     sid, win['start'], win['end'])
        require(len(receipts) == len(pages), 'Unused pages cannot claim completion')
        return raw, dict(kind='OFFLINE_INJECTED_PAGES', pages=receipts,
                         replay_at_utc=clock, envelope_sha256=envelope['content_sha256'])
    except Exception:
        state.outcome(key, clock, status='FAILED')
        raise
