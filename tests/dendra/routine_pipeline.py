"""Opt-in SM2C full local scientific transaction sequence, not a hosted run.
Only collection and scientific clocks are injected. Resource clocks stay real.
All Git writes/remotes must resolve below work/sm2c/git-fixtures.
"""
import argparse,csv,tarfile,importlib.util,json,os,shutil,subprocess,sys,tempfile,time
from pathlib import Path
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from unittest.mock import patch
from archive_fixture import ArchiveFixture,ROOT,dc
from routine_git_transactions import LocalGit
from routine_clock import install
import dendra_archive as da
import dendra_state as ds
import dendra_validation as dv

SOURCE=['scripts/'+x for x in dv.SOURCES if not x.startswith('../')]+['data/input/dendra/pilot_catalog.json','tests/dendra/routine_clock.py']
EXECUTION_SOURCE=SOURCE+['tests/dendra/routine_pipeline.py','tests/dendra/routine_git_transactions.py','tests/dendra/archive_fixture.py','tests/dendra/integration_fixture.py']
CLOCKS={'one-update':'2026-09-21T12:00:00Z','one-publish':'2026-09-21T12:10:00Z','no-op':'2026-09-21T12:20:00Z','one-overlap':'2026-09-21T20:00:00Z','fresh-prepare':'2026-09-22T12:00:00Z','seven-publish':'2026-09-22T12:30:00Z','latest-recovery':'2026-09-22T13:00:00Z','next-update':'2026-09-22T14:00:00Z'}

class Boundary(LocalGit):
    def __init__(self,root,create=False):
        if create:super().__init__(root)
        else:
            self.root=Path(root).resolve();expected=(ROOT.parent/'sm2c/git-fixtures').resolve()
            dc.require(self.root.is_relative_to(expected) and self.root!=expected,'SM2C fixture containment')
            e=dc.load(self.root/'experiment.json');self.env=e['safe_git_environment'];self.commands=[]
            # Retain runtime-only local audit paths, never serialize credentials.
            for k in ('DENDRA_VALIDATION_TRACE','RSCRIPT','R_LIBS_USER','R_LIBS'):
                if k in os.environ:self.env[k]=os.environ[k]
            self.env['PATH']=os.environ['PATH']
            dc.require(e['execution_bytes']=={p:dc.sha(ROOT/p) for p in EXECUTION_SOURCE},'Execution source changed after fixture init')
        self.source=self.root/'source';self.bare=self.root/'remote.git'
        if not create:
            dc.require(e['source_bytes']=={p:dc.sha(self.source/p) for p in SOURCE},'Committed fixture source mismatch')
            self.remote(self.run('remote','get-url','origin',cwd=self.source))
    def activate(self,clock):
        e=dc.load(self.root/'experiment.json');sha=e['source_fixture_commit'];self.env.update(GITHUB_ACTIONS='true',GITHUB_REF='refs/heads/main',GITHUB_REF_TYPE='branch',GITHUB_SHA=sha,BRIM_LIVE_MAIN_PUBLISH='true')
        os.environ.clear();os.environ.update(self.env);tempfile.tempdir=str(self.root/'tmp');install(clock);return sha
    def head(self):return self.run('rev-parse','refs/heads/main',cwd=self.bare)

class ClockFixture(ArchiveFixture):
    def invoke(self,*args,**kwargs):
        real=subprocess.run
        def launch(command,*a,**kw):
            if command[:2]==['Rscript','--vanilla']:
                p=Path(command[2]);s=p.read_text();old=' real_system2(command,args,...)'
                new=''' if(any(grepl("dendra_(candidate|state|archive).py",clean))) {
  return(real_system2(command,shQuote(c('''+json.dumps(str(ROOT/'tests/dendra/routine_clock.py'))+''',"--clock",'''+json.dumps(self.utc(self.now))+''',"--script",clean)),...))
 }
 real_system2(command,args,...)'''
                assert old in s;s=s.replace(old,new);p.write_text(s)
            return real(command,*a,**kw)
        with patch.object(subprocess,'run',side_effect=launch):return super().invoke(*args,**kwargs)

def native(f,parent,days,variant=0):
    end=(f.now-timedelta(hours=8)).date();begin=end-timedelta(days=days);idx=da.open_index(parent);records=[];unchanged=corrected=deleted=added=0
    by_id={s['datastream_id']:s for s in idx['streams']}
    for j,s in enumerate(f.catalog['streams']):
        _,p=da.stream_product(parent,by_id[s['datastream_id']]);old={r['date']:r for r in p['rows']};path=f.root/f"native-{s['datastream_id']}.csv";count=0;latest=None
        with path.open('w',newline='') as out:
            w=csv.writer(out);w.writerow(['t','datastream_id','v','value_status','duplicate_conflict','alternative_out_of_range'])
            for n in range(days):
                day=begin+timedelta(days=n);key=day.isoformat();prior=old.get(key)
                if days==1:value=.2+variant*.001;number=144
                elif prior is None:value=.201+variant*.001;number=144;added+=1
                elif prior['n_total']==0:value=None;number=0;unchanged+=1
                else:
                    assert prior['n_total']==prior['n_valid']==144 and prior['cadence_seconds']==600 and not prior['flags']
                    value=prior['mean_native'];number=144;unchanged+=1
                if days==7 and j==0 and n==2 and number:value+=.001;corrected+=1;unchanged-=1
                if days==7 and j==2 and n==3 and number:number=0;deleted+=1;unchanged-=1
                for k in range(number):
                    t=datetime.combine(day,datetime.min.time(),tzinfo=timezone.utc)+timedelta(hours=8,minutes=10*k);latest=f.utc(t);w.writerow([latest,s['datastream_id'],value,'number','FALSE','FALSE']);count+=1
        digest=dc.sha(path);records.append({'stream':s,'start':begin.isoformat(),'end':end.isoformat(),'native_csv':str(path),'native_sha256':digest,'native_row_count':count,'chunks':[{'requested_interval':{'start_inclusive':begin.isoformat()+'T08:00:00Z','end_exclusive':end.isoformat()+'T08:00:00Z'},'content_sha256':digest,'retrieval_last_utc':f.utc(f.now),'latest_observation_utc':latest}]})
    dc.write(f.root/'native.json',{'complete':True,'failures':[],'streams':records})
    return {'days':days,'begin':begin.isoformat(),'end_exclusive':end.isoformat(),'native_rows':sum(x['native_row_count'] for x in records),'unchanged_source_day_cases':unchanged,'older_corrections':corrected,'complete_source_deletions':deleted,'new_daily_rows':added,'note':'SYNTHETIC constant observations reproduce saved synthetic daily sufficient statistics; query retrieval provenance legitimately changes. No real observations replaced.'}

def measured(records,name,fn):
    wall=time.time();mono=time.monotonic();start=datetime.now(timezone.utc).isoformat();result=fn()
    records.append({'name':name,'utc_start':start,'utc_finish':datetime.now(timezone.utc).isoformat(),'wall_seconds':time.time()-wall,'monotonic_seconds':time.monotonic()-mono});return result

def cleanup_generation(state,pointer,records):
    target=Path(state)/'runs'/pointer['generation']/'generation';assert target.is_dir() and not (Path(state)/'writer.lock').exists()
    files=[{'path':p.relative_to(target).as_posix(),'bytes':p.stat().st_size,'sha256':dc.sha(p)} for p in sorted(target.rglob('*')) if p.is_file()]
    records.append({'name':'retire-completed-temporary-daily-view','path':str(target),'files':files,'bytes':sum(x['bytes'] for x in files)})
    shutil.rmtree(target)

def update(g,label,state,days,clock,records,variant=0):
    pointer=dc.load(state/('published.json' if (state/'published.json').exists() else 'seed.json'));prior=ds.candidate_at(state,pointer);before=(state/('published.json' if pointer['state_role']=='published' else 'seed.json')).read_bytes();oldidx=da.open_index(prior)
    f=ClockFixture(g.root/label,datetime.fromisoformat(clock.replace('Z','+00:00')));f.catalog=dc.load(g.root/'catalog.json');recipe=native(f,prior,days,variant)
    p=measured(records,label+'-R-update',lambda:f.invoke(state=state,extra={'days':str(days)}));candidate=ds.candidate_at(state,p);newidx=da.open_index(candidate);closed=0;preserved_rows=0
    for old_s,new_s in zip(oldidx['streams'],newidx['streams']):
        old_m,old_p=da.stream_product(prior,old_s);new_m,new_p=da.stream_product(candidate,new_s);lookup={r['date']:r for r in new_p['rows']}
        for part in old_m['partitions']:
            if part['water_year']<2026:assert part in new_m['partitions'];closed+=1
        for row in old_p['rows']:
            if row['date']<recipe['begin']:assert lookup[row['date']]==row;preserved_rows+=1
    assert (state/('published.json' if pointer['state_role']=='published' else 'seed.json')).read_bytes()==before
    files=ds.inventory(candidate);old=ds.inventory(prior);oldfiles={x['path']:x for x in old};changed=[x for x in files if oldfiles.get(x['path'])!=x]
    records.append({'name':label+'-coverage','recipe':recipe,'parent_generation':pointer['generation'],'generation':p['generation'],'closed_WY_exact':closed,'older_daily_rows_exact':preserved_rows,'files':len(files),'bytes':sum(x['bytes'] for x in files),'rows':sum(s['summary']['calendar_row_count'] for s in newidx['streams']),'changed_files':len(changed),'changed_content_bytes':sum(x['bytes'] for x in changed),'candidate_index_sha256':dc.sha(candidate/dc.FIXED[0]),'acknowledged_pointer_unchanged':True})
    cleanup_generation(state,p,records);dc.write(g.root/(label+'-candidate.json'),{'candidate':str(candidate),'state':str(state),'pointer':p});return candidate

def publish(g,candidate,metadata,clock,records,noop=False):
    spec=importlib.util.spec_from_file_location('routine_shared_publisher',g.source/'scripts/main_publisher.py');publisher=importlib.util.module_from_spec(spec);spec.loader.exec_module(publisher);real=publisher._run;callback_records=[]
    def guarded(args,**kw):
        g.guard(args,kw.get('cwd',g.root));start=time.time();mono=time.monotonic();phase=kw.get('env',{}).get('BRIM_PUBLISH_PHASE')
        actual=args
        if phase:actual=[args[0],str(g.source/'tests/dendra/routine_clock.py'),'--clock',clock,'--script',*args[1:]]
        result=real(actual,**kw)
        if phase:callback_records.append({'phase':phase,'attempt':kw['env']['BRIM_PUBLISH_ATTEMPT'],'wall_seconds':time.time()-start,'monotonic_seconds':time.monotonic()-mono,'exit':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
        return result
    publisher._run=guarded;before=g.head()
    args=SimpleNamespace(repo=str(g.source),candidate_root=str(candidate),metadata=str(metadata),product_id=dc.PRODUCT,target_ref='refs/heads/main',commit_subject='SM2C SYNTHETIC local archive transaction',allowlist=dc.FIXED,owned_root=dc.OWNED,candidate_validator=str(g.source/'scripts/dendra_publisher.py'),reconcile_callback=str(g.source/'scripts/dendra_publisher.py'),staged_validator=str(g.source/'scripts/dendra_publisher.py'))
    measured(records,'shared-publication-command-lock-relevant-span',lambda:publisher.publish(args));after=g.head();assert (before==after)==noop
    changed=g.run('diff','--name-only',before,after,cwd=g.bare).splitlines() if before!=after else [];assert all(x==dc.FIXED[0] or any(x.startswith(p+'/') for p in dc.OWNED) for x in changed)
    assert json.loads(g.run('show','HEAD:docs/data/other/keep.json',cwd=g.bare))=={'unrelated':'retained'}
    records.append({'name':'publication-receipt','before':before,'after':after,'changed_paths':changed,'changed_files':len(changed),'callbacks':callback_records,'scope':'actual local file-protocol shared transaction; excludes queue wait, Actions setup and Internet transport'})
    return after

def run(a):
    g=Boundary(a.root,create=a.phase=='init');r=g.root;records=[];clock=CLOCKS.get(a.phase,'2026-09-21T12:00:00Z')
    if a.phase=='init':
        seed=Path(a.seed).resolve();assert seed.is_relative_to(ROOT.parent/'sm2b')
        g.run('init','--bare','--initial-branch=main',g.bare);g.run('init','--initial-branch=main',g.source)
        for name in SOURCE:
            dest=g.source/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,dest)
        dc.write(g.source/'docs/data/other/keep.json',{'unrelated':'retained'});g.run('add','.',cwd=g.source);g.run('commit','-m','SM2C synthetic source fixture only',cwd=g.source);sha=g.run('rev-parse','HEAD',cwd=g.source);g.run('remote','add','origin',g.bare,cwd=g.source);g.run('push','origin','HEAD:refs/heads/main',cwd=g.source)
        safe={k:v for k,v in g.env.items() if k.startswith('GIT_') or k in ('TMPDIR','PYTHONDONTWRITEBYTECODE')}
        dc.write(r/'experiment.json',{'source_fixture_commit':sha,'source_bytes':{p:dc.sha(ROOT/p) for p in SOURCE},'execution_bytes':{p:dc.sha(ROOT/p) for p in EXECUTION_SOURCE},'seed_index_sha256':dc.sha(seed/dc.FIXED[0]),'seed_content_sha256':dv.content_binding(seed),'safe_git_environment':safe,'scope':'synthetic fixture only'})
        idx=da.open_index(seed);catalog={'integration':{'version':'dendra-integration-2','temperature_days':90,'update_days':{'soil_moisture':7,'soil_temperature':7}},'live_identity_status':'verified','stations':idx['stations'],'streams':[da.stream_product(seed,s)[1]['stream'] for s in idx['streams']]};dc.write(r/'catalog.json',catalog)
        measured(records,'cold-seed-validate-and-import',lambda:ds.hydrate(r/'one-state',seed,'seed'))
    else:
        sha=g.activate(clock)
        if a.phase=='one-update':update(g,'one-update',r/'one-state',1,clock,records)
        elif a.phase=='one-publish':
            c=Path(dc.load(r/'one-update-candidate.json')['candidate']);m=measured(records,'candidate-prepare',lambda:dc.prepare(c,sha));publish(g,c,m,clock,records)
        elif a.phase=='no-op':
            c=Path(dc.load(r/'one-update-candidate.json')['candidate']);publish(g,c,c.parent/'candidate-metadata.json',clock,records,True)
        elif a.phase=='one-overlap':
            state=r/'comparison-state'
            measured(records,'comparison-cold-restore',lambda:ds.restore_public(g.bare,state))
            c=update(g,'one-overlap',state,1,clock,records,variant=1)
            m=measured(records,'candidate-prepare',lambda:dc.prepare(c,sha))
            publish(g,c,m,clock,records)
        elif a.phase=='fresh-prepare':
            # Start a genuinely empty state, extract current committed first product.
            state=r/'fresh-state';receipt=measured(records,'cold-first-publication-restore-and-ack',lambda:ds.restore_public(g.bare,state));assert receipt['publication_commit']==g.head()
            candidate=update(g,'seven-update',state,7,clock,records)
            measured(records,'template-prepare-transfer-validation-and-state-export',lambda:ds.transfer(state,r/'transfer',sha))
            # Include real local serialization/copy transfer rather than pretending
            # artifact upload latency was measured. Retire its source only afterward.
            archive=r/'local-transfer.tar.gz'
            def pack():
                with tarfile.open(archive,'w:gz',compresslevel=1) as t:t.add(r/'transfer',arcname='transfer')
            measured(records,'local-artifact-serialization',pack)
            original_inventory=ds.inventory(r/'transfer');shutil.rmtree(r/'transfer')
            def unpack():
                with tarfile.open(archive,'r:gz') as t:t.extractall(r/'received',filter='data')
                os.replace(r/'received/transfer',r/'download')
            measured(records,'local-artifact-extraction',unpack)
            assert ds.inventory(r/'download')==original_inventory
            records.append({'name':'transfer-closure','publication_binding':dv.content_binding(r/'download/publication/candidate'),'prepared_envelope':ds.validate_transfer(r/'download',sha),'local_archive_bytes':archive.stat().st_size,'local_archive_sha256':dc.sha(archive),'note':'Local serialization/extraction, not Internet transfer; source and destination exact inventories match.'})
        elif a.phase=='seven-publish':
            measured(records,'downloaded-transfer-envelope-and-metadata-binding',lambda:ds.validate_transfer(r/'download',sha))
            publish(g,r/'download/publication/candidate',r/'download/publication/candidate-metadata.json',clock,records)
        elif a.phase=='latest-recovery':
            latest=g.head();receipt=measured(records,'cold-latest-descendant-restore-and-exact-ack',lambda:ds.restore_public(g.bare,r/'latest-state'));assert receipt['publication_commit']==latest
            submitted=da.open_index(r/'download/publication/candidate');restored=da.open_index(ds.candidate_at(r/'latest-state',receipt));assert restored['generation']==submitted['generation'] and restored['parent_generation'] is not None
            assert {k:v for k,v in restored.items() if k!='publication_time_utc'}=={k:v for k,v in submitted.items() if k!='publication_time_utc'}
            assert [(x['path'],x['sha256']) for x in ds.inventory(ds.candidate_at(r/'latest-state',receipt)) if x['path']!=dc.FIXED[0]]==[(x['path'],x['sha256']) for x in ds.inventory(r/'download/publication/candidate') if x['path']!=dc.FIXED[0]]
            records.append({'name':'exact-latest-receipt','receipt':receipt,'all_nonindex_bytes_match_submitted':True})
        elif a.phase=='next-update':
            prior=(r/'latest-state/published.json').read_bytes();update(g,'next-update',r/'latest-state',7,clock,records);assert (r/'latest-state/published.json').read_bytes()==prior
        else:raise ValueError(a.phase)
    dc.write(r/(a.phase+'-report.json'),{'status':'passed','phase':a.phase,'scope':'SYNTHETIC actual R/local Git; mocked acquisition/science clock only','science_clock':clock,'records':records,'git_commands':g.commands});print(json.dumps({'phase':a.phase,'status':'passed','records':len(records)}))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--seed');p.add_argument('--phase',required=True,choices=['init',*CLOCKS]);run(p.parse_args())
