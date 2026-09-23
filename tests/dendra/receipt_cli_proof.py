#!/usr/bin/env python3
"""Actual receipt CLI + independent always-export shell, with explicit failure boundaries."""
import argparse,copy,json,os,shutil,subprocess,sys
from pathlib import Path
from datetime import datetime,timezone,timedelta
from archive_fixture import ROOT,dc
import soil_network_workflow as w
import soil_moisture_operations as o

def run(root,repos,prepared):
    root=Path(root).resolve();assert not root.exists();root.mkdir(parents=True);checks=[];real_git=shutil.which('git')
    for network in ('scan','dendra'):
        template=ROOT/'templates/soil-moisture'/f'{network}.template.yml'
        parsed=subprocess.check_output(['Rscript','--vanilla','-e','cat(jsonlite::toJSON(yaml::read_yaml(commandArgs(TRUE)[1]),auto_unbox=TRUE))',str(template)],text=True,timeout=30)
        steps=json.loads(parsed)['jobs']['publish']['steps'];final=next(s for s in steps if s.get('name')=='Publication outcome does not overwrite query clocks');export=next(s for s in steps if s.get('name')=='Export publication health even when receipt finalization fails');assert 'always()' in final['if'] and 'always()' in export['if']
        base=dc.load(prepared[network]);repo=Path(repos[network]).resolve();old=w.record_publication(root/(network+'-baseline-health'),prepared[network],repo,True);assert old['acknowledgement'],old
        # Earlier acknowledged generation/source checks are retained across every failure.
        old['run_id']='prior-'+network;old['started_at_utc']='2026-01-01T00:00:00Z';old['last_successful_source_check']='2026-01-01T00:00:00Z';old['null_reasons'].pop('last_successful_source_check',None)
        for case in ('transaction-failure','missing-committed-directory','validation-exception','receipt-head-mismatch','interrupted-finalization','verified-success','verified-noop'):
            temp=root/(network+'-'+case);temp.mkdir();health=temp/'soil-operations';o.append(health,old)
            x=copy.deepcopy(base);x['run_id']=network+'-'+case;x['attempt']=2
            if case=='verified-noop':x['outcome']='success_no_data_change'
            # Give the test result an exact query clock and observation clock; it must survive failures.
            x['last_successful_source_check']=x['finished_at_utc'];x['null_reasons'].pop('last_successful_source_check',None)
            path=temp/(network+'-prepare/run-result.json');dc.write(path,x);o.append(health,x)
            actual_repo=repo
            if case=='missing-committed-directory':actual_repo=temp/'absent'
            if case=='validation-exception':
                # A private plain copy, with read-only Git metadata resolved via GIT_DIR
                # so invalid working bytes trigger SCAN's validator. Dendra restores
                # only committed bytes, hence a failing actual R executable boundary.
                if network=='scan':
                    actual_repo=temp/'bad-working-tree';shutil.copytree(repo/'docs',actual_repo/'docs');(actual_repo/w.scan.SUMMARY_PATH).write_text('{}')
            shim=temp/'bin';shim.mkdir();env=dict(os.environ,RUNNER_TEMP=str(temp),PUBLISH_OUTCOME='failure' if case=='transaction-failure' else 'success')
            if case in ('receipt-head-mismatch','interrupted-finalization'):
                code='''#!/usr/bin/env python3
import os,sys,subprocess,signal
from pathlib import Path
a=sys.argv[1:]
if a==['rev-parse','HEAD']:
 p=Path(os.environ['CLI_GIT_COUNT']);n=int(p.read_text())+1 if p.exists() else 1;p.write_text(str(n))
 if os.environ['CLI_CASE']=='interrupted-finalization':os.kill(os.getppid(),signal.SIGTERM);raise SystemExit(2)
 if n>1:print('f'*40);raise SystemExit(0)
raise SystemExit(subprocess.call([os.environ['CLI_REAL_GIT'],*a]))
'''
                (shim/'git').write_text(code);(shim/'git').chmod(0o755);env.update(PATH=str(shim)+os.pathsep+os.environ['PATH'],CLI_GIT_COUNT=str(temp/'git-count'),CLI_CASE=case,CLI_REAL_GIT=real_git)
            if case=='validation-exception':
                if network=='scan':env.update(GIT_DIR=subprocess.check_output(['git','rev-parse','--absolute-git-dir'],cwd=repo,text=True).strip(),GIT_WORK_TREE=str(actual_repo))
                else:
                    (shim/'Rscript-failure').write_text('#!/bin/sh\necho "SYNTHETIC R validator exception" >&2\nexit 2\n');(shim/'Rscript-failure').chmod(0o755);env['RSCRIPT']=str(shim/'Rscript-failure')
            command=final['run'].replace('"$RUNNER_TEMP/committed"',json.dumps(str(actual_repo)))
            p=subprocess.run(['bash','-euo','pipefail','-c',command],cwd=ROOT,env=env,capture_output=True,text=True,timeout=90)
            success=case in ('verified-success','verified-noop');assert (p.returncode==0)==success,(network,case,p.returncode,p.stdout,p.stderr)
            # Simulate the separate always() shell step even though receipt shell failed.
            q=subprocess.run(['bash','-euo','pipefail','-c',export['run']],cwd=ROOT,env=env,capture_output=True,text=True,timeout=30);assert q.returncode==0,q.stderr
            m=temp/'published-health/manifest.json';assert m.is_file();o.restore(m.parent,dc.sha(m),temp/'restored-health');rs=o.records(temp/'restored-health');r=next(i for i in rs if i['run_id']==x['run_id'] and i['phase']=='publication')
            assert r['last_successful_source_check']==x['last_successful_source_check'] and r['latest_eligible_observation']==x['latest_eligible_observation']
            if success:assert r['acknowledgement'] and r['outcome']==x['outcome']
            else:assert r['acknowledgement'] is None and r['outcome']=='publication_failure'
            stamp=datetime.now(timezone.utc).replace(microsecond=0)+timedelta(seconds=1);now=stamp.isoformat().replace('+00:00','Z');h=o.aggregate(rs,now)
            if not success:assert h['networks'][network]['acknowledgement']==old['acknowledgement']
            inventory=dict(complete=True,checked_at_utc=now,runs=[dict(network=network,cycle=x['cycle'],kind='primary',status='completed',conclusion='success' if success else 'failure',run_id=x['run_id'],attempt=x['attempt'],started_at_utc=x['started_at_utc'],finished_at_utc=now)])
            decisions=o.proposals(h,x['cycle'],now,(stamp-timedelta(hours=1)).isoformat().replace('+00:00','Z'),(stamp+timedelta(hours=1)).isoformat().replace('+00:00','Z'),{network:True},inventory)
            decision=next(d for d in decisions['decisions'] if d['network']==network);assert decision['action']==('none' if success else 'propose_publication_recovery'),decision
            record=dict(network=network,case=case,passed=True,template_sha256=dc.sha(template),actual_receipt_shell=command,receipt_exit=p.returncode,stdout=p.stdout,stderr=p.stderr,export_shell=export['run'],export_exit=q.returncode,export_stdout=q.stdout,export_stderr=q.stderr,result=r,prior_acknowledgement=old['acknowledgement'],health_manifest=dc.load(m),health_manifest_sha256=dc.sha(m),decision=decision,scheduler_inventory=inventory,boundary_double=case if case in ('receipt-head-mismatch','interrupted-finalization','validation-exception') else None)
            dc.write(temp/'evidence.json',record);checks.append(record);print(network,case,'passed',flush=True)
    report=dict(status='passed',checks=checks,external_requests=0,Git_writes=0,interpretation='Actual CLI and YAML shell steps; invalid validator/HEAD/signal boundaries explicitly identified. SIGKILL/missing checkout cannot write a receipt: prior health plus scheduler inventory remains authoritative; export requires a surviving runner.')
    dc.write(root/'report.json',report);return report
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True)
    for n in ('scan','dendra'):
        p.add_argument('--'+n+'-repo',required=True);p.add_argument('--'+n+'-prepared',required=True)
    a=p.parse_args();run(a.root,{n:getattr(a,n+'_repo') for n in ('scan','dendra')},{n:getattr(a,n+'_prepared') for n in ('scan','dendra')})
