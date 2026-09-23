#!/usr/bin/env python3
"""TEST ONLY: inject an opener and captured scientific clock at the HTTP boundary.

Never opens a URL. All observations are explicitly synthetic hourly constants.
"""
import argparse,json,sys,io,urllib.parse
from pathlib import Path
from datetime import datetime,timedelta,timezone
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'scripts/dendra')]
from bridge import BudgetClient
from coverage_collect import collect_coverage
from dendra_coverage import utc
from transport import format_utc

def run(args,settings):
    plan=json.loads(Path(args.plan).read_text());clock=utc(settings['clock']);calls=[]
    class Response(io.BytesIO):status=200;headers={}
    def fetch(request,timeout):
        q=urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query);calls.append(q)
        sid=q['datastream_id'][0]
        if sid in settings.get('fail_streams',[]):raise TimeoutError('SYNTHETIC selected-source timeout')
        lo,hi=utc(q['time[$gte]'][0]),utc(q['time[$lt]'][0]);limit=int(q['$limit'][0]);data=[]
        if sid not in settings.get('empty_streams',[]):
            value=settings.get('values',{}).get(sid,{'1'*24:10,'2'*24:11,'3'*24:-3}.get(sid,20))
            while lo<hi and len(data)<limit:
                data.append(dict(t=format_utc(lo),datastream_id=sid,v=value));lo+=timedelta(hours=1)
        return Response(json.dumps(dict(data=data,limit=limit)).encode())
    limits=plan['limits'];client=BudgetClient(args.ledger,args.budget,opener=fetch,now=lambda:clock,max_elapsed_seconds=limits['elapsed_seconds'],max_response_bytes=limits['response_bytes'],max_source_rows=limits['source_rows']);client.fetcher.sleep_fn=lambda _:None
    result=collect_coverage(plan,client,args.state,args.output)
    Path(args.output,'SIMULATED-opener-calls.json').write_text(json.dumps(dict(external_requests=0,simulated_attempts=len(calls),calls=calls),indent=2)+'\n')
    return 0 if result['complete'] else 2
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--settings',required=True);p.add_argument('mode',choices=['collect'])
    for k in ('plan','state','output','ledger'):p.add_argument('--'+k,required=True)
    p.add_argument('--budget',type=int,required=True);a=p.parse_args();raise SystemExit(run(a,json.loads(Path(a.settings).read_text())))
