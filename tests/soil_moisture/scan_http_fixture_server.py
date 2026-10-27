"""Finite synthetic HTTP fixture; no provider client or external listener."""
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import gzip, json, time, urllib.parse

class FixtureServer(HTTPServer):
    def __init__(self, root, port=0):
        self.root=Path(root); self.records=[]; self.counts={}; self.body_bytes=0; self.failure=None; self.request_limit=256; self.byte_limit=64*1024**2
        super().__init__(("127.0.0.1", port), Handler)
    def verify_request(self, request, address):
        return self.failure is None and len(self.records)<self.request_limit
    def handle_error(self, request, address):
        self.failure="fixture handler failed"
        (self.root/"SERVER_FAILURE.json").write_text(json.dumps({"error":self.failure}))

class Handler(BaseHTTPRequestHandler):
    protocol_version="HTTP/1.1"
    def log_message(self,*args): pass
    def do_GET(self): self.fixture()
    def do_POST(self): self.fixture()
    def fixture(self):
        s=self.server; self.connection.settimeout(2)
        record=dict(number=len(s.records)+1,path=urllib.parse.urlsplit(self.path).path,method=self.command,request_body_bytes=0,reserved_decoded_response_bytes=0,status=None,synthetic_only=True,complete=False)
        s.records.append(record)
        with (s.root/'SERVER.jsonl').open('a') as f:f.write(json.dumps(dict(event='attempt',**record))+'\n')
        assert s.failure is None and len(s.records)<=s.request_limit
        length=int(self.headers.get("Content-Length","0")); assert 0<=length<=8192
        assert s.body_bytes+length<=s.byte_limit
        s.body_bytes+=length;record['request_body_bytes']=length
        request=self.rfile.read(length); assert len(request)==length
        form=urllib.parse.parse_qs(request.decode(),keep_blank_values=True)
        path=urllib.parse.urlsplit(self.path).path
        s.counts[path]=s.counts.get(path,0)+1
        status=200; headers={}; unknown=False; truncated=False; invalid=False; delay=0
        if path.startswith('/builder/'):
            _,_,mode,route=path.split('/')
            assert mode in ['success','transient_first','good_peer','fatal_preflight','source_scope','oversize','budget','canary_empty','main_empty','no_current','few_stations']
            if route=='preflight':
                assert self.command=='GET' and not form; body=b'SYNTHETIC endpoint'
                if mode=='transient_first' and s.counts[path]==1: status=503
                if mode=='fatal_preflight': status=403
            else:
                assert route=='report' and self.command=='POST'
                site=check_form(form); absent=mode=='canary_empty' or mode=='main_empty' and s.counts[path]>1 or mode in ['few_stations','good_peer'] and form['sitenum']==urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)['last']
                values='-99.9,-99.9,-99.9' if absent else '0,20,40'
                dates=['2026-09-28','2026-09-29'] if mode=='no_current' else ['2026-10-01','2026-10-25']
                body=report(site, dates, values)
        else:
            assert self.command=='POST'; check_form(form)
            assert path in ['/exact','/unknown-exact','/oversize','/unknown','/gzip','/truncated','/invalid-framing','/timeout','/redirect','/error','/report','/refusal','/retry-after']
            body=report('2218',['2026-10-01'],'0,20,40') if path=='/report' else b'x'*(512 if path=='/error' else 1024 if path in ['/exact','/unknown-exact'] else 8192)
            if path=='/timeout': delay=.7
            if path=='/redirect': status=307; headers['Location']=f'http://127.0.0.1:{s.server_port}/exact'
            if path=='/error': status=503
            if path=='/refusal': status=429
            if path=='/retry-after': headers['Retry-After']='600'
            unknown=path in ['/unknown','/unknown-exact'];truncated=path=='/truncated';invalid=path=='/invalid-framing'
        assert len(body)<=8*1024**2 and s.body_bytes+len(body)<=s.byte_limit
        s.body_bytes+=len(body)
        record.update(reserved_decoded_response_bytes=len(body),status=status,complete=True)
        with (s.root/'SERVER.jsonl').open('a') as f:f.write(json.dumps(dict(event='response_reserved',**record))+'\n')
        try:
            if delay:time.sleep(delay)
            if invalid:
                self.wfile.write(b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\nnot-hex\r\nabc\r\n');self.close_connection=True;return
            self.send_response(status);self.send_header('Connection','close')
            for key,value in headers.items():self.send_header(key,value)
            if path=='/gzip':body=gzip.compress(body);self.send_header('Content-Encoding','gzip')
            if not unknown:self.send_header('Content-Length',str(len(body)+(100 if truncated else 0)))
            self.end_headers();self.wfile.write(body);self.wfile.flush()
        except (BrokenPipeError,ConnectionResetError):pass
        finally:self.close_connection=True

def check_form(form):
    expected={'intervalType':[' View Historic '],'report':['SMS'],'timeseries':['Daily'],'format':['copy'],'interval':['YEAR'],'year':['2026'],'month':['CY']}
    assert set(form)==set(expected)|{'sitenum'} and all(form[k]==v for k,v in expected.items())
    assert len(form['sitenum'])==1 and form['sitenum'][0].isdigit()
    return form['sitenum'][0]

def report(site, dates, values):
    return ('\n'.join(['SYNTHETIC SCAN daily fixture - not observations','Site Id,Date,Time,SMS.I-1:-2 (pct),SMS.I-1:-8 (pct),SMS.I-2:-8 (pct),',*[f'{site},{d},,{values},' for d in dates],f'{site},2026-10-24,,-99.9,NA,NaN,'])).encode()
