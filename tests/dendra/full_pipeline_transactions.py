#!/usr/bin/env python3
"""SM2B full-science local publisher experiment. Never permits an external remote.
Run phases separately under a process-tree/disk/time guard; each remains real.
"""
import argparse,copy,csv,importlib.util,json,os,shutil,subprocess,sys,tempfile,time
from pathlib import Path
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from archive_fixture import ArchiveFixture,ROOT,dc
from archive_git_transactions import LocalGit as PriorBoundary
import dendra_archive as da
import dendra_state as ds

SOURCE=['scripts/main_publisher.py','scripts/dendra_candidate.py','scripts/dendra_archive.py','scripts/dendra_publisher.py','scripts/dendra_state.py','scripts/dendra/core.R','scripts/dendra/integrated.R','scripts/dendra/semantic.R','data/input/dendra/pilot_catalog.json']
EXECUTION_SOURCE=SOURCE+['scripts/build_dendra_daily.R','scripts/dendra/archive.R','tests/dendra/full_pipeline_transactions.py','tests/dendra/archive_fixture.py','tests/dendra/integration_fixture.py','tests/dendra/archive_git_transactions.py']
class Boundary(PriorBoundary):
    def __init__(self,root,create=False):
        self.root=Path(root).resolve();expected=(ROOT.parent/'sm2b/git-fixtures').resolve()
        dc.require(self.root.is_relative_to(expected) and self.root!=expected,'SM2B Git path boundary')
        if create:
            dc.require(not self.root.exists(),'Use a fresh disposable fixture');self.root.mkdir(parents=True)
            (self.root/'tmp').mkdir();(self.root/'no-hooks').mkdir()
        else:dc.require((self.root/'experiment.json').is_file(),'Not an initialized SM2B fixture')
        env={k:v for k,v in os.environ.items() if not k.startswith('GIT_') and k not in ('BRIM_LIVE_MAIN_PUBLISH','GITHUB_SHA','GITHUB_REF','GITHUB_REF_TYPE','GITHUB_ACTIONS')}
        env.update(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull,GIT_ALLOW_PROTOCOL='file',GIT_TERMINAL_PROMPT='0',PYTHONDONTWRITEBYTECODE='1',TMPDIR=str(self.root/'tmp'))
        config={'core.hooksPath':str(self.root/'no-hooks'),'credential.helper':'','protocol.allow':'never','protocol.file.allow':'always','user.name':'Dendra synthetic fixture','user.email':'dendra-fixture@example.invalid','commit.gpgSign':'false','tag.gpgSign':'false'}
        env['GIT_CONFIG_COUNT']=str(len(config))
        for n,(k,v) in enumerate(config.items()):env[f'GIT_CONFIG_KEY_{n}']=k;env[f'GIT_CONFIG_VALUE_{n}']=v
        self.env=env;self.commands=[]
        self.source=self.root/'source';self.bare=self.root/'remote.git'
        if not create:
            e=dc.load(self.root/'experiment.json');dc.require(e['execution_bytes']=={p:dc.sha(ROOT/p) for p in EXECUTION_SOURCE},'Producer/test source changed since initialization');dc.require(e['source_bytes']=={r:dc.sha(ROOT/r) for r in SOURCE},'Implementation changed since fixture initialization')
            dc.require(e['source_bytes']=={r:dc.sha(self.source/r) for r in SOURCE},'Fixture source changed')
            self.remote(self.run('remote','get-url','origin',cwd=self.source))
    def head(self):return self.run('rev-parse','refs/heads/main',cwd=self.bare)
    def activate(self):
        sha=dc.load(self.root/'experiment.json')['source_fixture_commit'];self.env.update(GITHUB_ACTIONS='true',GITHUB_REF='refs/heads/main',GITHUB_REF_TYPE='branch',GITHUB_SHA=sha,BRIM_LIVE_MAIN_PUBLISH='true')
        os.environ.clear();os.environ.update(self.env);tempfile.tempdir=str(self.root/'tmp');return sha

def native(f,variant):
    end='2026-09-21';begin='2026-09-20';items=[]
    for s in f.catalog['streams']:
        p=f.root/f"native-{s['datastream_id']}.csv";t=datetime(2026,9,20,8,tzinfo=timezone.utc)
        with p.open('w',newline='') as out:
            w=csv.writer(out);w.writerow(['t','datastream_id','v','value_status','duplicate_conflict','alternative_out_of_range'])
            for n in range(144):w.writerow([f.utc(t+timedelta(minutes=10*n)),s['datastream_id'],.2+variant*.001,'number','FALSE','FALSE'])
        sha=dc.sha(p);items.append({'stream':s,'start':begin,'end':end,'native_csv':str(p),'native_sha256':sha,'native_row_count':144,'chunks':[{'requested_interval':{'start_inclusive':begin+'T08:00:00Z','end_exclusive':end+'T08:00:00Z'},'content_sha256':sha,'retrieval_last_utc':f.utc(f.now),'latest_observation_utc':'2026-09-21T07:50:00Z'}]})
    dc.write(f.root/'native.json',{'complete':True,'failures':[],'streams':items})

def run(a):
    start=time.monotonic();g=Boundary(a.root,create=a.phase=='init');r=g.root;records=[]
    def mark(name,**kw):records.append({'name':name,'elapsed_seconds':time.monotonic()-start,**kw});dc.write(r/f'{a.phase}-report.json',{'scope':'SYNTHETIC full scientific local file-protocol publication only','records':records,'git_commands':g.commands})
    if a.phase=='init':
        seed=Path(a.seed).resolve();g.run('init','--bare','--initial-branch=main',g.bare);g.run('init','--initial-branch=main',g.source)
        for rel in SOURCE:
            p=g.source/rel;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/rel,p)
        dc.write(g.source/'docs/data/other/keep.json',{'untouched':'other product'})
        g.run('add','.',cwd=g.source);g.run('commit','-m','SM2B synthetic source fixture only',cwd=g.source);sha=g.run('rev-parse','HEAD',cwd=g.source)
        g.run('remote','add','origin',g.bare,cwd=g.source);g.run('push','origin','HEAD:refs/heads/main',cwd=g.source)
        dc.write(r/'experiment.json',{'source_fixture_commit':sha,'source_bytes':{p:dc.sha(ROOT/p) for p in SOURCE},'execution_bytes':{p:dc.sha(ROOT/p) for p in EXECUTION_SOURCE},'seed_index_sha256':dc.sha(seed/dc.FIXED[0])})
        index=da.open_index(seed);catalog={'integration':{'version':'dendra-integration-2','temperature_days':90,'update_days':{'soil_moisture':1,'soil_temperature':1}},'live_identity_status':'verified','stations':index['stations'],'streams':[]}
        for s in index['streams']:
            _,p=da.stream_product(seed,s);catalog['streams'].append(p['stream'])
        dc.write(r/'catalog.json',catalog);ds.hydrate(r/'state',seed,'seed');mark('real-seed-validation-and-state-preparation',streams=len(catalog['streams']));return
    sha=g.activate()
    if a.phase in ('first-update','overlap-update'):
        first=a.phase=='first-update';state=r/('state' if first else 'cold-state')
        acknowledged=state/('seed.json' if first else 'published.json');before=acknowledged.read_bytes();parent=ds.candidate_at(state,dc.load(acknowledged));before_files=ds.inventory(parent)
        f=ArchiveFixture(r/a.phase,datetime(2026,9,21,12 if first else 20,tzinfo=timezone.utc));f.catalog=dc.load(r/'catalog.json');native(f,0 if first else 1)
        p=f.invoke(state=state,extra={'days':'1'});candidate=ds.candidate_at(state,p);dc.require(acknowledged.read_bytes()==before,'Preparing advanced acknowledgement')
        after=ds.inventory(candidate);old={x['path']:x for x in before_files};new={x['path']:x for x in after};changed=[x for k,x in new.items() if old.get(k)!=x]
        # Every old closed-WY descriptor and daily record is bound by exact bytes.
        oldidx=da.open_index(parent);idx=da.open_index(candidate);closed=0
        for s in oldidx['streams']:
            m,_=da.stream_product(parent,s);n,_=da.stream_product(candidate,next(v for v in idx['streams'] if v['datastream_id']==s['datastream_id']))
            for part in m['partitions']:
                if part['water_year']<2026:dc.require(part in n['partitions'],'Closed year changed');closed+=1
        dc.write(r/(a.phase+'-candidate.json'),{'candidate':str(candidate),'state':str(state)})
        mark('real-R-saved-state-update-build-and-state-stage',candidate_index_sha256=dc.sha(candidate/dc.FIXED[0]),parent_unchanged=True,closed_WY_partitions_unchanged=closed,files=len(after),bytes=sum(x['bytes'] for x in after),changed_files=len(changed),changed_bytes=sum(x['bytes'] for x in changed),removed_paths=sorted(set(old)-set(new)),changed_paths=[x['path'] for x in changed]);return
    if a.phase in ('first-publish','overlap-publish','no-op'):
        which='first-update' if a.phase in ('first-publish','no-op') else 'overlap-update';candidate=Path(dc.load(r/(which+'-candidate.json'))['candidate'])
        metadata=dc.prepare(candidate,sha);mark('real-prepare-complete',candidate_index_sha256=dc.sha(candidate/dc.FIXED[0]))
        spec=importlib.util.spec_from_file_location('sm2b_shared_publisher',g.source/'scripts/main_publisher.py');publisher=importlib.util.module_from_spec(spec);spec.loader.exec_module(publisher)
        real=publisher._run
        def guarded(args,**kw):
            g.guard(args,kw.get('cwd',g.root));result=real(args,**kw)
            # Record callback stdout as well as exact Git operation list.
            with (r/(a.phase+'-transport.log')).open('a') as out:out.write(json.dumps({'args':args,'exit':result.returncode,'stdout':result.stdout,'stderr':result.stderr})+'\n')
            return result
        publisher._run=guarded
        before=g.head();args=SimpleNamespace(repo=str(g.source),candidate_root=str(candidate),metadata=str(metadata),product_id=dc.PRODUCT,target_ref='refs/heads/main',commit_subject='SM2B synthetic full archive publication',allowlist=dc.FIXED,owned_root=dc.OWNED,candidate_validator=str(g.source/'scripts/dendra_publisher.py'),reconcile_callback=str(g.source/'scripts/dendra_publisher.py'),staged_validator=str(g.source/'scripts/dendra_publisher.py'))
        publisher.publish(args);after=g.head();changed=g.run('diff','--name-only',before,after,cwd=g.bare).splitlines() if before!=after else []
        dc.require((before==after)==(a.phase=='no-op'),'Unexpected local publication/no-op result')
        dc.require(all(x==dc.FIXED[0] or any(x.startswith(z+'/') for z in dc.OWNED) for x in changed),'Unrelated changes')
        dc.require(json.loads(g.run('show','HEAD:docs/data/other/keep.json',cwd=g.bare))=={'untouched':'other product'},'Other product changed')
        dc.require(g.run('rev-parse','HEAD',cwd=g.source)==sha and not g.run('status','--porcelain',cwd=g.source),'Source fixture checkout changed')
        mark('real-shared-publisher-complete',before=before,after=after,changed_files=len(changed),changed_paths=changed,git_storage=g.run('count-objects','-v',cwd=g.bare));return
    if a.phase=='restore':
        before=g.head();pointer=ds.restore_public(g.bare,r/'cold-state');dc.require(pointer['publication_commit']==before==g.head(),'Cold receipt mismatch')
        mark('cold-state-restored-from-real-local-commit',pointer=pointer);return
    raise ValueError(a.phase)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--seed');p.add_argument('--phase',required=True,choices=['init','first-update','first-publish','no-op','restore','overlap-update','overlap-publish']);run(p.parse_args())
