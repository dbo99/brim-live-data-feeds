#!/usr/bin/env python3
"""Rebound invalid products must fail real validators and publisher callback."""
import argparse,copy,json,os,shutil,subprocess,sys,tempfile
from pathlib import Path
from archive_fixture import dc,ROOT
import dendra_archive as da
import dendra_state as ds

def run(workspace,root):
    w=Path(workspace).resolve();root=Path(root).resolve();assert not root.exists();root.mkdir(parents=True);p=dc.load(w/'work/sm2a/state/seed.json');source=ds.candidate_at(w/'work/sm2a/state',p);original=dc.load(source/dc.FIXED[0]);checks=[]
    def rejected(label,candidate,callback=False):
        try:dc.validate(candidate)
        except (ValueError,KeyError):pass
        else:raise AssertionError('Accepted '+label)
        if callback:
            env={**os.environ,'BRIM_PUBLISH_PRODUCT_ID':dc.PRODUCT,'BRIM_PUBLISH_FIXED_PATHS':json.dumps(dc.FIXED),'BRIM_PUBLISH_OWNED_ROOTS':json.dumps(dc.OWNED),'BRIM_PUBLISH_PHASE':'validate-candidate','BRIM_PUBLISH_CANDIDATE_ROOT':str(candidate),'BRIM_PUBLISH_WORKTREE':str(root/'unused'),'BRIM_PUBLISH_METADATA':str(root/'never-needed.json')}
            r=subprocess.run([sys.executable,str(ROOT/'scripts/dendra_publisher.py')],env=env,capture_output=True,text=True);assert r.returncode!=0 and 'DENDRA_PUBLISHER_REJECTED' in r.stderr;checks.append({'name':label,'validator_rejected':True,'actual_callback_rejected':True,'message':r.stderr.strip()})
        else:checks.append({'name':label,'validator_rejected':True})
    for label,mutate in [('wrong-product',lambda x:x.update(product_id='wrong')),('seed-as-live',lambda x:x.update(mode='live')),('seed-public-parent',lambda x:x.update(parent_generation='20260921T000000-123')),('seed-bad-checksum',lambda x:x['lineage'].update(seed_sha256='bad')),('too-many-files',lambda x:x.update(file_count=20001))]:
        dest=root/label;shutil.copytree(source,dest);i=copy.deepcopy(original);mutate(i);dc.write(dest/dc.FIXED[0],i);rejected(label,dest,True)
    large=root/'oversize';(large/'docs/data/dendra').mkdir(parents=True);(large/dc.FIXED[0]).write_bytes(b' '*256001);rejected('root-cap-before-parsing',large)
    generation=root/'generation';da.materialize(source,generation);index=dc.load(generation/'index.json')
    for label,mutate in [('bad-station-id',lambda x:x['stations'][0].update(station_id='../private')),('bad-coordinates',lambda x:x['stations'][0].update(geometry={'type':'Point','coordinates':[900,34]})),('hidden-source',lambda x:x['streams'][0].update(source_is_hidden=0)),('negative-depth',lambda x:x['streams'][0].update(depth_cm=-1))]:
        i=copy.deepcopy(index);mutate(i);dc.write(generation/'index.json',i);dest=root/label;dest.mkdir()
        try:da.write_layout(generation,dest)
        except (ValueError,KeyError):checks.append({'name':label,'producer_rejected':True});continue
        rejected(label,dest)
    # Diagnostic partition policy mutation is integrity rebound through layout output.
    dc.write(generation/'index.json',index);dest=root/'partition-policy';dest.mkdir();da.write_layout(generation,dest);i=da.open_index(dest);s=i['streams'][0];m,p=da.stream_product(dest,s);part=m['partitions'][0];body=dc.load(dest/part['diagnostics']['path']);body['policy_version']='INVALID';bad=da.put(dest,part['diagnostics']['path'].rsplit('-',1)[0],body,da.LIMITS['partition_bytes']);m['partitions'][0]['diagnostics']=bad
    # stream_product validates policy independently of generation inventory closure.
    md=da.put(dest,'docs/data/dendra/state/streams/'+s['datastream_id'],m,262144);s['manifest']=md
    try:da.stream_product(dest,s)
    except ValueError:checks.append({'name':'rebound-diagnostic-policy','validator_rejected':True})
    else:raise AssertionError('Diagnostic policy accepted')
    dc.write(root/'report.json',{'status':'passed','provider_requests':0,'checks':checks});print(json.dumps({'passed':len(checks)}))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--workspace',required=True);p.add_argument('--root',required=True);a=p.parse_args();run(a.workspace,a.root)
