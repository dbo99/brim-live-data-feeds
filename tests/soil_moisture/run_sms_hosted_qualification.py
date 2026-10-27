#!/usr/bin/env python3
"""Serial, synthetic-only hosted qualification and finite runtime provisioning."""
from pathlib import Path
import argparse, csv, errno, gzip, hashlib, io, json, os, re, shutil, signal, socket, stat, subprocess, sys, tarfile, threading, time, urllib.request, urllib.parse
ROOT=Path(__file__).resolve().parents[2]
LOCK=json.loads((Path(__file__).parent/'hosted_runtime_lock.json').read_text())
PATHS=['docs/data/scan_soil_moisture_latest.geojson','docs/data/scan_soil_moisture_latest_summary.json','docs/data/scan_soil_moisture_current_wy_trace.csv','docs/data/scan_soil_moisture_current_wy_trace_summary.json','docs/data/scan_depth_style.csv']
def dump(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as f:json.dump(value,f,sort_keys=True,indent=2);f.write('\n')
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def pins(root):
    out={}
    for name in PATHS:
        p=root/name;s=p.lstat();assert stat.S_ISREG(s.st_mode) and not p.is_symlink()
        out[name]=dict(bytes=s.st_size,sha256=sha(p),ctime_ns=s.st_ctime_ns,mtime_ns=s.st_mtime_ns,ino=s.st_ino,dev=s.st_dev,mode=stat.S_IMODE(s.st_mode),nlink=s.st_nlink)
    return out

def command(argv,out,name,timeout=90,env=None,cwd=ROOT,ok=True):
    with (out/(name+'.stdout')).open('xb') as stdout,(out/(name+'.stderr')).open('xb') as stderr:
        p=subprocess.run(argv,cwd=cwd,env=env,stdout=stdout,stderr=stderr,timeout=timeout)
    if ok and p.returncode:raise RuntimeError(name+' failed; retained bounded log')
    return p.returncode


def check_controls(out):
    import copy, datetime, textwrap
    raw=(ROOT/'.github/workflows/build-scan-soil-moisture-latest.yml').read_text()
    lines=raw.splitlines();first=next(i for i,x in enumerate(lines) if '# BEGIN SCAN_APPROVAL_GUARD' in x);last=next(i for i,x in enumerate(lines) if '# END SCAN_APPROVAL_GUARD' in x)
    body=textwrap.dedent('\n'.join(lines[first:last+1]))+'\n';script=out/'scan-approval-guard.py';script.write_text(body)
    names=('SCAN_HTTP_MAX_REQUESTS','SCAN_HTTP_RESPONSE_BYTES','SCAN_HTTP_TOTAL_BYTES','SCAN_HTTP_CAMPAIGN_SECONDS','SCAN_FETCH_TIMEOUT_SEC')
    values=dict(zip(names,[10,4096,16384,10,2]));now=datetime.datetime.now(datetime.timezone.utc)
    fmt=lambda x:x.strftime('%Y-%m-%dT%H:%M:%SZ')
    cfg=dict(approved=True,approval_id='SYNTHETIC-ONLY',source_sha='0'*40,run_number=90000000,not_before_utc=fmt(now-datetime.timedelta(seconds=60)),not_after_utc=fmt(now+datetime.timedelta(seconds=600)),budgets=values)
    base=dict(os.environ,GITHUB_SHA='0'*40,GITHUB_RUN_ID='SYNTHETIC',GITHUB_RUN_ATTEMPT='1',GITHUB_RUN_NUMBER='90000000',GITHUB_REF='refs/heads/main',**{k:str(v) for k,v in values.items()})
    cases=[('valid',{},{}),('missing',{},dict(SCAN_LIVE_APPROVAL_JSON='')),('rerun',{},dict(GITHUB_RUN_ATTEMPT='2')),('wrong_source',dict(source_sha='1'*40),{}),('wrong_run',dict(run_number=90000001),{}),('unapproved',dict(approved=False),{}),('expired',dict(not_after_utc=fmt(now-datetime.timedelta(seconds=1))),{}),('invalid_budget',{},dict(SCAN_HTTP_MAX_REQUESTS='NaN')),('budget_mismatch',{},dict(SCAN_HTTP_TOTAL_BYTES='16385'))]
    for name,change,envchange in cases:
        root=out/('control-'+name);root.mkdir();grant=copy.deepcopy(cfg);grant.update(change)
        env=dict(base,RUNNER_TEMP=str(root),SCAN_LIVE_APPROVAL_JSON=json.dumps(grant));env.update(envchange)
        rc=command([sys.executable,'-B',str(script)],out,'control-'+name,env=env,ok=False)
        assert (rc==0)==(name=='valid'),name
    assert raw.count('cron: "30 14 * * *"')==1 and "github.ref == 'refs/heads/main'" in raw and 'group: brim-live-main-publish' in raw and 'queue: max' in raw
    assert all(p in raw for p in PATHS)
    dump(out/'CONTROLS.json',dict(status='PASS',approval_cases=len(cases),production_workflow_dispatched=False,schedule_queue_and_allowlist_preserved=True))

def qualify(out, prior_requests=0, prior_bytes=0):
    from scan_http_fixture_server import FixtureServer
    assert 0<=prior_requests<256 and 0<=prior_bytes<64*1024**2
    assert os.geteuid()!=0,'Tests must not run as root'
    assert not any(re.search(r'(TOKEN|SECRET|PASSWORD|CREDENTIAL)',k,re.I) for k in os.environ),'Secrets must be removed from test environment'
    out.mkdir(parents=True,exist_ok=False);(out/'tmp').mkdir()
    os.environ['TMPDIR']=str(out/'tmp');os.environ['PYTHONDONTWRITEBYTECODE']='1';os.environ['GIT_OPTIONAL_LOCKS']='0';os.environ['GIT_NO_LAZY_FETCH']='1'
    if sys.platform.startswith('linux'):
        assert os.readlink('/proc/self/ns/net')!=os.environ['SMS_OUTER_NETNS']
        assert len(Path('/proc/net/route').read_text().splitlines())==1,'External IPv4 route exists'
    with socket.socket() as s:
        s.settimeout(.5)
        try:s.connect(('192.0.2.1',9))
        except OSError as e:assert e.errno in (errno.EPERM,errno.EACCES,errno.ENETUNREACH,errno.EHOSTUNREACH);denied=e.errno
        else:raise AssertionError('External egress was not denied')
    dump(out/'EGRESS.json',dict(status='PASS',external_reserved_test_address='192.0.2.1:9',errno=denied,native_process_tree_boundary='Linux network namespace' if sys.platform.startswith('linux') else 'Mac Seatbelt preliminary only',loopback_allowed=True,test_uid_nonroot=True,provider_requests=0))
    check_controls(out)
    rscript=shutil.which('Rscript');assert rscript
    runtime_code='''x<-list(R=as.character(getRversion()),platform=R.version$platform,os=R.version$os,packages=setNames(lapply(c("soilDB","httr","curl"),function(p)as.character(packageVersion(p))),c("soilDB","httr","curl")),native=curl::curl_version());jsonlite::write_json(x,commandArgs(TRUE)[1],auto_unbox=TRUE,pretty=TRUE)'''
    command([rscript,'--vanilla','-e',runtime_code,str(out/'RUNTIME.json')],out,'runtime')
    runtime=json.loads((out/'RUNTIME.json').read_text());assert runtime['packages']==LOCK['core_packages']
    import shlex
    compiler_config={}
    for name in ['CC','CXX','FC']:
        value=subprocess.check_output([shutil.which('R'),'CMD','config',name],text=True,timeout=5).strip()
        compiler_config[name]=value
    cc=shlex.split(compiler_config['CC']);assert cc
    compiler_version=subprocess.check_output([cc[0],'--version'],text=True,timeout=5)[:8192]
    dump(out/'COMPILER.json',dict(configuration=compiler_config,CC_version=compiler_version))
    if sys.platform.startswith('linux'):assert runtime['R']==LOCK['r']
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    if os.environ.get('GITHUB_SHA'):assert head==os.environ['GITHUB_SHA']
    source_names=['scripts/build_scan_soil_moisture_latest.R','scripts/soil_moisture/scan_metered_http.R','scripts/soil_moisture/scan_nwcc_compat.R','scripts/soil_moisture/snotel_snapshot.py','scripts/soil_network_workflow.py']
    dump(out/'SOURCE.json',dict(commit=head,run_id=os.environ.get('GITHUB_RUN_ID','local'),attempt=os.environ.get('GITHUB_RUN_ATTEMPT','1'),files={n:sha(ROOT/n) for n in source_names}))
    server=FixtureServer(out,int(os.environ.get('SMS_FIXTURE_PORT','0')));server.request_limit=256-prior_requests;server.byte_limit=64*1024**2-prior_bytes
    thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.05});thread.start()
    env=dict(os.environ,SCAN_TEST_LOOPBACK_PORT=str(server.server_port))
    reports=[]
    try:
        command([rscript,'--vanilla','tests/soil_moisture/test_scan_nwcc_compat.R'],out,'scan-compat',env=env)
        command([rscript,'--vanilla','tests/soil_moisture/test_scan_metered_http.R'],out,'scan-metered',env=env)
        # Native HTTP refusal/backoff assertions complement retained in-process cases.
        extra='''source("scripts/soil_moisture/scan_metered_http.R");f<-getFromNamespace(".make_SCAN_req","soilDB")(2218,2026,"SMS","Daily");for(path in c("/refusal","/retry-after")){b<-function(method,url,form,timeout,read_limit,consume,check)pt_scan_curl_backend(method,paste0("http://127.0.0.1:",Sys.getenv("SCAN_TEST_LOOPBACK_PORT"),path),form,timeout,read_limit,consume,check);h<-pt_scan_make_http(2,16384,32768,10,5,b);e<-tryCatch(h$request("POST","https://wcc.sc.egov.usda.gov/nwcc/view",f),error=identity);stopifnot(inherits(e,"scan_provider_refusal"),h$snapshot()$requests==1);e<-tryCatch(h$request("POST","https://wcc.sc.egov.usda.gov/nwcc/view",f),error=identity);stopifnot(inherits(e,"scan_fatal"),h$snapshot()$requests==1)}'''
        command([rscript,'--vanilla','-e',extra],out,'scan-native-refusal',env=env)
        modes=['success','transient_first','good_peer','fatal_preflight','source_scope','oversize','budget','canary_empty','main_empty','no_current','few_stations']
        expected_counts=dict(success=11,transient_first=12,good_peer=14,fatal_preflight=1,source_scope=0,oversize=2,budget=2,canary_empty=4,main_empty=8,no_current=11,few_stations=13)
        failures={
            'fatal_preflight':('scan_provider_refusal','SCAN provider refusal; no retry','scan_provider_refusal'),
            'source_scope':('scan_integrity_error','SCAN cached metadata does not exactly match authorized stations','scan_integrity_error'),
            'oversize':('scan_budget_exhausted','SCAN response/body cap exceeded (EOF probe included)','scan_budget_exhausted'),
            'budget':('scan_budget_exhausted','SCAN request/body budget exhausted','scan_budget_exhausted'),
            'canary_empty':('simpleError','SCAN SMS canary failed: 0 of 1 canary request(s) returned SMS rows; minimum required is 1.',None),
            'main_empty':('simpleError','SCAN fetch early-abort: the first 2 main station/year request(s) after the canary returned no SMS rows.',None),
            'no_current':('simpleError','No current-water-year SCAN SMS rows were returned. Not publishing a replacement feed.',None),
            'few_stations':('simpleError','Only 9 SCAN stations had current-water-year SMS data; minimum required to publish is 10. Not publishing replacement feed files.',None)}
        for mode in modes:
            run=out/mode;failed=mode not in ['success','transient_first','good_peer'];before=None
            if not failed:run.mkdir()
            if failed:
                assert run.is_dir()
                # Copies become admitted last-good only after the preparation step
                # has closed. No failed test is repinned or original data mutated.
                before=pins(run);dump(run/'LAST_GOOD_BEFORE.json',before)
            count=len(server.records)
            rc=command([rscript,'--vanilla','tests/soil_moisture/run_scan_hosted_qualification.R',str(ROOT),str(run),mode,str(server.server_port)],out,'builder-'+mode,env=env,ok=False)
            receipt=json.loads((run/'BUILDER_RECEIPT.json').read_text());assert (rc!=0)==failed,(mode,rc)
            attempts=len(server.records)-count;assert attempts==expected_counts[mode],(mode,attempts)
            assert receipt['success']==(not failed) and receipt['account']['requests']==attempts,mode
            if failed:
                error_class,message,fatal=failures[mode]
                assert error_class in receipt['error']['class'] and receipt['error']['message'].startswith(message) and receipt['account']['fatal']==fatal,(mode,receipt)
                after=pins(run);dump(run/'LAST_GOOD_AFTER.json',after);assert before==after,(mode,'last-good changed')
            else:
                assert receipt['account']['fatal'] is None
                v=command([sys.executable,'-B','scripts/scan_soil_moisture_publisher.py','validate','--root',str(run)],out,'validate-'+mode,env=env,ok=False)
                assert (v==0)==(mode!='good_peer'),mode
                if mode=='good_peer':assert (out/'validate-good_peer.stderr').read_text().strip()=='SCAN_PUBLISHER_REJECTED: SCAN feature 10 has invalid display_sms_pct'
                rows=list(csv.DictReader((run/PATHS[2]).open()));assert len(rows)==40
                assert all(float(r['sms_pct'])==(0 if float(r['depth_in'])==2 else 30) and int(r['sensor_count'])==(1 if float(r['depth_in'])==2 else 2) for r in rows)
                assert {r['obs_date'] for r in rows}=={'2026-10-01','2026-10-25'} and all(int(r['water_year'])==2027 for r in rows) and {int(r['water_day']) for r in rows}=={1,25}
                if mode=='transient_first':assert all((run/n).read_bytes()==(out/'success'/n).read_bytes() for n in PATHS)
            if mode=='success':
                creation={}
                for later in modes[3:]:
                    target=out/later;target.mkdir()
                    for n in PATHS:(target/n).parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(run/n,target/n)
                    creation[later]=pins(target)
                dump(out/'LAST_GOOD_CREATION_PHASE.json',creation)
            reports.append(dict(case=mode,status='PASS',physical_requests=attempts,last_good_preserved=failed,publisher='EXPECTED_MISSING_STATION_REJECTION' if mode=='good_peer' else 'VALIDATED' if not failed else 'NOT_REACHED'))
        assert not server.failure
        dump(out/'SCAN.json',dict(status='PASS',cases=reports,actual_backend='accepted R curl connection; actual installed soilDB request/formatter and complete builder',provider_requests=0))
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3);assert not thread.is_alive()
        dump(out/'LOOPBACK.json',dict(physical_requests=len(server.records),conservative_body_bytes=server.body_bytes,request_limit=server.request_limit,body_limit=server.byte_limit,fixture_max_bytes=8*1024**2,server_exit_confirmed=True,provider_requests=0))
    import test_snotel_snapshot_hosted as snotel
    assert snotel.run(out/'SNOTEL.json')
    command([sys.executable,'-B','scripts/soil_moisture/snotel_saved_replay.py','--output',str(out/'tmp/legacy-replay')],out,'legacy-replay',env=env,timeout=60)
    dump(out/'SUMMARY.json',dict(status='PASS',source_commit=head,SCAN='native transport + complete builder + five outputs + last-good',SNOTEL='NEW snapshot CANDIDATE_READY + reconstruction/repeat + rollover',old_replay='separate unchanged synthetic coverage',provider_requests=0,observation_publication=False,production_qualified=False))

# Preserve the corrected supervisor pattern: immediate sampled RSS action and
# bounded child cleanup. No slow disk/accounting call precedes a threshold signal.
def supervise(argv,out,seconds,trigger,ceiling):
    out.mkdir(parents=True,exist_ok=True);start=time.monotonic();peak=0;reason=None;p=None
    observations=[];last=None;gap=0;completed=False;rc=125
    try:
        p=subprocess.Popen(argv,start_new_session=True)
        while True:
            rows=[tuple(map(int,x.split())) for x in subprocess.check_output(['ps','-axo','pid=,ppid=,pgid=,rss='],text=True,timeout=2).splitlines() if len(x.split())==4]
            selected={os.getpid(),p.pid};previous=set()
            while previous!=selected:
                previous=set(selected);selected.update(pid for pid,parent,group,rss in rows if parent in selected or group==p.pid)
            rss=sum(rss*1024 for pid,parent,group,rss in rows if pid in selected);peak=max(peak,rss);now=time.monotonic()
            if rss>=trigger:
                reason='RSS preventive termination';os.killpg(p.pid,signal.SIGTERM);break
            if last is not None:gap=max(gap,now-last)
            last=now;observations.append(dict(elapsed=now-start,rss_bytes=rss))
            if now-start>=seconds:reason='time limit';os.killpg(p.pid,signal.SIGTERM);break
            if p.poll() is not None:break
            time.sleep(.2)
        if reason:
            try:p.wait(timeout=5)
            except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait(timeout=5)
        rc=p.wait(timeout=5);completed=True
    except BaseException as exc:
        reason='supervision failure: '+type(exc).__name__
        raise
    finally:
        if p and p.poll() is None:os.killpg(p.pid,signal.SIGKILL);p.wait(timeout=5)
        rows=subprocess.check_output(['ps','-axo','pid=,pgid='],text=True,timeout=2).splitlines()
        alive=[r for r in rows if len(r.split())==2 and int(r.split()[1])==p.pid] if p else []
        if alive:
            reason=reason or 'residual process group'
            try:os.killpg(p.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            end=time.monotonic()+5
            while alive and time.monotonic()<end:
                time.sleep(.1)
                rows=subprocess.check_output(['ps','-axo','pid=,pgid=,stat='],text=True,timeout=2).splitlines()
                alive=[r for r in rows if len(r.split())==3 and int(r.split()[1])==p.pid and not r.split()[2].startswith('Z')]
        dump(out/'SUPERVISION.json',dict(completed=completed,returncode=p.returncode if p else None,reason=reason,elapsed_seconds=time.monotonic()-start,peak_sampled_aggregate_rss_bytes=peak,RSS_trigger=trigger,RSS_ceiling=ceiling,max_sample_gap_seconds=gap,instantaneous_hard_cap_proven=False,remaining_group_processes=alive,samples=observations))
    return rc if not reason and not alive else 125


def dcf(text):
    records=[];row={};key=None
    for line in text.splitlines()+['']:
        if not line.strip():
            if row:records.append(row);row={};key=None
        elif line[:1].isspace():
            assert key;row[key]+=' '+line.strip()
        else:key,value=line.split(':',1);row[key]=value.strip()
    return records

def apt_download_url(planned_url, mirror_text=None):
    """Resolve only the supported runner mirror-file transport to official URLs."""
    target=urllib.parse.urlsplit(planned_url)
    assert not target.query and not target.fragment and not target.username and not target.password
    hosts={'archive.ubuntu.com','security.ubuntu.com','azure.archive.ubuntu.com'}
    if target.scheme in ('http','https'):
        assert target.hostname in hosts and target.port is None
        assert target.path.startswith('/ubuntu/pool/') and '..' not in urllib.parse.unquote(target.path).split('/')
        return planned_url
    assert target.scheme=='mirror+file' and not target.netloc
    prefix='/etc/apt/apt-mirrors.txt/'
    assert target.path.startswith(prefix)
    relative=target.path[len(prefix):]
    assert relative.startswith('pool/') and '..' not in urllib.parse.unquote(relative).split('/')
    assert mirror_text is not None and len(mirror_text.encode())<=65536
    choices=[]
    for line in mirror_text.splitlines():
        line=line.split('#',1)[0].strip()
        if not line:continue
        match=re.fullmatch(r'(https?://(?:archive\.ubuntu\.com|security\.ubuntu\.com|azure\.archive\.ubuntu\.com)/ubuntu/?)\s+priority:([123])',line)
        assert match,'Unsupported APT mirror entry'
        choices.append((int(match[2]),match[1].rstrip('/')+'/'))
    assert len(choices)==3 and {x[0] for x in choices}=={1,2,3}
    assert {urllib.parse.urlsplit(x[1]).hostname for x in choices}==hosts
    base=min(choices)[1]
    assert base=='https://archive.ubuntu.com/ubuntu/','Expected existing Canonical-first mirror configuration'
    return base+relative

def provision(out):
    """Download exact required bodies into runner scratch before installation.

    Reuse the runner's existing signed APT indexes; do not perform an unmetered
    index refresh. Missing/stale prerequisites fail this setup explicitly.
    """
    import shlex
    out.mkdir(parents=True,exist_ok=False);downloads=out/'downloads';downloads.mkdir();library=out/'library';library.mkdir()
    total=0;records=[];index=None
    allowed={'cran.r-project.org','cloud.r-project.org','archive.ubuntu.com','security.ubuntu.com','azure.archive.ubuntu.com'}
    class Redirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,req,fp,code,msg,headers,newurl):
            # Default urllib redirect handling reads intermediate bodies outside
            # the meter. Refuse instead of following or consuming those bodies.
            fp.close()
            raise RuntimeError('Dependency redirect refused')
    opener=urllib.request.build_opener(Redirect)
    def download(url,path,limit,expected_size=None,expected_digest=None):
        nonlocal total
        target=urllib.parse.urlsplit(url);assert target.hostname in allowed and target.scheme in ('http','https')
        limit=min(limit,LOCK['max_download_body_bytes']-total);assert limit>0
        h=hashlib.sha256();md5=hashlib.md5();size=0
        request=urllib.request.Request(url,headers={'User-Agent':'BRIM synthetic QA dependency setup'})
        with opener.open(request,timeout=30) as response,path.open('xb') as f:
            assert response.status==200
            while True:
                remaining=LOCK['max_download_body_bytes']-total;assert remaining>0,'Dependency aggregate body budget exhausted'
                chunk=response.read(min(65536,limit-size+1,remaining))
                if not chunk:break
                size+=len(chunk);total+=len(chunk);h.update(chunk);md5.update(chunk);f.write(chunk)
                (out/'DOWNLOAD_PROGRESS.json').write_text(json.dumps(dict(cumulative_consumed_body_bytes=total,current_url=url,current_bytes=size,ceiling=LOCK['max_download_body_bytes'])))
                assert size<=limit and total<=LOCK['max_download_body_bytes'],'Dependency body budget exceeded'
        if expected_size is not None:assert size==expected_size
        if expected_digest:
            algorithm,want=expected_digest.split(':',1);actual=h.hexdigest() if algorithm.lower()=='sha256' else md5.hexdigest() if algorithm.lower()=='md5sum' else None
            assert actual==want,'Dependency catalog hash mismatch'
        record=dict(url=url,file=path.name,bytes=size,sha256=h.hexdigest(),md5=md5.hexdigest());records.append(record)
        with (out/'DOWNLOADS.jsonl').open('a') as f:f.write(json.dumps(record)+'\n')
        return path
    apt=['libcurl4-openssl-dev','libssl-dev','libxml2-dev']
    plan=subprocess.check_output(['sudo','apt-get','--print-uris','--yes','--download-only','install',*apt],text=True,timeout=45)
    assert len(plan.encode())<=65536,'APT URI plan exceeded bounded diagnostic size'
    dump(out/'APT_PLAN.json',dict(plan=plan,requested_packages=apt))
    archives=out/'apt-archives';archives.mkdir();(archives/'partial').mkdir()
    routes=[]
    for line in plan.splitlines():
        if not line.startswith("'"):continue
        parts=shlex.split(line);assert len(parts)==4;url,name,size,digest=parts;assert Path(name).name==name
        mirror_text=None
        if urllib.parse.urlsplit(url).scheme=='mirror+file':
            mirror=Path('/etc/apt/apt-mirrors.txt');assert mirror.is_file() and not mirror.is_symlink() and mirror.stat().st_size<=65536
            mirror_text=mirror.read_text()
        resolved=apt_download_url(url,mirror_text)
        routes.append(dict(planned_url=url,resolved_url=resolved,file=name,bytes=int(size),catalog_digest=digest))
        with (out/'APT_ROUTES.jsonl').open('a') as f:f.write(json.dumps(routes[-1])+'\n')
        download(resolved,archives/name,128*1024**2,int(size),digest)
    dump(out/'APT_DOWNLOADS.json',dict(metadata='Existing platform signed APT indexes reused; no index update traffic',packages=apt,planned_uris=[r for r in records]))
    command(['sudo','apt-get','--yes','--no-download','-o','Dir::Cache::archives='+str(archives),'install',*apt],out,'apt-install',timeout=180)
    r=shutil.which('Rscript');assert r
    version=subprocess.check_output([r,'--vanilla','-e','cat(as.character(getRversion()))'],text=True).strip();assert version==LOCK['r'],'Pinned hardened R runtime unavailable'
    existing=subprocess.check_output([r,'--vanilla','-e','x<-installed.packages();write.table(x[,c("Package","Version")],stdout(),sep=intToUtf8(9),row.names=FALSE,col.names=FALSE,quote=FALSE)'],text=True)
    installed=dict(line.split('\t') for line in existing.splitlines() if '\t' in line)
    index_file=download(LOCK['official_source']+'PACKAGES.gz',downloads/'PACKAGES.gz',16*1024**2)
    with gzip.open(index_file,'rb') as f:index_bytes=f.read(LOCK['max_index_decoded_bytes']+1)
    assert len(index_bytes)<=LOCK['max_index_decoded_bytes'];index={x['Package']:x for x in dcf(index_bytes.decode())}
    selected={};install_order=[];visiting=set();reused={}
    def version_tuple(v):return tuple(int(x) for x in re.split('[.-]',v))
    def satisfies(v,op,want):
        if not op:return True
        a,b=version_tuple(v),version_tuple(want);return {'>=':a>=b,'>':a>b,'<=':a<=b,'<':a<b,'==':a==b}[op]
    def resolve(name,op=None,want=None):
        if name=='R':assert satisfies(version,op,want);return
        if name in installed and name not in LOCK['core_packages'] and satisfies(installed[name],op,want):reused[name]=installed[name];return
        if name in selected:assert satisfies(selected[name]['version'],op,want);return
        assert name not in visiting;visiting.add(name)
        meta=index.get(name);assert meta,'Required official package missing: '+name
        ver=LOCK['core_packages'].get(name,meta['Version']);assert satisfies(ver,op,want)
        current=meta['Version']==ver;url=LOCK['official_source']+(name+'_'+ver+'.tar.gz' if current else 'Archive/'+name+'/'+name+'_'+ver+'.tar.gz')
        archive=download(url,downloads/(name+'_'+ver+'.tar.gz'),LOCK['max_package_body_bytes'],expected_digest='MD5sum:'+meta['MD5sum'] if current and meta.get('MD5sum') else None)
        with tarfile.open(archive,'r:gz') as tar:
            member=tar.getmember(name+'/DESCRIPTION');assert member.isfile() and member.size<1024**2;description=dcf(tar.extractfile(member).read().decode())[0]
        assert description['Package']==name and description['Version']==ver
        selected[name]=dict(version=ver,archive=archive.name,sha256=sha(archive),url=url)
        for field in ['Depends','Imports','LinkingTo']:
            for item in description.get(field,'').split(','):
                item=item.strip()
                if not item:continue
                match=re.fullmatch(r'([A-Za-z][A-Za-z0-9.]*)\s*(?:\((>=|<=|==|>|<)\s*([0-9.-]+)\))?',item);assert match,item
                resolve(*match.groups())
        visiting.remove(name);install_order.append(name)
    for name in LOCK['required_packages']:resolve(name)
    dump(out/'RESOLVED_RUNTIME_LOCK.json',dict(R=version,source_index_sha256=sha(index_file),selected=selected,reused_platform_packages=reused,order=install_order,download_body_bytes=total,ceiling=LOCK['max_download_body_bytes'],platform_managed_delivery='GitHub VM/action and hardened R setup delivery are outside these explicit package-body counters; job/setup timeouts still apply.'))
    env=dict(os.environ,R_LIBS_USER=str(library),MAKEFLAGS='-j1')
    for name in install_order:
        command([shutil.which('R'),'CMD','INSTALL','--no-multiarch','--library='+str(library),str(downloads/selected[name]['archive'])],out,'install-'+name,timeout=360,env=env)
    with open(os.environ['GITHUB_ENV'],'a') as f:f.write('R_LIBS_USER='+str(library)+'\n')



def summarize(root):
    artifacts=root/'artifacts';artifacts.mkdir(exist_ok=False)
    mapping={'tests/SUMMARY.json':'COMPONENT_SUMMARY.json','tests/SCAN.json':'SCAN.json','tests/SNOTEL.json':'SNOTEL.json','tests/EGRESS.json':'EGRESS.json','tests/RUNTIME.json':'RUNTIME.json','tests/COMPILER.json':'COMPILER.json','tests/SOURCE.json':'SOURCE.json','tests/LOOPBACK.json':'LOOPBACK.json','tests/CONTROLS.json':'CONTROLS.json','setup/RESOLVED_RUNTIME_LOCK.json':'DEPENDENCIES.json','setup/APT_PLAN.json':'APT_PLAN.json','setup/APT_DOWNLOADS.json':'APT_DOWNLOADS.json','setup/DOWNLOAD_PROGRESS.json':'DOWNLOAD_PROGRESS.json','tests/tmp/legacy-replay/summary/SUMMARY.json':'LEGACY_REPLAY.json'}
    for src,dest in mapping.items():
        p=root/src
        if p.exists():shutil.copyfile(p,artifacts/dest)
    routes=root/'setup/APT_ROUTES.jsonl'
    if routes.exists():dump(artifacts/'APT_ROUTES.json',[json.loads(x) for x in routes.read_text().splitlines()])
    downloads=root/'setup/DOWNLOADS.jsonl'
    if downloads.exists():dump(artifacts/'DOWNLOADS.json',[json.loads(x) for x in downloads.read_text().splitlines()])
    monitors={}
    for name in ['setup-monitor','test-monitor']:
        p=root/name/'SUPERVISION.json'
        if p.exists():
            data=json.loads(p.read_text());data.pop('samples',None);monitors[name]=data;dump(artifacts/(name+'.json'),data)
    logs=[]
    for p in sorted(root.glob('**/*.stderr'))[:48]:
        if 'artifacts' in p.parts:continue
        text=p.read_bytes()[-8192:].decode(errors='replace');logs.append(p.name+'\n'+text)
    (artifacts/'DIAGNOSTICS.txt').write_text('\n'.join(logs))
    component=artifacts/'COMPONENT_SUMMARY.json'
    success=component.exists() and json.loads(component.read_text()).get('status')=='PASS' and len(monitors)==2 and all(m.get('completed') is True and m.get('returncode')==0 and m.get('reason') is None and m.get('remaining_group_processes')==[] for m in monitors.values())
    dump(artifacts/'SUMMARY.json',dict(status='PASS' if success else 'INCOMPLETE_OR_FAILED',component_pass=component.exists(),supervision_and_exit_pass=success,production_qualified=False))
    if not success:dump(artifacts/'FAILURE.json',dict(status='INCOMPLETE_OR_FAILED',provider_requests=0,claim='No hosted qualification pass',evidence='See bounded diagnostics and available step receipts; abrupt runner loss may prevent this artifact.'))
    dump(artifacts/'HOSTED_CONTEXT.json',dict(source_sha=os.environ.get('GITHUB_SHA'),run_id=os.environ.get('GITHUB_RUN_ID'),attempt=os.environ.get('GITHUB_RUN_ATTEMPT'),ref=os.environ.get('GITHUB_REF'),event=os.environ.get('GITHUB_EVENT_NAME'),observations_published=False,public_artifacts='Synthetic summaries only; no catalog, captured provider body, credential or production state.'))
    total=sum(p.stat().st_size for p in artifacts.iterdir());assert total<=64*1024**2
    dump(artifacts/'ARTIFACT_MANIFEST.json',dict(files={p.name:dict(bytes=p.stat().st_size,sha256=sha(p)) for p in sorted(artifacts.iterdir())},total_before_manifest_bytes=total))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['qualify','supervise','provision','summarize']);p.add_argument('--output',required=True,type=Path);p.add_argument('--prior-requests',type=int,default=0);p.add_argument('--prior-bytes',type=int,default=0);p.add_argument('--seconds',type=int,default=1800);p.add_argument('--trigger',type=int,default=3*1024**3);p.add_argument('--ceiling',type=int,default=4*1024**3);raw=sys.argv[1:];split=raw.index('--') if '--' in raw else len(raw);a=p.parse_args(raw[:split]);argv=raw[split+1:]
    if a.mode=='summarize':summarize(a.output)
    elif a.mode=='provision':provision(a.output)
    elif a.mode=='qualify':qualify(a.output,a.prior_requests,a.prior_bytes)
    else:
        assert 0<a.seconds<=3300 and 0<a.trigger<a.ceiling<=4*1024**3
        assert argv;sys.exit(supervise(argv,a.output,a.seconds,a.trigger,a.ceiling))
