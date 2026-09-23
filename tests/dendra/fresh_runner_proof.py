#!/usr/bin/env python3
"""Actual R/HTTP-boundary proof, fresh processes and actual template transfer shells.
Parent is either an exact committed local checkout or its validated portable snapshot.
No Git writes or external requests in this program.
"""
import argparse,copy,json,os,shutil,subprocess,sys
from pathlib import Path
from datetime import datetime,timedelta,timezone
from coverage_fixture import CoverageFixture,WorkflowBoundary,ROOT,dc
import dendra_state as ds
import soil_network_workflow as workflow
import soil_moisture_operations as ops
import dendra_archive as da

def templates():
    r=subprocess.run(['Rscript','--vanilla','-e','cat(jsonlite::toJSON(yaml::read_yaml(commandArgs(TRUE)[1]),auto_unbox=TRUE))',str(ROOT/'templates/soil-moisture/dendra.template.yml')],capture_output=True,text=True,timeout=30);assert r.returncode==0,r.stderr;return json.loads(r.stdout)
def run(root,parent,catalog_file,snapshot=False):
    root=Path(root).resolve();assert not root.exists();root.mkdir(parents=True);steps=templates()['jobs']['prepare']['steps'];checks=[]
    catalog=dc.load(catalog_file);idx=da.open_index(Path(parent)/'candidate') if snapshot else dc.load(Path(parent)/dc.FIXED[0])
    initial=datetime.fromisoformat(idx['as_of_utc'].replace('Z','+00:00'))+timedelta(minutes=1)
    def restore_parent(dest):
        if snapshot:ds.restore_snapshot(parent,dest)
        else:ds.restore_public(parent,dest)
    def shell(name,runner,stamp,env=None):
        step=next(s for s in steps if s.get('name')==name);command=step['run'].replace('$(date -u +%Y-%m-%dT%H:%M:%SZ)',stamp)
        p=subprocess.run(['bash','-euo','pipefail','-c',command],cwd=ROOT,env=dict(os.environ,RUNNER_TEMP=str(runner),**(env or {})),capture_output=True,text=True,timeout=60)
        record=dict(template_step=name,shell=step['run'],executed_shell=command,exit=p.returncode,stdout=p.stdout,stderr=p.stderr);assert p.returncode==0,record;return record
    for day in (0,1):
        case=root/('same-day' if not day else 'next-day');case.mkdir();first=case/'runner1';first.mkdir();restore_parent(first/'dendra-state');before=(first/'dendra-state/published.json').read_bytes()
        cat=copy.deepcopy(catalog);cat['verified_at_utc']=initial.isoformat().replace('+00:00','Z');dc.write(first/'dendra-catalog.json',cat);dc.write(first/'limits.json',{'new_intervals':1})
        first_result=workflow.prepare('dendra',first/'dendra-prepare',first/'soil-operations','first',initial.date().isoformat(),'a'*40,state=first/'dendra-state',catalog=first/'dendra-catalog.json',as_of=cat['verified_at_utc'],budget=80,enabled=True,runner=WorkflowBoundary(first/'boundary',initial),clock=lambda:(initial+timedelta(seconds=3)).isoformat().replace('+00:00','Z'),collection_limits=first/'limits.json')
        assert first_result['outcome']=='incomplete_catch_up',first_result
        first_runs=list((first/'dendra-state/runs').glob('*/native/collection-result.json'));assert len(first_runs)==1
        collection1=dc.load(first_runs[0]);calls1=dc.load(first_runs[0].parent/'SIMULATED-opener-calls.json');assert collection1['completed_intervals']==1 and collection1['backlog_intervals']>=1
        assert (first/'dendra-state/published.json').read_bytes()==before and not (first/'dendra-prepare/transfer').exists()
        stamp=(initial+timedelta(seconds=4)).isoformat().replace('+00:00','Z')
        export_shell=shell('Export complete pending queries even without a prepared candidate',first,stamp)
        health_shell=shell('Export preparation health independently of earlier step status',first,stamp)
        # Locally simulate named artifact transport; no remote artifacts/Actions.
        carried=case/'carried-artifacts';carried.mkdir();shutil.copytree(first/'pending-snapshot',carried/'dendra-pending-collection');shutil.copytree(first/'health-snapshot',carried/'dendra-health')
        pending_sha=dc.sha(carried/'dendra-pending-collection/manifest.json');health_sha=dc.sha(carried/'dendra-health/manifest.json');original={p.relative_to(carried/'dendra-pending-collection').as_posix():dc.sha(p) for p in (carried/'dendra-pending-collection/chunks').glob('*/*.json')}
        logs={str(p.relative_to(first)):p.read_text() for p in first.rglob('*.R')};shutil.rmtree(first);assert not first.exists()
        second=case/'runner2';second.mkdir();restore_parent(second/'dendra-state');assert (second/'dendra-state/published.json').read_bytes()==before
        later=initial+timedelta(days=day,seconds=30);cat['verified_at_utc']=later.isoformat().replace('+00:00','Z');dc.write(second/'dendra-catalog.json',cat);dc.write(second/'limits.json',{'new_intervals':1})
        shutil.copytree(carried/'dendra-pending-collection',second/'prior-pending');shutil.copytree(carried/'dendra-health',second/'prior-health')
        restore_health=shell('Restore health independently from observation acknowledgement',second,cat['verified_at_utc'],dict(HEALTH_SHA=health_sha))
        restore_pending=shell('Restore complete pending queries after public parent and before planning',second,cat['verified_at_utc'],dict(PENDING_SHA=pending_sha,PENDING_POLICY='require_snapshot'))
        assert ops.records(second/'soil-operations')==[first_result]
        restored={p.relative_to(second/'dendra-state/intervals/coverage-v1').as_posix():dc.sha(p) for p in (second/'dendra-state/intervals/coverage-v1/chunks').glob('*/*.json')};assert original==restored
        result=workflow.prepare('dendra',second/'dendra-prepare',second/'soil-operations','second',later.date().isoformat(),'a'*40,state=second/'dendra-state',catalog=second/'dendra-catalog.json',as_of=cat['verified_at_utc'],budget=80,enabled=True,runner=WorkflowBoundary(second/'boundary',later),clock=lambda:(later+timedelta(seconds=3)).isoformat().replace('+00:00','Z'),collection_limits=second/'limits.json')
        assert result['outcome']=='success_changes',result
        runs=list((second/'dendra-state/runs').glob('*/native/collection-result.json'));assert len(runs)==1;collection2=dc.load(runs[0]);calls2=dc.load(runs[0].parent/'SIMULATED-opener-calls.json');assert collection2['complete'] and collection2['new_intervals_attempted']==1
        old=collection1['completed'][0];reused=next(x for x in collection2['completed'] if x['task_id']==old['task_id']);assert reused['cache_hit'] and reused['chunks']==old['chunks'] and reused['collection_sha256']==old['collection_sha256']
        assert not any(x['time[$gte]'][0].startswith(old['start']) for x in calls2['calls'])
        assert (second/'dendra-state/published.json').read_bytes()==before and result['acknowledgement'] is None
        checks.append(dict(scenario=case.name,first_incomplete_result=first_result,first_collection=collection1,first_boundary_calls=calls1,old_runner_removed=True,old_runner_path=str(first),fresh_runner_path=str(second),same_acknowledged_parent=json.loads(before),pending_manifest_sha256=pending_sha,health_manifest_sha256=health_sha,original_and_restored_checkpoint_hashes=original,second_result=result,second_collection=collection2,remaining_only_boundary_calls=calls2,original_retrieval_exact=True,acknowledgement_unchanged=True,actual_template_shells=[export_shell,health_shell,restore_health,restore_pending],R_boundary_wrappers=logs))
        print(case.name,'fresh R recovery passed',flush=True)
    report=dict(status='passed',external_requests=0,source_boundary='Synthetic hourly observations injected only at existing HTTP opener; actual R, validation, workflow helper, pending/health CLI and template shell execution',parent_restore='full portable snapshot validation' if snapshot else 'exact Git committed public restore',template_sha256=dc.sha(ROOT/'templates/soil-moisture/dendra.template.yml'),checks=checks)
    dc.write(root/'report.json',report);return report
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--parent',required=True);p.add_argument('--catalog',required=True);p.add_argument('--snapshot',action='store_true');a=p.parse_args();run(a.root,a.parent,a.catalog,a.snapshot)
