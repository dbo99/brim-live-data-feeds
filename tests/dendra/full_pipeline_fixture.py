#!/usr/bin/env python3
"""Deterministic SYNTHETIC full scientific pipeline fixtures, not observed coverage."""
import argparse,copy,json,sys,subprocess,hashlib,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'scripts'))
import dendra_archive as da
import dendra_candidate as dc

def templates(workspace,root):
    w=Path(workspace).resolve();root=Path(root).resolve();assert not root.exists();root.mkdir(parents=True);state=w/'work/sm2a/state';pointer=dc.load(state/'seed.json');candidate=state/pointer['candidate_relpath'];idx=da.open_index(candidate);inp=root/'input';inp.mkdir()
    for name,sid in [('deep','5d8e42e72da5c3cc53f6531d'),('granites','5ae879f1fe27f4c40b102ea4')]:
        stream=next(s for s in idx['streams'] if s['datastream_id']==sid);_,p=da.stream_product(candidate,stream);dc.write(inp/(name+'.json'),p)
    dc.write(root/'index-template.json',idx)
    subprocess.run(['Rscript','--vanilla',str(Path(__file__).with_name('full_pipeline_templates.R')),str(inp),str(root/'templates')],check=True)
    dc.write(root/'recipe.json',{'label':'SYNTHETIC FULL-PIPELINE','version':1,'deterministic_seed':20260922,'source_binding':'accepted SM2A saved deep/granites daily products; not raw reaggregation','variants':['long-variable-record','stale-tail','zero-sparse-range-empty-null','same-with-one-unqueried-day'],'frozen_clock':'2026-09-21T12:00:00Z','cutoff':'2026-09-19','templates':{p.name:dc.sha(p) for p in (root/'templates').glob('*.json')}})

def build(template_root,out,count):
    t=Path(template_root);out=Path(out).resolve();assert not out.exists();assert count in (8,64,434);out.mkdir(parents=True);clock='2026-09-21T12:00:00Z';idx=dc.load(t/'index-template.json');station_base=idx['stations'][0];idx={k:v for k,v in idx.items() if k not in ('limits','selection_ids','catalogs','file_pages','file_count','stream_count','station_count','streams','stations')};idx.update(generation=f'20260921T120000-{count}',generated_at_utc=clock,run_started_at_utc=clock,as_of_utc='2026-09-20T23:00:00Z',complete_through_date='2026-09-19',mode='replay',parent_generation=None,publication_time_utc=None,expires_at_utc=None,scope='SYNTHETIC FULL-PIPELINE; not observed coverage',streams=[],stations=[]);idx['lineage']={'kind':'imported_seed','state_parent_generation':None,'seed_generation':idx['generation'],'seed_sha256':dc.sha(t/'recipe.json'),'selection_change':None}
    gen=out/'generation';gen.mkdir();station_count=min(122,count);products=[dc.load(t/'templates'/f'{v}.json') for v in range(4)];summaries=[dc.load(t/'templates'/f'{v}-summary.json') for v in range(4)]
    for j in range(station_count):
        s=copy.deepcopy(station_base);s.update(station_id=f'{0xf000+j:024x}',name=f'SYNTHETIC station {j}',source_name=f'SYNTHETIC station {j}',selected_stream_ids=[],selected_stream_count=0);idx['stations'].append(s)
    for j in range(count):
        variant=(j+20260922)%4;p=copy.deepcopy(products[variant]);sid=f'{0x1000+j:024x}';station=idx['stations'][j%station_count];depth=[5,20,60,100][j%4];s=p['stream'];s.update(datastream_id=sid,station_id=station['station_id'],source_name=f'SYNTHETIC stream {j}',sensor_label=f'SYNTHETIC {depth} cm',depth_cm=depth,orientation='horizontal',source_attributes={'depth':{'unit_tag':'dt_Unit_Centimeter','value':depth},'orientation':'horizontal'});summary={**copy.deepcopy(s),**copy.deepcopy(summaries[variant])};station['selected_stream_ids'].append(sid);station['selected_stream_count']+=1;idx['streams'].append(summary);dc.write(gen/'daily'/f'{sid}.json',p)
    dc.write(gen/'index.json',idx);candidate=out/'candidate';candidate.mkdir();start=time.monotonic();da.write_layout(gen,candidate)
    files=[p for p in candidate.rglob('*') if p.is_file()];report={'scope':'SYNTHETIC FULL-PIPELINE fixture assembly ONLY; complete semantics is a separate measured invocation','streams':count,'rows':sum(s['summary']['calendar_row_count'] for s in idx['streams']),'eligible':sum(s['summary']['eligible_row_count'] for s in idx['streams']),'files':len(files),'bytes':sum(p.stat().st_size for p in files),'layout_seconds':time.monotonic()-start,'recipe_sha256':dc.sha(t/'recipe.json'),'index_sha256':dc.sha(candidate/dc.FIXED[0]),'generation':idx['generation']};dc.write(out/'fixture.json',report);print(json.dumps(report))
if __name__=='__main__':
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='mode',required=True);a=sub.add_parser('templates');a.add_argument('--workspace',required=True);a.add_argument('--root',required=True);a=sub.add_parser('build');a.add_argument('--templates',required=True);a.add_argument('--output',required=True);a.add_argument('--streams',type=int,required=True);a=p.parse_args();templates(a.workspace,a.root) if a.mode=='templates' else build(a.templates,a.output,a.streams)
