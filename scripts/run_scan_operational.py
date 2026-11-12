"""Bounded SCAN child output/footprint. Raw provider logs never enter Actions logs."""
import argparse, json, os, pathlib, resource, signal, stat, subprocess, time

PRODUCTS=("scan_soil_moisture_latest.geojson","scan_soil_moisture_latest_summary.json",
          "scan_soil_moisture_current_wy_trace.csv","scan_soil_moisture_current_wy_trace_summary.json","scan_depth_style.csv")

R_OUTCOMES={'BUILDER_EXCEPTION','ACCOUNT_REJECTED','RUNTIME_RECEIPT_FAILURE','BUILT_ACCOUNT_COMPLETE'}
PHASES={'RUNTIME_ADMISSION','BUILDER','ACCOUNT_SNAPSHOT','ACCOUNT_PREDICATE','RECEIPT_WRITE','COMPLETE'}
ERRORS={'NONE','SCAN_FATAL','SIMPLE_ERROR','OTHER_ERROR'}
ACCOUNT_REFUSALS={'NONE','NOT_EVALUATED','ACCOUNT_SHAPE','FATAL_PRESENT','REQUEST_COUNT_TYPE',
                 'RECEIPT_COUNT_TYPE','RECEIPT_SHAPE','RECEIPT_STATE','RECEIPT_STATUS',
                 'RECEIPT_ATTEMPT','RECEIPT_METHOD','RECEIPT_BYTES','TIMEOUT_NONFINITE_TYPE',
                 'TIMEOUT_LIMIT','TOTAL_BYTES','BYTE_SUM','SNAPSHOT_EXCEPTION',
                 'PREDICATE_EXCEPTION','RECEIPT_EXCEPTION'}

def diagnostic_json(path):
    # Never return arbitrary file content in an exception or public diagnostic.
    if path.is_symlink() or not path.is_file() or path.stat().st_size>65536:
        return None
    try:
        value=json.loads(path.read_text())
        return value if isinstance(value,dict) else None
    except (OSError,ValueError):
        return None

def bounded_state(root,name):
    value=diagnostic_json(root/name/'bounded.json')
    if value is None:return 'MISSING'
    if value.get('reason') is not None or value.get('remaining_owned_processes')!=[]:
        return 'SUPERVISOR_FAILURE'
    code=value.get('returncode')
    if type(code) is not int:return 'MISSING'
    return 'SUCCESS' if code==0 else 'CHILD_FAILURE'

def finalize(root,build,validation,metadata):
    """Independent always-step. Raw child logs are never opened or uploaded."""
    states={'success','failure','cancelled','skipped'}
    build=build if build in states else 'unverified'
    validation=validation if validation in states else 'unverified'
    metadata=metadata if metadata in states else 'unverified'
    runtime=diagnostic_json(root/'scan-control/runtime-account.json') or {}
    status=runtime.get('status')
    status=status if isinstance(status,str) and status in R_OUTCOMES else 'UNVERIFIED'
    phase=runtime.get('phase');phase=phase if isinstance(phase,str) and phase in PHASES else 'UNVERIFIED'
    error=runtime.get('exception_class');error=error if isinstance(error,str) and error in ERRORS else 'UNVERIFIED'
    refusal=runtime.get('account_refusal')
    refusal=refusal if isinstance(refusal,str) and refusal in ACCOUNT_REFUSALS else 'UNVERIFIED'
    supervised=bounded_state(root,'scan-acquisition-bounded')
    if build=='skipped':outcome='NOT_RUN'
    elif supervised in {'MISSING','SUPERVISOR_FAILURE'} or build=='cancelled':outcome='SUPERVISOR_FAILURE'
    elif build=='failure' and status in R_OUTCOMES-{'BUILT_ACCOUNT_COMPLETE'} and supervised=='CHILD_FAILURE':outcome=status
    elif build=='failure' and status=='UNVERIFIED' and supervised=='CHILD_FAILURE':outcome='RUNTIME_RECEIPT_FAILURE'
    elif build!='success' or supervised!='SUCCESS':outcome='SUPERVISOR_FAILURE'
    elif status!='BUILT_ACCOUNT_COMPLETE' or refusal!='NONE' or runtime.get('account_predicate') is not True:outcome='RUNTIME_RECEIPT_FAILURE'
    elif validation!='success' or bounded_state(root,'scan-validation-bounded')!='SUCCESS':
        outcome='VALIDATION_FAILURE' if validation=='failure' and bounded_state(root,'scan-validation-bounded')=='CHILD_FAILURE' else 'SUPERVISOR_FAILURE'
    elif bounded_state(root,'scan-metadata-bounded') in {'MISSING','SUPERVISOR_FAILURE'}:outcome='SUPERVISOR_FAILURE'
    elif metadata!='success' or bounded_state(root,'scan-metadata-bounded')!='SUCCESS':outcome='RUNTIME_RECEIPT_FAILURE'
    else:outcome='VALIDATED_CANDIDATE'
    record=dict(schema='scan-safe-failure-1',outcome=outcome,runtime_status=status,
                phase=phase,exception_class=error,account_refusal=refusal,build_step=build,
                validation_step=validation,metadata_step=metadata,
                acquisition_supervision=supervised,raw_logs_public=False)
    control=root/'scan-control';control.mkdir(exist_ok=True)
    with (control/'failure-summary.json').open('x') as f:
        json.dump(record,f,sort_keys=True);f.write('\n');f.flush();os.fsync(f.fileno())
    print('SCAN diagnostic '+outcome)
    return 0 if outcome=='VALIDATED_CANDIDATE' else 1

def audit_candidate(root,account):
    expected={'candidate/docs/data/'+p for p in PRODUCTS}|{'candidate-metadata.json'}
    entries=list(root.rglob('*'))
    assert not any(p.is_symlink() for p in entries), 'Linked artifact'
    assert {str(p.relative_to(root)) for p in entries if p.is_file()}==expected,'Artifact disclosure boundary'
    assert sum(p.stat().st_size for p in entries if p.is_file())<=64*1024**2,'Artifact byte limit'
    value=json.loads(account.read_text())
    assert value['status']=='BUILT_ACCOUNT_COMPLETE' and value['account']['requests']==29
    assert value['account_refusal']=='NONE' and value['account_predicate'] is True
    assert value['raw_provider_errors_disclosed'] is False


def bounded(argv,out,roots,seconds,file_limit=256*1024**2,trigger=8*1024**3):
    out.mkdir(parents=True,exist_ok=False)
    start=time.monotonic();p=None;reason=None;peak=0
    def interrupted(signum, frame):raise InterruptedError('supervisor termination')
    old_handler=signal.signal(signal.SIGTERM,interrupted)
    def limits():resource.setrlimit(resource.RLIMIT_FSIZE,(file_limit,file_limit));os.umask(0o077)
    try:
        with (out/'child.stdout').open('xb') as stdout,(out/'child.stderr').open('xb') as stderr:
            p=subprocess.Popen(argv,stdout=stdout,stderr=stderr,start_new_session=True,preexec_fn=limits)
            while True:
                # These are mutable child scratch trees, not strict source pins.
                # Files removed by the child between enumeration and lstat add
                # no currently retained blocks; other errors still refuse.
                size=0
                for base in roots:
                    for q in base.rglob('*'):
                        try:s=q.lstat()
                        except FileNotFoundError:continue
                        if stat.S_ISREG(s.st_mode):size+=s.st_blocks*512
                peak=max(peak,size)
                if size>=trigger:reason='FOOTPRINT_TRIGGER'
                if os.fstat(stdout.fileno()).st_size+os.fstat(stderr.fileno()).st_size>16*1024**2:reason='LOG_TRIGGER'
                if time.monotonic()-start>seconds:reason='TIME_LIMIT'
                if reason or p.poll() is not None:break
                time.sleep(.2)
            if reason and p.poll() is None:os.killpg(p.pid,signal.SIGTERM)
            try:p.wait(timeout=5)
            except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait(timeout=5)
    finally:
        if p and p.poll() is None:os.killpg(p.pid,signal.SIGKILL);p.wait(timeout=5)
        if p:
            rows=subprocess.check_output(['ps','-axo','pid=,pgid=,stat='],text=True,timeout=2).splitlines()
            alive=[r for r in rows if len(r.split())==3 and int(r.split()[1])==p.pid and not r.split()[2].startswith('Z')]
            if alive:
                reason='OWNED_DESCENDANT_REMAINED';os.killpg(p.pid,signal.SIGKILL)
                time.sleep(.2)
                rows=subprocess.check_output(['ps','-axo','pid=,pgid=,stat='],text=True,timeout=2).splitlines()
                alive=[r for r in rows if len(r.split())==3 and int(r.split()[1])==p.pid and not r.split()[2].startswith('Z')]
        else:alive=[]
        result={'returncode':p.returncode if p else None,'reason':reason,'elapsed_seconds':time.monotonic()-start,'peak_sampled_allocated_bytes':peak,'footprint_trigger_bytes':trigger,'per_file_limit_bytes':file_limit,'instantaneous_aggregate_cap_proven':False,'remaining_owned_processes':alive,'raw_logs_public':False}
        (out/'bounded.json').write_text(json.dumps(result,sort_keys=True)+'\n')
        signal.signal(signal.SIGTERM,old_handler)
    print('SCAN bounded child '+('PASS' if p.returncode==0 and reason is None and not alive else 'FAILED'))
    return p.returncode if reason is None and not alive else 125

def main():
    import sys
    if len(sys.argv)==6 and sys.argv[1]=='finalize':
        try:code=finalize(pathlib.Path(sys.argv[2]),*sys.argv[3:])
        except Exception:
            print('SCAN diagnostic RUNTIME_RECEIPT_FAILURE');code=1
        raise SystemExit(code)
    if len(sys.argv)==4 and sys.argv[1]=='audit-candidate':
        audit_candidate(pathlib.Path(sys.argv[2]),pathlib.Path(sys.argv[3]));raise SystemExit(0)
    p=argparse.ArgumentParser();p.add_argument('--output',type=pathlib.Path,required=True);p.add_argument('--root',type=pathlib.Path,action='append',required=True);p.add_argument('--seconds',type=int,required=True);p.add_argument('command',nargs=argparse.REMAINDER);a=p.parse_args();assert 0<a.seconds<=3300
    assert a.command[0]=='--';raise SystemExit(bounded(a.command[1:],a.output,a.root,a.seconds))

if __name__=='__main__':
    try:main()
    except Exception:
        # Launch, audit, filesystem and supervisor exception text can contain
        # private paths or provider content. Detailed child logs stay local.
        print('SCAN wrapper RUNTIME_RECEIPT_FAILURE')
        raise SystemExit(1)
