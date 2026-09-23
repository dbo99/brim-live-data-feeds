"""Real R, immutable-copy and after-check mutation tests; saved inputs only."""
import argparse,copy,hashlib,json,shutil,sys
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'scripts'))
import dendra_candidate as dc
import dendra_archive as da
import dendra_state as ds
import dendra_validation as dv

def run(workspace,root):
    w=Path(workspace).resolve();root=Path(root).resolve();assert not root.exists();root.mkdir(parents=True)
    saved=w/'work/sm2a/state';sp=dc.load(saved/'seed.json');source=ds.candidate_at(saved,sp);original=dv.content_binding(source);checks=[]
    def ok(name,**kw):checks.append({'case':name,'passed':True,**kw});print(name,flush=True)
    def holds(name,fn):
        try:fn()
        except (ValueError,KeyError,OSError,dc.shared.PublisherError) as e:ok(name,rejection=str(e))
        else:raise AssertionError(name+' did not hold')
    op=dv.ValidationOperation();p=op.validate(source);assert p['semantic']['rows_checked']==10623
    assert op.validate(source)['operation_reuse'];ok('actual-R-cold-then-private-reuse',rows=10623)
    view=root/'view';da.materialize(source,view);idx=da.open_index(source);parity=[]
    def typed(x):
        if x is None:return ['null']
        if isinstance(x,bool):return ['boolean',x]
        if isinstance(x,(int,float)):return ['number',x]
        if isinstance(x,str):return ['string',x]
        if isinstance(x,list):return ['array',[typed(v) for v in x]]
        return ['object',[[k,typed(x[k])] for k in sorted(x)]]
    def digest(x):return hashlib.sha256(json.dumps(typed(x),ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    for s in idx['streams']:
        _,before=da.stream_product(source,s);after=dc.load(view/'daily'/(s['datastream_id']+'.json'));assert typed(before)==typed(after)
        parity.append({'stream_id':s['datastream_id'],'rows':len(before['rows']),'eligible':sum(r['plot_eligible'] for r in before['rows']),'whole_product_typed_sha256':digest(before),'daily_rows_typed_sha256':digest(before['rows']),'materialized_file_sha256':dc.sha(view/'daily'/(s['datastream_id']+'.json'))})
    ok('all-real-product-fields-and-10623-rows-through-materialization-exact',products=parity)
    # Full re-layouts rebind descriptors: the actual R validator must reject the
    # contradiction, rather than relying only on a stale file checksum.
    sid=idx['streams'][0]['datastream_id'];product=dc.load(view/'daily'/(sid+'.json'))
    for name,mutate in [
        ('diagnostic-mean',lambda p:p['rows'][next(i for i,r in enumerate(p['rows']) if r['plot_eligible'])].__setitem__('mean_value',999)),
        ('daily-flags',lambda p:p['rows'][0].__setitem__('flags',['unrecognized-flag'])),
        ('identity-unit',lambda p:p['stream']['unit_normalization'].__setitem__('multiplier',999))]:
        gen=root/name/'generation';shutil.copytree(view,gen);p=copy.deepcopy(product);mutate(p);dc.write(gen/'daily'/(sid+'.json'),p)
        candidate=root/name/'candidate';candidate.mkdir();da.write_layout(gen,candidate)
        holds('after-check-rebound-'+name,lambda:op.validate(candidate))
    # Mutation of actual same-path bytes, an old closed partition, extra paths,
    # and a claimed validation receipt all receive a fresh check or explicit hold.
    bad=root/'same-path'/'candidate';shutil.copytree(source,bad);op.validate(bad)
    file=next(p for p in (bad/'docs/data/dendra/history').rglob('*.json') if int(p.name[:4])<2026);file.write_bytes(file.read_bytes()+b' ')
    holds('same-path-old-partition-corruption-after-check',lambda:op.validate(bad))
    foreign=root/'scope'/'candidate';shutil.copytree(source,foreign);dc.write(foreign/'outside.json',{})
    holds('extra-owned-closure-rejects',lambda:op.validate(foreign))
    forged=root/'forged'/'candidate';shutil.copytree(source,forged);i=dc.load(forged/dc.FIXED[0]);i['validation_receipt']={'status':'passed','content_sha256':original};dc.write(forged/dc.FIXED[0],i)
    assert op.validate(forged)['operation_reuse'] is False;ok('candidate-claimed-validation-never-skips-real-R')
    with patch.object(dv,'source_binding',return_value=('different-source',)):
        holds('changed-validator-code-held',lambda:op.validate(source))
    with patch.object(dc,'SEMANTIC_TOTAL_SECONDS',0):
        holds('real-dispatch-deadline-holds-cold-state',lambda:ds.hydrate(root/'deadline-state',source,'seed'))
    assert not (root/'deadline-state/current.json').exists()
    # A different but scientifically valid generation copied after the first
    # check must not become an acknowledged/prepared substitute.
    real_copy=ds.shutil.copytree
    def changed_copy(src,dst,*a,**kw):
        result=real_copy(src,dst,*a,**kw)
        index=Path(dst)/dc.FIXED[0]
        if index.exists():
            i=dc.load(index);i['scope']='SYNTHETIC after-check replacement';i['generation']='20260921T235012-999999';i['lineage']['seed_generation']=i['generation'];dc.write(index,i)
        return result
    with patch.object(ds.shutil,'copytree',side_effect=changed_copy):
        holds('valid-different-copy-after-check-held',lambda:ds.hydrate(root/'copy-state',source,'seed'))
    assert 'differs from checked bytes' in checks[-1]['rejection']
    assert not (root/'copy-state/current.json').exists()
    restored=root/'isolated-state';ds.hydrate(restored,source,'seed');snapshot=root/'snapshot';ds.export_snapshot(restored,snapshot,'seed')
    real_hydrate=ds.hydrate
    def substituted_hydrate(state,candidate,*a,**kw):
        path=Path(candidate)/dc.FIXED[0];before=path.read_bytes();changed=json.loads(before)
        changed['generation']='20260921T235012-888888';changed['lineage']['seed_generation']=changed['generation'];dc.write(path,changed)
        try:return real_hydrate(state,candidate,*a,**kw)
        finally:path.write_bytes(before)
    with patch.object(ds,'hydrate',side_effect=substituted_hydrate):
        holds('portable-valid-generation-substitution-after-manifest-check',lambda:ds.restore_snapshot(snapshot,root/'substituted-snapshot-state'))
    assert 'candidate changed during restore' in checks[-1]['rejection'] and not (root/'substituted-snapshot-state').exists()
    manifest_path=snapshot/'manifest.json';manifest_bytes=manifest_path.read_bytes()
    def changed_manifest(state,candidate,*a,**kw):
        pointer=real_hydrate(state,candidate,*a,**kw);manifest_path.write_bytes(manifest_bytes+b' ');return pointer
    with patch.object(ds,'hydrate',side_effect=changed_manifest):
        holds('portable-manifest-mutation-before-exposure',lambda:ds.restore_snapshot(snapshot,root/'changed-manifest-state'))
    assert not (root/'changed-manifest-state').exists();manifest_path.write_bytes(manifest_bytes)
    copied=snapshot/'candidate'/dc.FIXED[0];copied.write_bytes(copied.read_bytes()+b' ')
    assert dv.content_binding(source)==original and dv.content_binding(ds.candidate_at(restored,dc.load(restored/'seed.json')))==original
    ok('export-and-restored-state-have-no-writable-alias-to-prior')
    transfer=root/'transfer';ds.transfer(restored,transfer,'a'*40)
    dc.shared.validate_candidate_metadata(root=transfer/'publication/candidate',metadata_path=transfer/'publication/candidate-metadata.json',product_id=dc.PRODUCT,allowlist=dc.FIXED,owned_roots=dc.OWNED,expected_source_sha='a'*40)
    assert dv.content_binding(transfer/'publication/candidate')==original
    envelope=ds.validate_transfer(transfer,'a'*40);assert envelope['candidate_content_sha256']==original
    prepared=root/'restored-transfer';ds.restore_transfer(transfer,prepared,'a'*40);assert not (prepared/'seed.json').exists() and not (prepared/'published.json').exists()
    assert dc.load(prepared/'current.json')['state_role']=='prepared'
    holds('wrong-expected-source-held',lambda:ds.validate_transfer(transfer,'b'*40))
    for field,value in [('state_role','published'),('candidate_relpath','../candidate'),('generation','20240101T000000-1'),('metadata_sha256','0'*64)]:
        bad_envelope=dict(envelope);bad_envelope[field]=value;dc.write(transfer/'prepared-state.json',bad_envelope)
        holds('prepared-envelope-'+field,lambda:ds.restore_transfer(transfer,root/('rejected-'+field),'a'*40))
        assert not (root/('rejected-'+field)).exists()
    dc.write(transfer/'prepared-state.json',envelope)
    ok('actual-template-transfer-export-exact-publication-closure')
    assert dv.content_binding(source)==original
    report={'status':'passed','provider_requests':0,'checks':checks,'real_row_count':10623,'eligible':10093};dc.write(root/'report.json',report);return report
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--workspace',required=True);p.add_argument('--root',required=True);a=p.parse_args();run(a.workspace,a.root)
