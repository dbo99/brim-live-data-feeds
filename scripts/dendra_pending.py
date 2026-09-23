#!/usr/bin/env python3
"""Bounded, unacknowledged query handoff. Never a data or validation receipt.

Restore only after restore-public, before planning. Invalid/absent handoffs hold
acquisition by default. An explicitly reviewed refetch is reported, never a cache hit.
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, shutil, sys, tempfile
from datetime import timedelta
from pathlib import Path
import dendra_coverage as cp
import soil_moisture_operations as ops
sys.path.insert(0, str(Path(__file__).resolve().parent/'dendra'))
from coverage_collect import BoundChunks
from transport import _content_hash

VERSION='dendra-pending-collection-1'
MAX_FILES=4096
MAX_BYTES=256*1024**2
MAX_ROWS=1_000_000
RETENTION_DAYS=14  # Proposed artifact retention, not a durable scientific archive.
LIMITS=dict(files=MAX_FILES,bytes=MAX_BYTES,rows=MAX_ROWS,retention_days=RETENTION_DAYS)
require=cp.require
def sha(raw):return hashlib.sha256(raw).hexdigest()
def read(path,cap=8*1024**2):
    path=ops.plain(path);require(path.is_file() and path.stat().st_size<=cap,'Missing/oversized pending input')
    with path.open('rb') as f:raw=f.read(cap+1)
    require(len(raw)<=cap,'Pending input grew beyond limit')
    return raw
def load(path):return json.loads(read(path))
def sources():
    root=Path(__file__).resolve().parent
    return cp.collector_binding()|{n:sha((root/n).read_bytes()) for n in ('dendra_pending.py','dendra/archive.R','dendra_workflow.py')}
def selection(catalog):
    # Retrieval clock/metadata refresh alone must not discard completed queries.
    return dict(streams=sorted((cp.identity(s) for s in catalog['streams']),key=lambda s:s['datastream_id']),
                integration=catalog['integration'],observation_windows=catalog.get('observation_windows'))
def parent_at(state):
    p=load(Path(state)/'published.json')
    require(p.get('state_role')=='published' and re.fullmatch('[0-9a-f]{40}',p.get('publication_commit','')) and
            re.fullmatch('[0-9a-f]{64}',p.get('index_sha256','')),'Acknowledged public parent required')
    # Actual public restoration owns semantic/committed validation. Bind the index too.
    import dendra_state as ds
    import dendra_candidate as dc
    require(dc.sha(ds.candidate_at(Path(state),p)/dc.FIXED[0])==p['index_sha256'],'Pending parent index mismatch')
    return p
def task_from(envelope,catalog,epoch):
    sid=envelope.get('datastream_id');streams={s['datastream_id']:s for s in catalog['streams']}
    require(sid in streams,'Pending selection changed')
    s=streams[sid];b=envelope['collection_binding'];lo,hi=cp.day(b['start']),cp.day(b['end'])
    require(s['public_level']==3 and s['source_is_hidden'] is False,'Pending source no longer public')
    require(0<(hi-lo).days<=(90 if s['parameter']=='soil_temperature' else 30),'Pending interval limit')
    require(b==dict(version=cp.VERSION,source='dendra',identity=cp.identity(s),policy_version=cp.POLICY,
                    calendar=cp.CALENDAR,start=str(lo),end=str(hi),request_generation=epoch,
                    collector_sources=cp.collector_binding()),'Pending identity/policy/epoch/code changed')
    return dict(task_id=cp.digest(b),checkpoint_key='coverage-'+cp.digest(b),stream=s,start=str(lo),end=str(hi),collection_binding=b)
def validate_envelope(raw,catalog,epoch,now):
    x=json.loads(raw);task=task_from(x,catalog,epoch)
    require(x.get('query_complete') is True and x.get('content_sha256')==_content_hash(x),'Incomplete/corrupt pending response')
    BoundChunks(Path('.'),task).validate_envelope(x)
    require(cp.utc(x['retrieval_last_utc'])<=cp.utc(now),'Future pending retrieval')
    require(len(x['rows'])<=MAX_ROWS,'Pending row limit')
    return x,task
def closure(root,expected):
    found=set();directories=set()
    for n in expected:
        directories.update(str(p) for p in Path(n).parents if str(p)!='.')
    for p in root.rglob('*'):
        ops.plain(p);n=p.relative_to(root).as_posix()
        require(p.is_file() or p.is_dir(),'Nonregular pending member')
        if p.is_file():found.add(n)
        else:require(n in directories,'Unexpected pending directory')
        require(len(found)<=MAX_FILES+1,'Pending file limit')
    require(found==set(expected),'Pending snapshot closure')

def export(state,catalog,destination,now):
    state=ops.plain(state);dest=ops.plain(destination);require(not dest.exists(),'Fresh pending export required')
    parent=parent_at(state);catalog=load(catalog);epoch='parent-'+parent['index_sha256'][:32]
    files=[];total=rows=0;oldest=cp.utc(now);payload=[]
    chunks=state/'intervals/coverage-v1/chunks';ops.plain(chunks)
    for p in sorted(chunks.glob('*/*.json')):
        require(len(files)<MAX_FILES,'Pending file limit');raw=read(p,64*1024**2)
        x,task=validate_envelope(raw,catalog,epoch,now)
        name='chunks/'+x['datastream_id']+'/'+task['checkpoint_key']+'.json'
        require(p.relative_to(chunks).as_posix()==name[len('chunks/'):],'Pending path binding')
        total+=len(raw);rows+=len(x['rows']);require(total<=MAX_BYTES and rows<=MAX_ROWS,'Pending aggregate bound')
        oldest=min(oldest,cp.utc(x['retrieval_first_utc']))
        files.append(dict(path=name,bytes=len(raw),rows=len(x['rows']),sha256=sha(raw)))
        payload.append((name,raw))
    expires=oldest+timedelta(days=RETENTION_DAYS)
    require(cp.utc(now)<expires,'Pending retrieval retention expired; explicit refetch review required')
    manifest=dict(version=VERSION,role='unacknowledged_complete_queries',parent=parent,epoch=epoch,
                  selection=selection(catalog),sources=sources(),created_at_utc=now,
                  expires_at_utc=expires.isoformat().replace('+00:00','Z'),limits=LIMITS,files=files,
                  acknowledged_parent_advanced=False)
    dest.parent.mkdir(parents=True,exist_ok=True)
    temp=Path(tempfile.mkdtemp(prefix=dest.name+'.export-',dir=dest.parent))
    try:
        for name,raw in payload:
            p=temp/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
        raw=(json.dumps(manifest,sort_keys=True,separators=(',',':'))+'\n').encode()
        require(len(raw)<=8*1024**2,'Pending manifest bound');(temp/'manifest.json').write_bytes(raw)
        os.replace(temp,dest)
    finally:
        if temp.exists():shutil.rmtree(temp)
    return dict(status='exported',manifest_sha256=sha(raw),completed_intervals=len(files),rows=rows,bytes=total,
                acknowledged_parent_advanced=False,expires_at_utc=manifest['expires_at_utc'])

def restore(snapshot,expected,state,catalog,now):
    state=ops.plain(state);src=ops.plain(snapshot);target=state/'intervals/coverage-v1';ops.plain(target)
    require(not target.exists(),'Pending restore destination must be fresh')
    parent=parent_at(state);catalog=load(catalog);raw=read(src/'manifest.json');require(sha(raw)==expected,'Pending manifest checksum')
    m=json.loads(raw)
    require(set(m)=={'version','role','parent','epoch','selection','sources','created_at_utc','expires_at_utc','limits','files','acknowledged_parent_advanced'},'Pending manifest fields')
    require(m['version']==VERSION and m['role']=='unacknowledged_complete_queries' and m['acknowledged_parent_advanced'] is False and m['limits']==LIMITS,'Pending snapshot type/limits')
    require(m['parent']==parent and m['epoch']=='parent-'+parent['index_sha256'][:32],'Pending parent changed')
    require(m['selection']==selection(catalog) and m['sources']==sources(),'Pending selection/policy/code changed')
    require(cp.utc(m['created_at_utc'])<=cp.utc(now)<cp.utc(m['expires_at_utc'])<=cp.utc(m['created_at_utc'])+timedelta(days=RETENTION_DAYS),'Pending expired/future snapshot')
    require(isinstance(m['files'],list) and len(m['files'])<=MAX_FILES,'Pending file limit')
    payload=[];names=set();total=rows=0
    for f in m['files']:
        require(set(f)=={'path','bytes','rows','sha256'} and isinstance(f['path'],str) and
                re.fullmatch(r'chunks/[0-9a-f]{24}/coverage-[0-9a-f]{64}\.json',f['path']) and f['path'] not in names,'Pending file identity')
        names.add(f['path']);data=read(src/f['path'],64*1024**2);x,task=validate_envelope(data,catalog,m['epoch'],now)
        require(f['path']=='chunks/'+x['datastream_id']+'/'+task['checkpoint_key']+'.json','Pending task path')
        require(f['bytes']==len(data) and f['rows']==len(x['rows']) and f['sha256']==sha(data),'Pending member checksum/count')
        require(cp.utc(m['expires_at_utc'])<=cp.utc(x['retrieval_first_utc'])+timedelta(days=RETENTION_DAYS),'Pending retention renewal forbidden')
        total+=len(data);rows+=len(x['rows']);require(total<=MAX_BYTES and rows<=MAX_ROWS,'Pending aggregate bound');payload.append((f['path'],data))
    closure(src,names|{'manifest.json'})
    # Validate everything before installation; an interrupted copy cannot look reusable.
    target.parent.mkdir(parents=True,exist_ok=True);temp=Path(tempfile.mkdtemp(prefix='pending.restore-',dir=target.parent))
    try:
        for name,data in payload:
            p=temp/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
        require(read(src/'manifest.json')==raw and parent_at(state)==parent,'Pending input/parent changed during restore')
        closure(src,names|{'manifest.json'});os.replace(temp,target)
    finally:
        if temp.exists():shutil.rmtree(temp)
    return dict(status='restored',manifest_sha256=expected,completed_intervals=len(payload),rows=rows,bytes=total,
                acknowledged_parent_advanced=False,original_retrieval_evidence_preserved=True)

def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=('export','restore'))
    for n in ('state','catalog','as-of','report'):p.add_argument('--'+n,required=True)
    p.add_argument('--output');p.add_argument('--input');p.add_argument('--sha256');p.add_argument('--reviewed-refetch',action='store_true');a=p.parse_args()
    try:
        r=export(a.state,a.catalog,a.output,a.as_of) if a.mode=='export' else restore(a.input,a.sha256,a.state,a.catalog,a.as_of);status=0
    except (ValueError,KeyError,TypeError,OSError) as exc:
        # No automatic acquisition fallback. Manual review can explicitly permit a
        # bounded refetch on a fresh state. This is loss of progress, not resumption.
        fresh=not (ops.plain(a.state)/'intervals/coverage-v1').exists()
        allowed=a.mode=='restore' and a.reviewed_refetch and fresh
        r=dict(status='reviewed_bounded_refetch_required' if allowed else 'pending_recovery_hold',
               reason=str(exc),completed_intervals_restored=0,acknowledged_parent_advanced=False,
               durable_progress=False,automatic_refetch=False);status=0 if allowed else 2
    report=ops.plain(a.report);report.parent.mkdir(parents=True,exist_ok=True);report.write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r));return status
if __name__=='__main__':raise SystemExit(main())
