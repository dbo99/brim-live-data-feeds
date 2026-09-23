"""TEST ONLY: explicit synthetic science clock; resource clocks remain real."""
import argparse,importlib,sys
from datetime import datetime,timezone
from pathlib import Path

def install(value):
    reference=datetime.fromisoformat(value.replace('Z','+00:00'))
    class SyntheticClock(datetime):
        @classmethod
        def now(cls,tz=None):return reference.astimezone(tz) if tz else reference.replace(tzinfo=None)
    for name in ('dendra_candidate','dendra_validation','dendra_publisher'):
        module=importlib.import_module(name);module.datetime=SyntheticClock
    return reference

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--clock',required=True);p.add_argument('--script',required=True);p.add_argument('arguments',nargs=argparse.REMAINDER);a=p.parse_args()
    script=Path(a.script).resolve();assert script.name in ('dendra_candidate.py','dendra_state.py','dendra_archive.py','dendra_publisher.py')
    sys.path.insert(0,str(script.parent));install(a.clock);sys.argv=[str(script),*a.arguments]
    module=importlib.import_module(script.stem)
    if script.stem=='dendra_publisher':module.callback()
    elif script.stem=='dendra_archive':
        q=argparse.ArgumentParser();q.add_argument('command',choices=['materialize','validate']);q.add_argument('--root',required=True);q.add_argument('--output');b=q.parse_args()
        module.materialize(b.root,b.output) if b.command=='materialize' else print(module.validate(b.root))
    else:module.main()
