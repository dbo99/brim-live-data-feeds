#!/usr/bin/env python3
"""Actual pure-policy regression inputs/outputs, including review counterexamples."""
import argparse,copy,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import soil_moisture_operations as o
C='2026-09-22';T=C+'T10:37:00Z';END=C+'T10:45:00Z';NOW=C+'T14:00:00Z'
def scheduler(rid='42',attempt=1,cycle=C,status='completed',conclusion='failure',start=T,end=END,kind='primary',network='dendra'):
    return dict(network=network,cycle=cycle,kind=kind,status=status,conclusion=conclusion if status in ('completed','cancelled') else None,run_id=rid,attempt=attempt,started_at_utc=start,finished_at_utc=end if status in ('completed','cancelled') else None)
def event(rid='42',attempt=1,outcome='provider_failure',phase='prepare',candidate=None,ack=None,start=T,end=END,network='dendra'):
    return o.result(network,rid,C,start,end,outcome,attempt=attempt,phase=phase,candidate_generation=candidate,acknowledgement=ack,last_successful_source_check=T if candidate else None,latest_eligible_observation='2026-09-20' if candidate else None)
def receipt():return dict(generation='verified',commit='a'*40,index_sha256='b'*64,observed_at_utc=END,evidence='verified_committed_product',commit_ancestry=['a'*40])
def run():
    cases=[]
    def check(name,events,runs,action,reason,network='dendra'):
        args=dict(health=o.aggregate(events,NOW),cycle=C,now=NOW,window_start=C+'T13:00:00Z',window_end=C+'T15:00:00Z',enabled=dict(scan=True,dendra=True,snotel=True),run_inventory=dict(complete=True,checked_at_utc=C+'T13:59:59Z',runs=runs))
        out=o.proposals(**args);d=next(d for d in out['decisions'] if d['network']==network)
        assert (d['action'],d['reason'])==(action,reason),(name,d)
        assert out['dispatches']==0 and o.proposals(**args)==out
        cases.append(dict(name=name,input=args,output=out,passed=True));return d
    for cycle in ('2026-09-21','2026-09-23'):
        for status in ('queued','in_progress'):
            check('active-'+cycle+'-'+status,[],[scheduler(cycle=cycle,status=status)],'none','primary_or_retry_active')
    check('matching-provider-failure',[event()],[scheduler()],'propose_retry','provider_failure')
    old=scheduler(rid='lexically-z-old',start=C+'T09:00:00Z',end=C+'T12:00:00Z')
    for rs in ([old,scheduler()],[scheduler(),old]):
        check('older-completed-primary-does-not-shadow',[event() ],rs,'propose_retry','provider_failure')
    check('delayed-older-health-completion',[event(),event(rid='lexically-z-old',start=C+'T09:00:00Z',end=C+'T12:00:00Z')],[old,scheduler()],'propose_retry','provider_failure')
    check('attempt-2-matching',[event(attempt=2)],[scheduler(attempt=1,start=C+'T10:00:00Z'),scheduler(attempt=2)],'propose_retry','provider_failure')
    check('attempt-1-cannot-explain-attempt-2',[event()],[scheduler(attempt=2)],'none','terminal_run_result_missing_or_stale')
    for conclusion in ('failure','success','cancelled'):
        check('prepare-only-'+conclusion,[event(outcome='success_changes',candidate='prepared')],[scheduler(conclusion=conclusion)],'propose_publication_recovery','prepared_candidate_delivery_unverified')
    check('receipt-failure-keeps-candidate',[event(outcome='publication_failure',phase='publication',candidate='prepared')],[scheduler()],'propose_publication_recovery','prepared_candidate_delivery_unverified')
    for outcome in ('success_changes','success_no_data_change'):
        check('verified-'+outcome,[event(outcome=outcome,phase='publication',candidate='prepared',ack=receipt())],[scheduler(conclusion='success')],'none','verified_delivery')
    check('verified-but-workflow-degraded',[event(outcome='success_changes',phase='publication',candidate='prepared',ack=receipt())],[scheduler()],'none','verified_delivery_workflow_degraded')
    for outcome in ('semantic_hold','data_loss_hold'):
        check(outcome,[event(outcome=outcome)],[scheduler()],'none','review_required')
        check(outcome+'-not-bypassed-by-publication',[event(outcome=outcome),event(outcome='publication_failure',phase='publication',candidate='prepared')],[scheduler()],'none','review_required')
    check('absent-terminal-health',[],[scheduler()],'none','terminal_run_result_missing_or_stale')
    check('ambiguous-start',[],[scheduler(),scheduler(rid='43')],'none','ambiguous_scheduler_chronology')
    check('cancelled-without-health',[],[scheduler(status='cancelled',conclusion='cancelled')],'none','terminal_run_result_missing_or_stale')
    check('reserved-retry',[],[scheduler(kind='retry',status='reserved')],'none','primary_or_retry_active')
    check('cancelled-retry-consumes-quota',[event()],[scheduler(kind='retry',status='cancelled',conclusion='cancelled')],'none','retry_already_reserved_or_attempted')
    check('no-run-missed',[],[],'propose_retry','missed_primary')
    check('health-inventory-disagrees',[event()],[],'none','health_scheduler_inventory_disagreement')
    check('SNOTEL-always-gated',[],[],'none','source_not_enabled',network='snotel')
    both=[event(),event(network='scan',outcome='success_no_data_change',phase='publication',candidate='scan-verified',ack=receipt())]
    inv=[scheduler(),scheduler(network='scan',conclusion='success')]
    check('SCAN-success-does-not-block-Dendra',both,inv,'propose_retry','provider_failure')
    check('Dendra-failure-does-not-retry-SCAN',both,inv,'none','verified_delivery',network='scan')
    # Strict inventory boundaries reject rather than silently dispatch with partial evidence.
    original=cases[4]['input']
    for name,mutate in [('incomplete',lambda x:x['run_inventory'].update(complete=False)),('stale',lambda x:x['run_inventory'].update(checked_at_utc=T)),('missing-attempt',lambda x:x['run_inventory']['runs'][0].pop('attempt')),('duplicate',lambda x:x['run_inventory']['runs'].append(copy.deepcopy(x['run_inventory']['runs'][0])))]:
        x=copy.deepcopy(original);mutate(x)
        try:o.proposals(**x)
        except (ValueError,KeyError) as e:cases.append(dict(name=name,input=x,rejected=str(e),passed=True))
        else:raise AssertionError(name)
    return dict(status='passed',external_requests=0,actual_policy=True,cases=cases)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--report',required=True);a=p.parse_args();r=run();Path(a.report).write_text(json.dumps(r,indent=2)+'\n');print(len(r['cases']),'morning boundary cases passed')
