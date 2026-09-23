"""Bounded common Dendra catalog shards; SCAN/SNOTEL version1 is unchanged."""
import hashlib,json,re
from pathlib import Path
LIMIT=262144

def packed(x):return (json.dumps(x,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n').encode()
def emit(out,source,index):
    out=Path(out);raw=packed(index)
    if len(raw)<=LIMIT:(out/(source+'.json')).write_bytes(raw);return
    assert source=='dendra','Unrelated source index exceeds existing bound'
    header={k:v for k,v in index.items() if k!='stations'};header['schema']='brim-soil-moisture-2';header['shards']=[]
    group=[];groups=[]
    def body(rows):return {'schema':'brim-soil-moisture-shard-2','source':source,'generation':index['generation'],'stations':rows}
    for station in index['stations']:
        if group and len(packed(body(group+[station])))>240000:groups.append(group);group=[]
        group.append(station)
    if group:groups.append(group)
    assert 0<len(groups)<=128
    for n,rows in enumerate(groups):
        raw=packed(body(rows));assert len(raw)<=LIMIT,'One station exceeds bounded shard';sha=hashlib.sha256(raw).hexdigest();name=f'dendra-index-{n:03d}-{sha}.json';(out/name).write_bytes(raw);header['shards'].append({'path':name,'bytes':len(raw),'sha256':sha})
    raw=packed(header);assert len(raw)<=LIMIT;(out/(source+'.json')).write_bytes(raw)
def expand(index,check):
    if index['schema']=='brim-soil-moisture-1':return index
    assert index['schema']=='brim-soil-moisture-2' and index['source']=='dendra' and 'stations' not in index and 0<len(index['shards'])<=128
    stations=[];paths=set()
    for d in index['shards']:
        assert re.fullmatch(r'dendra-index-\d{3}-'+d['sha256']+r'\.json',d['path']) and d['path'] not in paths;paths.add(d['path']);assert 0<d['bytes']<=LIMIT
        b=json.loads(check(d));assert b['schema']=='brim-soil-moisture-shard-2' and b['source']=='dendra' and b['generation']==index['generation'];stations+=b['stations'];assert len(stations)<=1500
    return {**index,'schema':'brim-soil-moisture-1','stations':stations}
