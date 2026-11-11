"""SCAN-only affected native qualification; external networking must be denied."""
import argparse, errno, json, os, pathlib, shutil, socket, subprocess, sys, threading
from scan_http_fixture_server import FixtureServer

ROOT=pathlib.Path(__file__).resolve().parents[2]

def run(out):
    assert os.geteuid()!=0
    assert not any(any(word in k.upper() for word in ('TOKEN','SECRET','PASSWORD','CREDENTIAL')) for k in os.environ)
    out.mkdir(parents=True,exist_ok=False)
    if sys.platform.startswith('linux'):
        assert os.readlink('/proc/self/ns/net')!=os.environ['SMS_OUTER_NETNS']
        assert len(pathlib.Path('/proc/net/route').read_text().splitlines())==1
    with socket.socket() as s:
        s.settimeout(.5)
        try:s.connect(('192.0.2.1',9))
        except OSError as e:assert e.errno in (errno.EPERM,errno.EACCES,errno.ENETUNREACH,errno.EHOSTUNREACH)
        else:raise AssertionError('External route not denied')
    code='x<-list(R=as.character(getRversion()),packages=setNames(lapply(c("soilDB","httr","curl"),function(p)as.character(packageVersion(p))),c("soilDB","httr","curl")));jsonlite::write_json(x,commandArgs(TRUE)[1],auto_unbox=TRUE)'
    subprocess.run(['Rscript','--vanilla','-e',code,str(out/'runtime.json')],check=True,cwd=ROOT,timeout=30)
    runtime=json.loads((out/'runtime.json').read_text())
    if sys.platform.startswith('linux'):
        assert runtime['R']=='4.6.1' and runtime['packages']=={'soilDB':'2.9.2','httr':'1.4.9','curl':'8.1.0'}
    else:assert runtime['packages']['soilDB']=='2.9.2' # Explicit Mac preliminary only.
    server=FixtureServer(out);thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.05});thread.start()
    env=dict(os.environ,SCAN_TEST_LOOPBACK_PORT=str(server.server_port),SCAN_NATIVE_TIMEOUT_RECEIPT=str(out/'native-timeout.json'))
    exits={}
    try:
        for name in ('test_scan_nwcc_compat.R','test_scan_metered_http.R','test_scan_native_timeout.R'):
            with (out/(name+'.log')).open('xb') as f:
                p=subprocess.run(['Rscript','--vanilla',str(ROOT/'tests/soil_moisture'/name)],cwd=ROOT,env=env,stdout=f,stderr=f,timeout=90)
            exits[name]=p.returncode
            assert p.returncode==0, name+' failed; no raw output disclosed'
        assert not server.failure
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3);assert not thread.is_alive()
        (out/'closure.json').write_text(json.dumps({'actual_exits':exits,'fixture_requests':len(server.records),'fixture_body_bytes':server.body_bytes,'server_closed':True,'provider_requests':0}))
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True,timeout=5).strip()
    if os.environ.get('GITHUB_SHA'):assert head==os.environ['GITHUB_SHA']
    result={'status':'PASS','platform':sys.platform,'source_sha':head,'runtime':runtime,'native_timeout':json.loads((out/'native-timeout.json').read_text()),'actual_exits':exits,'fixture_requests':len(server.records),'fixture_body_bytes':server.body_bytes,'external_egress_denied':True,'provider_requests':0,'server_closed':True}
    (out/'hosted-qualification.json').write_text(json.dumps(result,sort_keys=True)+'\n')
    print('SCAN contained native qualification PASS ('+sys.platform+')')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=pathlib.Path,required=True);run(p.parse_args().output)
