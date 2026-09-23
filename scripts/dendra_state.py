#!/usr/bin/env python3
"""Portable daily state. Public committed product is authoritative; current is prepared.

Snapshots contain the validated public product, checksums, and role/commit receipt.
Native observations, credentials, absolute paths, and transient locks are not durable.
"""
from __future__ import annotations
import argparse,json,os,re,shutil,subprocess,tempfile
from pathlib import Path
import dendra_candidate as dc

VERSION='dendra-portable-state-1'
ARCHIVE_VERSION='dendra-portable-state-2'
def exact_copy(source,destination,binding):
    from dendra_validation import content_binding
    shutil.copytree(source,destination)
    dc.require(content_binding(destination)==binding,'Copied candidate differs from checked bytes')
def inventory(root):
    root=Path(root)
    dc.require(not any(p.is_symlink() for p in root.rglob('*')),'State symlink forbidden')
    files=[p for p in sorted(root.rglob('*')) if p.is_file()]
    dc.require(len(files)<=20000,'Portable inventory file bound')
    return [{'path':p.relative_to(root).as_posix(),'bytes':p.stat().st_size,'sha256':dc.sha(p)} for p in files]
def atomic(path,value):
    path=Path(path);tmp=path.with_suffix('.next.json');dc.write(tmp,value);os.replace(tmp,path)
def candidate_at(state,pointer):
    state=Path(state).resolve();rel=pointer['candidate_relpath'];p=(state/rel).resolve()
    dc.require(p.is_relative_to(state) and rel==f"artifacts/{pointer['generation']}/candidate",'Unsafe portable candidate path')
    return p
def stage(state,candidate,now=None,_operation=None,_adopt=False):
    from dendra_validation import operation,content_binding
    op=operation(_operation)
    state=Path(state).resolve();candidate=Path(candidate);proof=op.validate(candidate,now=now);binding=proof['content_sha256']
    idx=dc.load(candidate/dc.FIXED[0]);dc.require(idx.get('integration_version') in (dc.INTEGRATION,'dendra-integration-2'),'Portable state requires modern integration')
    gen=dc.generation_id(idx['generation'])
    target=state/'artifacts'/gen/'candidate'
    dc.require(target.resolve().is_relative_to(state),'Candidate staging escapes state directory')
    dc.require(not any(p.is_symlink() for p in (state/'artifacts',target.parent,target)),'State artifact symlink forbidden')
    if target.exists():dc.require(content_binding(target)==binding,'Immutable generation collision')
    else:
        target.parent.mkdir(parents=True,exist_ok=True);tmp=target.with_name('candidate.staging')
        dc.require(not tmp.exists(),'Interrupted staging exists; inspect before recovery')
        if _adopt:
            dc.require(_operation is not None,'Adoption requires the trusted producing operation')
            # Only newly produced/private restore candidates use this internal path.
            # Move ownership; do not alias an externally mutable inode into state.
            shutil.move(str(candidate),str(tmp))
            dc.require(content_binding(tmp)==binding,'Adopted candidate differs from checked bytes')
        else:exact_copy(candidate,tmp,binding)
        dc.require(dc.load(tmp/dc.FIXED[0])['generation']==gen,'Staged generation changed during copy')
        op.validate(tmp,now=now);os.replace(tmp,target)
    dc.require(content_binding(target)==binding,'State changed before installation')
    dc.require(dc.load(target/dc.FIXED[0])['generation']==gen,'State artifact generation/path mismatch')
    op.validate(target,now=now)
    return target

def hydrate(state,candidate,role,commit=None,_operation=None,_adopt=False):
    from dendra_validation import operation,content_binding
    op=operation(_operation);proof=op.validate(candidate)
    state=Path(state).resolve();target=stage(state,candidate,_operation=op,_adopt=_adopt);idx=dc.load(target/dc.FIXED[0]);gen=idx['generation']
    dc.require(content_binding(target)==proof['content_sha256'] and target.parent.name==gen,'Hydration changed the checked generation')
    dc.require(re.fullmatch(r'[0-9]{8}T[0-9]{6}-[0-9]+',gen),'Unsafe generation')
    if idx['integration_version']=='dendra-integration-2':
        dc.require(role in ('seed','prepared','published'),'Archive state role')
        pointer={'integration_version':idx['integration_version'],'generation':gen,'policy_version':idx['policy_version'],
                 'stream_ids':idx['selection_ids'],'index_sha256':dc.sha(target/dc.FIXED[0]),
                 'candidate_relpath':target.relative_to(state).as_posix(),'state_role':role}
        if role=='seed':
            dc.require(idx['mode']=='replay' and idx['lineage']['kind']=='imported_seed' and idx['parent_generation'] is None,'Only explicit saved seed may bootstrap')
            dc.require(not (state/'seed.json').exists() and not (state/'published.json').exists(),'Seed already initialized')
            atomic(state/'seed.json',pointer)
        if role=='published':
            dc.require(idx['mode']=='live' and idx.get('publication_time_utc') and re.fullmatch('[0-9a-f]{40}',commit or ''),'Acknowledged archive requires committed live receipt')
            pointer['publication_commit']=commit;atomic(state/'published.json',pointer)
        atomic(state/'current.json',pointer)
        return pointer
    daily=state/'generations'/gen;dc.require(not daily.exists(),'Existing generation must not be overwritten')
    daily.mkdir(parents=True);shutil.copyfile(target/dc.STATE_INDEX,daily/'index.json')
    (daily/'daily').mkdir()
    for s in idx['streams']:shutil.copyfile(target/s['diagnostics_path'],daily/'daily'/f"{s['datastream_id']}.json")
    pointer={'integration_version':dc.INTEGRATION,'generation':gen,'policy_version':idx['policy_version'],
             'stream_ids':[s['datastream_id'] for s in idx['streams']],'index_sha256':dc.sha(daily/'index.json'),
             'files':inventory(daily),'candidate_relpath':target.relative_to(state).as_posix(),'state_role':role}
    if role=='published':
        dc.require(idx['mode']=='live' and idx.get('publication_time_utc') and re.fullmatch('[0-9a-f]{40}',commit or ''),'Acknowledged state requires a live committed publication receipt')
        pointer['publication_commit']=commit;atomic(state/'published.json',pointer)
    atomic(state/'current.json',pointer)
    return pointer

def export_snapshot(state,destination,role='prepared',_operation=None):
    from dendra_validation import operation
    op=operation(_operation)
    state=Path(state);destination=Path(destination);dc.require(not destination.exists(),'Snapshot destination must be new')
    dc.require(role in ('seed','prepared','published'),'Unknown export role')
    pointer=dc.load(state/({'published':'published.json','seed':'seed.json','prepared':'current.json'}[role]))
    dc.require(role=='prepared' or pointer['state_role']==role,'State role mismatch')
    candidate=candidate_at(state,pointer);proof=op.validate(candidate)
    pointer_index=dc.FIXED[0] if pointer['integration_version']=='dendra-integration-2' else dc.STATE_INDEX
    dc.require(dc.sha(candidate/pointer_index)==pointer['index_sha256'],'Export pointer/index mismatch')
    destination.mkdir(parents=True);exact_copy(candidate,destination/'candidate',proof['content_sha256'])
    op.validate(destination/'candidate')
    manifest={'version':ARCHIVE_VERSION if pointer['integration_version']=='dendra-integration-2' else VERSION,'generation':pointer['generation'],'state_role':role,
              'publication_commit':pointer.get('publication_commit') if role=='published' else None,'files':inventory(destination/'candidate')}
    dc.write(destination/'manifest.json',manifest);return manifest

def restore_snapshot(snapshot,state):
    from dendra_validation import ValidationOperation,content_binding
    op=ValidationOperation()
    snapshot=Path(snapshot);state=Path(state);dc.require(not state.exists(),'Restore destination must be new')
    dc.require(sorted(p.name for p in snapshot.iterdir())==['candidate','manifest.json'],'Snapshot closure mismatch')
    with (snapshot/'manifest.json').open('rb') as handle:raw=handle.read(8000001)
    dc.require(len(raw)<=8000000,'Portable manifest byte bound');manifest=json.loads(raw)
    # Validate caps/paths before inventory hashing or parsing untrusted index bytes.
    try:proof=op.validate(snapshot/'candidate')
    except (ValueError,KeyError,OSError,dc.shared.PublisherError) as exc:raise ValueError('Snapshot integrity failure: '+str(exc)) from exc
    idx=dc.load(snapshot/'candidate'/dc.FIXED[0])
    dc.require(manifest['version']==(ARCHIVE_VERSION if idx.get('integration_version')=='dendra-integration-2' else VERSION) and manifest['files']==inventory(snapshot/'candidate'),'Snapshot integrity failure')
    dc.require(manifest['generation']==dc.load(snapshot/'candidate'/dc.FIXED[0])['generation'],'Snapshot generation mismatch')
    dc.require(manifest['state_role'] in (('seed','prepared','published') if manifest['version']==ARCHIVE_VERSION else ('prepared','published')),'Unknown snapshot role')
    state.parent.mkdir(parents=True,exist_ok=True)
    temp=Path(tempfile.mkdtemp(prefix=state.name+'.restore-',dir=state.parent))
    try:
        pointer=hydrate(temp,snapshot/'candidate',manifest['state_role'],manifest['publication_commit'],_operation=op)
        dc.require(content_binding(candidate_at(temp,pointer))==proof['content_sha256'] and content_binding(snapshot/'candidate')==proof['content_sha256'],'Snapshot candidate changed during restore')
        dc.require((snapshot/'manifest.json').read_bytes()==raw and sorted(p.name for p in snapshot.iterdir())==['candidate','manifest.json'],'Snapshot manifest/closure changed during restore')
        os.replace(temp,state);return pointer
    finally:
        if temp.exists():shutil.rmtree(temp)

def restore_public(repo,state):
    """Extract exact committed paths, never working-tree or unacknowledged prepared bytes."""
    from dendra_validation import ValidationOperation
    op=ValidationOperation()
    repo=Path(repo).resolve()
    def git(*args):return subprocess.check_output(['git','-C',str(repo),*args])
    commit=git('rev-parse','HEAD^{commit}').decode().strip()
    entries=git('ls-tree','-rz','--full-tree',commit,'--','docs/data/dendra').split(b'\0')
    entries=[x for x in entries if x]
    dc.require(0<len(entries)<=20000,'No acknowledged Dendra generation or file bound exceeded')
    blobs=[];seen=set()
    for entry in entries:
        metadata,name=entry.split(b'\t',1);mode,kind,oid=metadata.decode().split();name=name.decode('utf-8')
        dc.require(mode=='100644' and kind=='blob' and re.fullmatch('[0-9a-f]{40}',oid),'Committed product must contain plain data blobs')
        dc.require(name not in seen and not Path(name).is_absolute() and '..' not in Path(name).parts and '\\' not in name,'Unsafe/duplicate committed path')
        seen.add(name);blobs.append((name,oid))
    dc.shared._validate_desired_inventory(sorted(seen),fixed_paths=dc.FIXED,owned_roots=dc.OWNED)
    blobs.sort(key=lambda x:(x[0]!=dc.FIXED[0],x[0]))
    state=Path(state);dc.require(not state.exists(),'Public restore destination must be fresh')
    state.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='dendra-public-',dir=state.parent) as td:
        candidate=Path(td)/'candidate';candidate.mkdir()
        # One Git reader, all objects bound to the captured immutable commit.
        proc=subprocess.Popen(['git','-C',str(repo),'cat-file','--batch'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            archive=False
            for number,(name,oid) in enumerate(blobs):
                if number%256==0:op._check()
                proc.stdin.write((oid+'\n').encode());proc.stdin.flush()
                header=proc.stdout.readline().decode().split()
                dc.require(len(header)==3 and header[:2]==[oid,'blob'],'Committed blob read failed')
                import dendra_archive as da
                limit=256000 if name==dc.FIXED[0] else da.expected_limit(name) if archive else 32000000
                size=int(header[2]);dc.require(0<size<=limit,'Committed blob byte bound')
                p=candidate/name;p.parent.mkdir(parents=True,exist_ok=True)
                data=proc.stdout.read(size);dc.require(len(data)==size and proc.stdout.read(1)==b'\n','Truncated committed blob')
                p.write_bytes(data)
                if name==dc.FIXED[0]:archive=json.loads(data).get('integration_version')=='dendra-integration-2'
            proc.stdin.close();dc.require(proc.wait(timeout=30)==0,'Committed object extraction failed')
        finally:
            if proc.poll() is None:proc.kill();proc.wait()
            proc.stdout.close();proc.stderr.close()
        op.validate(candidate)
        staged_state=Path(td)/'state'
        pointer=hydrate(staged_state,candidate,'published',commit,_operation=op,_adopt=True)
        dc.require(git('rev-parse','HEAD^{commit}').decode().strip()==commit,'Public parent moved during restore; retry exact latest state')
        os.replace(staged_state,state)
        return pointer

def transfer(state,destination,source_sha):
    """Export one candidate byte set and a strictly prepared transfer envelope."""
    from dendra_validation import ValidationOperation
    op=ValidationOperation();state=Path(state);destination=Path(destination)
    dc.require(not destination.exists(),'Transfer destination must be new')
    pointer_bytes=(state/'current.json').read_bytes();pointer=json.loads(pointer_bytes)
    candidate=candidate_at(state,pointer);proof=op.validate(candidate)
    pointer_index=dc.FIXED[0] if pointer['integration_version']=='dendra-integration-2' else dc.STATE_INDEX
    dc.require(dc.sha(candidate/pointer_index)==pointer['index_sha256'],'Transfer pointer/index mismatch')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='dendra-transfer-',dir=destination.parent) as td:
        stage=Path(td)/'transfer';publication=stage/'publication';publication.mkdir(parents=True)
        exact_copy(candidate,publication/'candidate',proof['content_sha256'])
        dc.prepare(publication/'candidate',source_sha,_operation=op)
        dc.require((state/'current.json').read_bytes()==pointer_bytes,'Prepared pointer moved during transfer')
        dc.shared.validate_candidate_metadata(root=publication/'candidate',metadata_path=publication/'candidate-metadata.json',product_id=dc.PRODUCT,allowlist=dc.FIXED,expected_source_sha=source_sha,owned_roots=dc.OWNED)
        envelope={'version':'dendra-prepared-transfer-1','state_role':'prepared','generation':pointer['generation'],
                  'candidate_relpath':'publication/candidate','candidate_content_sha256':proof['content_sha256'],
                  'index_sha256':dc.sha(publication/'candidate'/dc.FIXED[0]),
                  'metadata_sha256':dc.sha(publication/'candidate-metadata.json'),'source_event_sha':source_sha}
        dc.write(stage/'prepared-state.json',envelope)
        validate_transfer(stage,source_sha)
        os.replace(stage,destination)
    return destination

def validate_transfer(transfer_root,source_sha):
    """Byte/role boundary only; publication or restore performs cold R checks."""
    from dendra_validation import content_binding
    root=Path(transfer_root);envelope_path=root/'prepared-state.json'
    dc.require(not envelope_path.is_symlink() and envelope_path.stat().st_size<=4096,'Prepared envelope bound/symlink')
    envelope=dc.load(envelope_path)
    dc.require(set(envelope)=={'version','state_role','generation','candidate_relpath','candidate_content_sha256','index_sha256','metadata_sha256','source_event_sha'},'Prepared envelope fields')
    dc.require(envelope['version']=='dendra-prepared-transfer-1' and envelope['state_role']=='prepared' and envelope['candidate_relpath']=='publication/candidate','Prepared envelope version/role/path')
    candidate=root/'publication/candidate';metadata=root/'publication/candidate-metadata.json'
    outer=inventory(root)
    dc.require(all(i['path'] in ('prepared-state.json','publication/candidate-metadata.json') or i['path'].startswith('publication/candidate/') for i in outer),'Prepared transfer closure')
    dc.require(envelope['source_event_sha']==source_sha,'Prepared transfer source mismatch')
    dc.shared.validate_candidate_metadata(root=candidate,metadata_path=metadata,product_id=dc.PRODUCT,allowlist=dc.FIXED,expected_source_sha=source_sha,owned_roots=dc.OWNED)
    dc.require(envelope['metadata_sha256']==dc.sha(metadata) and envelope['index_sha256']==dc.sha(candidate/dc.FIXED[0]) and envelope['candidate_content_sha256']==content_binding(candidate),'Prepared transfer integrity')
    dc.require(envelope['generation']==dc.load(candidate/dc.FIXED[0])['generation'],'Prepared transfer generation')
    return envelope

def restore_transfer(transfer_root,state,source_sha):
    from dendra_validation import ValidationOperation,content_binding
    root=Path(transfer_root);state=Path(state);dc.require(not state.exists(),'Prepared restore destination must be fresh')
    envelope=validate_transfer(root,source_sha);candidate=root/'publication/candidate';op=ValidationOperation()
    proof=op.validate(candidate)
    dc.require(proof['content_sha256']==envelope['candidate_content_sha256'],'Prepared restore bytes changed')
    state.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='dendra-prepared-',dir=state.parent) as td:
        target=Path(td)/'state';pointer=hydrate(target,candidate,'prepared',_operation=op)
        dc.require(content_binding(candidate_at(target,pointer))==proof['content_sha256'],'Prepared candidate changed during restore')
        dc.require(content_binding(candidate)==envelope['candidate_content_sha256'] and validate_transfer(root,source_sha)==envelope,'Prepared envelope changed during restore')
        os.replace(target,state)
    return pointer

def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('stage');p.add_argument('--state',required=True);p.add_argument('--candidate',required=True)
    p=sub.add_parser('archive-prepare');p.add_argument('--state',required=True);p.add_argument('--candidate',required=True);p.add_argument('--role',choices=['seed','prepared'],required=True)
    p=sub.add_parser('export');p.add_argument('--state',required=True);p.add_argument('--destination',required=True);p.add_argument('--role',choices=['seed','prepared','published'],default='prepared')
    p=sub.add_parser('restore');p.add_argument('--snapshot',required=True);p.add_argument('--state',required=True)
    p=sub.add_parser('restore-public');p.add_argument('--repo',required=True);p.add_argument('--state',required=True)
    p=sub.add_parser('transfer');p.add_argument('--state',required=True);p.add_argument('--destination',required=True);p.add_argument('--source-sha',required=True)
    p=sub.add_parser('restore-transfer');p.add_argument('--transfer',required=True);p.add_argument('--state',required=True);p.add_argument('--source-sha',required=True)
    a=parser.parse_args()
    if a.command=='stage':stage(a.state,a.candidate)
    elif a.command=='archive-prepare':
        dc.require(dc.load(Path(a.candidate)/dc.FIXED[0]).get('integration_version')=='dendra-integration-2','Archive prepare version')
        hydrate(a.state,a.candidate,a.role)
    elif a.command=='export':export_snapshot(a.state,a.destination,a.role)
    elif a.command=='restore':restore_snapshot(a.snapshot,a.state)
    elif a.command=='transfer':transfer(a.state,a.destination,a.source_sha)
    elif a.command=='restore-transfer':restore_transfer(a.transfer,a.state,a.source_sha)
    else:restore_public(a.repo,a.state)
    print(json.dumps({'status':'passed','operation':a.command}))
if __name__=='__main__':
    try:main()
    except (ValueError,KeyError,OSError,subprocess.SubprocessError) as e:raise SystemExit(str(e))
