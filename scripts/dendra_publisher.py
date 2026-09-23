#!/usr/bin/env python3
"""Dendra callbacks for the unchanged shared main publisher; no Git operations here."""
import json,os,shutil,sys,tempfile
from pathlib import Path
from datetime import datetime,timezone
import dendra_candidate as dc


def validate_product(root,activation=False):
    root=Path(root)
    # Validate only this product in an existing multi-product publication tree.
    with tempfile.TemporaryDirectory(prefix='dendra-validation-') as td:
        clean=Path(td);source=root/'docs/data/dendra'
        dc.require(source.is_dir() and not source.is_symlink(),'Missing/unsafe Dendra tree')
        dc.require(not any(p.is_symlink() for p in source.rglob('*')),'Dendra symlink forbidden')
        # Read-only temporary view: hard links avoid duplicating the full archive.
        # Fall back to a normal copy across filesystems. Never mutate this view.
        def link_or_copy(src,dst):
            try:return os.link(src,dst)
            except OSError:return shutil.copyfile(src,dst)
        shutil.copytree(source,clean/'docs/data/dendra',copy_function=link_or_copy)
        dc.validate(clean)
    index=dc.load(root/dc.FIXED[0])
    dc.require(index.get('integration_version') in (dc.INTEGRATION,'dendra-integration-2') and index.get('safeguard_version')==dc.VALIDATOR_VERSION,'Public activation requires modern integration safeguards')
    dc.require(index['mode']=='live','Frozen/replay data cannot be activated')
    if activation:dc.require(dc.freshness(index)=='current','Stale prepared candidate cannot activate')
    return index


def callback():
    dc.require(os.environ['BRIM_PUBLISH_PRODUCT_ID']==dc.PRODUCT,'Wrong callback product')
    dc.require(json.loads(os.environ['BRIM_PUBLISH_FIXED_PATHS'])==dc.FIXED and json.loads(os.environ['BRIM_PUBLISH_OWNED_ROOTS'])==dc.OWNED,'Ownership declaration mismatch')
    phase=os.environ['BRIM_PUBLISH_PHASE'];candidate=Path(os.environ['BRIM_PUBLISH_CANDIDATE_ROOT']);tree=Path(os.environ['BRIM_PUBLISH_WORKTREE'])
    if phase=='validate-candidate':
        idx=validate_product(candidate)
        metadata=dc.load(os.environ['BRIM_PUBLISH_METADATA'])
        dc.require(metadata['semantic_key']=={'type':'dendra_state_generation','value':idx['generation']},'Semantic generation binding mismatch')
    elif phase=='reconcile':
        idx=validate_product(candidate)
        current=validate_product(tree) if (tree/dc.FIXED[0]).exists() else None
        if current is None:dc.require(not (tree/'docs/data/dendra').exists(),'Partial public state cannot bootstrap')
        decision=dc.reconcile_decision(idx,current)
        if decision['decision']=='publish':
            dc.require(idx['parent_generation'] is None or current is not None,'Missing acknowledged parent')
            # idx was fully validated above in this same callback. Reevaluate
            # wall-clock activation here; staged bytes receive a fresh full check.
            dc.require(dc.freshness(idx)=='current','Stale prepared candidate cannot activate')
            # Owned roots contain only the selected complete product; shared publisher
            # independently verifies every removal/addition and staged semantic content.
            for rel in dc.OWNED:
                target=tree/rel
                if target.exists():shutil.rmtree(target)
                if (candidate/rel).exists():shutil.copytree(candidate/rel,target)
                else:
                    # The shared publisher stages every declared owned root.
                    # Git accepts an existing empty directory, but a never-present
                    # optional root is an unmatched pathspec on first publication.
                    target.mkdir(parents=True,exist_ok=True)
            for rel in dc.FIXED:
                (tree/rel).parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(candidate/rel,tree/rel)
            idx['publication_time_utc']=datetime.now(timezone.utc).isoformat().replace('+00:00','Z');dc.write(tree/dc.FIXED[0],idx)
        dc.write(os.environ['BRIM_PUBLISH_RESULT'],decision)
    elif phase=='validate-staged':
        staged=validate_product(tree,activation=True);original=dc.load(candidate/dc.FIXED[0])
        dc.require({k:v for k,v in staged.items() if k!='publication_time_utc'}=={k:v for k,v in original.items() if k!='publication_time_utc'},'Staged candidate diverged')
        dc.require(staged.get('publication_time_utc') is not None,'Missing publication timestamp')
    else:raise ValueError('Unknown callback phase')
    print('Dendra callback passed: '+phase)
if __name__=='__main__':
    try:callback()
    except (ValueError,KeyError,OSError) as e:print('DENDRA_PUBLISHER_REJECTED: '+str(e),file=sys.stderr);sys.exit(1)
