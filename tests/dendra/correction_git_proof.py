#!/usr/bin/env python3
"""SM3AR1 scoped local Git setup/transactions; then actual R fresh-runner and CLI proofs."""
import argparse,importlib.util,json,os,shutil,subprocess,tempfile
from pathlib import Path
from datetime import datetime,timedelta,timezone
from sm3ar1_git import LocalGit,ROOT,dc,dv,ds,workflow,scan
from integration_fixture import Fixture
from coverage_fixture import CoverageFixture

def run(root,scan_input):
    g=LocalGit(root);source=g.root/'source';bare=g.root/'remote.git';g.run('init','--bare','--initial-branch=main',bare);g.run('init','--initial-branch=main',source)
    names={str(Path('scripts')/n) for n in dv.SOURCES if not n.startswith('../')}|{'data/input/dendra/pilot_catalog.json','scripts/scan_soil_moisture_publisher.py'}
    for n in names:
        p=source/n;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/n,p)
    dc.write(source/'docs/data/unrelated/keep.json',{'keep':True});g.run('add','.',cwd=source);g.run('commit','-m','SM3AR1 disposable fixture source',cwd=source);source_sha=g.run('rev-parse','HEAD',cwd=source);g.run('remote','add','origin',bare,cwd=source);g.run('push','origin','HEAD:refs/heads/main',cwd=source)
    g.env.update(GITHUB_ACTIONS='true',GITHUB_REF='refs/heads/main',GITHUB_REF_TYPE='branch',GITHUB_SHA=source_sha,BRIM_LIVE_MAIN_PUBLISH='true')
    old=os.environ.copy();os.environ.clear();os.environ.update(g.env);tempfile.tempdir=str(g.root/'tmp')
    spec=importlib.util.spec_from_file_location('correction_publisher',source/'scripts/main_publisher.py');pub=importlib.util.module_from_spec(spec);spec.loader.exec_module(pub);real=pub._run;callbacks=[]
    def safe(args,**kw):
        g.guard(args,kw.get('cwd',g.root));r=real(args,**kw)
        if kw.get('env',{}).get('BRIM_PUBLISH_PHASE'):callbacks.append(dict(phase=kw['env']['BRIM_PUBLISH_PHASE'],product=kw['env']['BRIM_PUBLISH_PRODUCT_ID'],exit=r.returncode))
        return r
    pub._run=safe
    def publish(n,transfer):
        args=workflow.publisher_args(n,source,transfer);before=g.run('rev-parse','HEAD',cwd=bare);pub.publish(pub.build_parser().parse_args(args[2:]));return before,g.run('rev-parse','HEAD',cwd=bare)
    try:
        now=datetime.now(timezone.utc).replace(microsecond=0)-timedelta(days=1,minutes=10);stamp=lambda d:d.isoformat().replace('+00:00','Z')
        legacy=Fixture(g.root/'old-science',now-timedelta(days=45));legacy.catalog['streams']=legacy.catalog['streams'][:1];native=legacy.native(backfill=True);ptr=legacy.invoke(manifest=native);candidate=ds.candidate_at(legacy.root/'state',ptr);idx=dc.load(candidate/dc.FIXED[0]);s=idx['streams'][0];p=candidate/s['diagnostics_path']
        f=CoverageFixture(g.root/'parent-builder',now);f.catalog['streams']=f.catalog['streams'][:1];dc.write(f.root/'seed.json',dict(version='dendra-saved-seed-2',mode='saved',streams=[dict(datastream_id=s['datastream_id'],path=str(p),sha256=dc.sha(p))]));f.invoke('bootstrap',extra={'seed-manifest':str(f.root/'seed.json')})
        end=(now-timedelta(hours=8)).date();Fixture.native(f,start=str(end-timedelta(days=1)),end=str(end));ptr=f.invoke('reconcile',extra={'days':'1','request-generation':'synthetic-reviewed-gap'})
        ds.transfer(f.root/'state',g.root/'dendra-transfer',source_sha);publish('dendra',g.root/'dendra-transfer');dendra_tree=g.root/'dendra-committed';g.run('worktree','add','--detach',dendra_tree,g.run('rev-parse','HEAD',cwd=bare),cwd=source)
        ds.restore_public(dendra_tree,g.root/'acknowledged');ds.export_snapshot(g.root/'acknowledged',g.root/'parent-snapshot',role='published')
        dr=__import__('soil_moisture_operations').result('dendra','parent',now.date().isoformat(),stamp(now),stamp(now),'success_changes',candidate_generation=ptr['generation'],latest_eligible_observation=str(end-timedelta(days=1)),last_successful_source_check=stamp(now));dc.write(g.root/'dendra-prepared.json',dr)
        # Fresh R proof executes in its own process; original first-run directories are removed.
        cmd=['python3',str(ROOT/'tests/dendra/fresh_runner_proof.py'),'--root',str(g.root/'fresh-runners'),'--parent',str(dendra_tree),'--catalog',str(f.root/'catalog.json')]
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=180);(g.root/'fresh-runners.log').write_text(p.stdout+p.stderr);assert p.returncode==0,p.stdout+p.stderr
        def saved_scan(args,**kw):
            if str(args[0])=='Rscript':
                for path in scan.ALLOWLIST:
                    target=Path(kw['cwd'])/path;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(Path(scan_input)/path.name,target)
                return 'Saved five-file SCAN acquisition boundary; builder not executed'
            return workflow.command(args,**kw)
        sr=workflow.prepare('scan',g.root/'scan-prepare',g.root/'scan-health','scan-parent',now.date().isoformat(),source_sha,enabled=True,runner=saved_scan)
        assert sr['outcome']=='success_changes',sr
        before,after=publish('scan',g.root/'scan-prepare/transfer');assert after!=before;scan_tree=g.root/'scan-committed';g.run('worktree','add','--detach',scan_tree,after,cwd=source)
        noops={}
        for n,tr in [('scan',g.root/'scan-prepare/transfer'),('dendra',g.root/'dendra-transfer')]:
            a,b=publish(n,tr);assert a==b;noops[n]=dict(before=a,after=b)
        args=['python3',str(ROOT/'tests/dendra/receipt_cli_proof.py'),'--root',str(g.root/'receipt-cli'),'--dendra-repo',str(dendra_tree),'--scan-repo',str(scan_tree),'--dendra-prepared',str(g.root/'dendra-prepared.json'),'--scan-prepared',str(g.root/'scan-prepare/run-result.json')]
        p=subprocess.run(args,capture_output=True,text=True,timeout=120);(g.root/'receipt-cli.log').write_text(p.stdout+p.stderr);assert p.returncode==0,p.stdout+p.stderr
        assert g.run('rev-parse','HEAD',cwd=source)==source_sha and not g.run('status','--porcelain',cwd=source)
        report=dict(status='passed',external_requests=0,source_fixture_commit=source_sha,git_commands=g.commands,actual_callbacks=callbacks,verified_noop_transactions=noops,fresh_runner_report=dc.load(g.root/'fresh-runners/report.json'),receipt_cli_report=dc.load(g.root/'receipt-cli/report.json'),parent_snapshot_manifest=dc.load(g.root/'parent-snapshot/manifest.json'),source_boundary_doubles=['small one-stream synthetic Dendra hourly opener','saved accepted SCAN five-file acquisition boundary'])
        dc.write(g.root/'report.json',report);return report
    finally:os.environ.clear();os.environ.update(old);tempfile.tempdir=None
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--scan-input',required=True);a=p.parse_args();run(a.root,a.scan_input)
