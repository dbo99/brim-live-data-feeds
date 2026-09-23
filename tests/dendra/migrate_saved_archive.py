#!/usr/bin/env python3
"""Offline migration of checksum-bound accepted daily products; never recompute means.
Native CSV timestamps are read only to recover legacy per-date query provenance.
"""
import argparse,copy,csv,json,sys,zipfile,hashlib
from pathlib import Path
from datetime import datetime,timedelta
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'scripts'))
import dendra_candidate as dc

def main(workspace,output):
    w=Path(workspace).resolve();out=Path(output).resolve();assert not out.exists();out.mkdir(parents=True)
    accepted=w/'exports/Soil_Moisture_SM1R1_Review.zip';assert dc.sha(accepted)=='f9efcb7e05d5a31a412f8c452c15f997f9d38b66095589a5e269476e36c5de98'
    hashes=dc.load(w/'evidence/D2A/REPORT.json')['input_hashes']
    for item in hashes:
        assert dc.sha(w/item['path'])==item['sha256'],item['path']
    bindings=dc.load(w/'evidence/D1R1/reproduction_inputs.json')
    for item in bindings['saved_input_files']:assert dc.sha(w/item['path'])==item['sha256']
    assert dc.sha(w/bindings['native_manifest'])==bindings['native_manifest_sha256']
    native=dc.load(w/bindings['native_manifest']);oldroot=w/'work/d2a/archive-artifacts/20260921T145024-68476/candidate';oldindex=dc.load(oldroot/dc.FIXED[0]);dc.validate(oldroot)
    newroot=w/'work/sm1r1/data/dendra';newindex=dc.load(newroot/'index.json');newstreams={s['datastream_id']:s for s in newindex['streams']};report=[];products={}
    # Verify completed prototype members directly against its immutable supplied ZIP.
    with zipfile.ZipFile(w/'inputs/dendra_runner_prototype.zip') as z,zipfile.ZipFile(accepted) as az:
        for ns in native['streams']:
            sid=ns['stream']['datastream_id'];old=dc.load(oldroot/f'docs/data/dendra/diagnostics/{sid}.json');ref=w/f'reference/dendra_runner_prototype/products/daily/{sid}.json'
            member=next(n for n in z.namelist() if n.endswith(f'/products/daily/{sid}.json'));assert z.read(member)==ref.read_bytes()
            golden=dc.load(ref)['rows'];assert len(golden)==len(old['rows'])
            for a,b in zip(golden,old['rows']):
                for k,v in a.items():
                    if isinstance(v,(int,float)) and not isinstance(v,bool):assert abs(v-b[k])<=1e-9,(sid,a['date'],k)
                    else:assert v==b[k],(sid,a['date'],k)
            oldrows=copy.deepcopy(old['rows']);p=copy.deepcopy(old);p['stream']['parameter']='soil_moisture';p['aggregation']['prior_observed_cadence_seconds']=None
            assert dc.sha(ns['native_csv'])==ns['native_sha256'];latest={}
            with open(ns['native_csv'],newline='') as f:
                for r in csv.DictReader(f):
                    day=(datetime.fromisoformat(r['t'].replace('Z','+00:00'))-timedelta(hours=8)).date().isoformat();latest[day]=max(latest.get(day,''),r['t'])
            query={}
            for row in p['rows']:
                day=row['date'];matches=[c for c in p['source_snapshot']['chunks'] if c['requested_interval']['start_inclusive'][:10]<=day<c['requested_interval']['end_exclusive'][:10]]
                if matches:
                    c=matches[-1];query[day]={'status':'queried','retrieved_at_utc':c['retrieval_last_utc'],'content_sha256':c['content_sha256'],'latest_observation_utc':latest.get(day)}
                else:
                    assert row['n_total']==0 and day<p['stream']['observed_start_utc'][:10];query[day]={'status':'before_saved_source_start','retrieved_at_utc':None,'content_sha256':None,'latest_observation_utc':None}
            p['source_snapshot']['query_by_date']=query;changed=[]
            if sid in newstreams:
                name=f'Soil_Moisture_SM1R1_Review/preview/data/dendra/diagnostics/{sid}.json';raw=az.read(name);assert raw==(newroot/f'diagnostics/{sid}.json').read_bytes();p=json.loads(raw)
                bydate={r['date']:r for r in p['rows']}
                for r in oldrows:
                    assert r['date'] in bydate
                    if r!=bydate[r['date']]:
                        assert '2026-08-21'<=r['date']<'2026-09-20';q=p['source_snapshot']['query_by_date'][r['date']];assert q['status']=='queried';changed.append({'date':r['date'],'fields':[k for k in r if r[k]!=bydate[r['date']][k]]})
            products[sid]=p;report.append({'stream':sid,'old_product_sha256':dc.sha(oldroot/f'docs/data/dendra/diagnostics/{sid}.json'),'original_rows':len(oldrows),'migrated_rows':len(p['rows']),'all_original_dates_preserved':True,'unchanged_rows':len(oldrows)-len(changed),'verified_newer_interval_replacements':changed,'last_date':p['rows'][-1]['date']})
        for sid,s in newstreams.items():
            if sid in products:continue
            name=f'Soil_Moisture_SM1R1_Review/preview/data/dendra/diagnostics/{sid}.json';raw=az.read(name);assert raw==(newroot/f'diagnostics/{sid}.json').read_bytes();products[sid]=json.loads(raw)
    catalog={'integration':{'version':'dendra-integration-2','temperature_days':90,'update_days':{'soil_moisture':7,'soil_temperature':7}},'live_identity_status':'saved_unverified_for_live','stations':oldindex['stations'],'streams':[p['stream'] for p in products.values()]}
    # Reuse exact accepted station identity; remove only old per-selection summary.
    for st in catalog['stations']:
        st.pop('selected_stream_ids',None);st.pop('selected_stream_count',None)
    seed={'version':'dendra-saved-seed-2','mode':'saved','streams':[],'input_hashes':hashes,'accepted_SM1R1_sha256':dc.sha(accepted)}
    for sid,p in products.items():
        path=out/'daily'/f'{sid}.json';dc.write(path,p);seed['streams'].append({'datastream_id':sid,'path':str(path),'sha256':dc.sha(path)})
    dc.write(out/'catalog.json',catalog);dc.write(out/'seed.json',seed);dc.write(out/'migration-parity.json',{'status':'passed','comparison':'every original daily field including flags/calendar/sufficient statistics; original Python vs accepted R tolerance 1e-9, accepted R retained/newer rows exact','streams':report,'total_rows':sum(len(p['rows']) for p in products.values()),'eligible_rows':sum(r['plot_eligible'] for p in products.values() for r in p['rows']),'provider_requests':0})
    print(out)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--workspace',required=True);p.add_argument('--output',required=True);a=p.parse_args();main(a.workspace,a.output)
