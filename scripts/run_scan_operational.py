"""Bounded SCAN child output/footprint. Raw provider logs never enter Actions logs."""
import argparse, json, os, pathlib, resource, signal, stat, subprocess, time

PRODUCTS=("scan_soil_moisture_latest.geojson","scan_soil_moisture_latest_summary.json",
          "scan_soil_moisture_current_wy_trace.csv","scan_soil_moisture_current_wy_trace_summary.json","scan_depth_style.csv")

def audit_candidate(root,account):
    expected={'candidate/docs/data/'+p for p in PRODUCTS}|{'candidate-metadata.json'}
    entries=list(root.rglob('*'))
    assert not any(p.is_symlink() for p in entries), 'Linked artifact'
    assert {str(p.relative_to(root)) for p in entries if p.is_file()}==expected,'Artifact disclosure boundary'
    assert sum(p.stat().st_size for p in entries if p.is_file())<=64*1024**2,'Artifact byte limit'
    value=json.loads(account.read_text())
    assert value['status']=='BUILT_ACCOUNT_COMPLETE' and value['account']['requests']==29
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

if __name__=='__main__':
    import sys
    if len(sys.argv)==4 and sys.argv[1]=='audit-candidate':
        audit_candidate(pathlib.Path(sys.argv[2]),pathlib.Path(sys.argv[3]));raise SystemExit(0)
    p=argparse.ArgumentParser();p.add_argument('--output',type=pathlib.Path,required=True);p.add_argument('--root',type=pathlib.Path,action='append',required=True);p.add_argument('--seconds',type=int,required=True);p.add_argument('command',nargs=argparse.REMAINDER);a=p.parse_args();assert 0<a.seconds<=3300
    assert a.command[0]=='--';raise SystemExit(bounded(a.command[1:],a.output,a.root,a.seconds))
