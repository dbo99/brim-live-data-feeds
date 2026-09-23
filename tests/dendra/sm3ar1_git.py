#!/usr/bin/env python3
"""SM3AR1 explicit disposable file-Git exception; actual source-owned callbacks."""
import argparse,importlib.util,json,os,shutil,subprocess,sys,tempfile
from pathlib import Path
from datetime import datetime,timedelta,timezone
from archive_fixture import ROOT,dc
from coverage_fixture import CoverageFixture,WorkflowBoundary
import dendra_validation as dv
import dendra_state as ds
import dendra_archive as da
import soil_network_workflow as workflow
import soil_moisture_operations as ops
import scan_soil_moisture_publisher as scan
class LocalGit:
    def __init__(self,root):
        self.root=Path(root).resolve()
        # Scope cannot be widened merely by supplying an arbitrary --root.
        expected=ROOT.parent/'sm3ar1/git-fixtures'
        dc.require(self.root.is_relative_to(expected.resolve()),'Fixture root must be beneath work/sm3ar1/git-fixtures')
        dc.require(not self.root.exists(),'Use a new disposable fixture directory')
        self.root.mkdir(parents=True);(self.root/'tmp').mkdir();(self.root/'no-hooks').mkdir()
        env={k:v for k,v in os.environ.items() if not k.startswith('GIT_') and k not in ('BRIM_LIVE_MAIN_PUBLISH','GITHUB_SHA','GITHUB_REF','GITHUB_REF_TYPE','GITHUB_ACTIONS')}
        env.update(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull,GIT_ALLOW_PROTOCOL='file',GIT_TERMINAL_PROMPT='0',PYTHONDONTWRITEBYTECODE='1',TMPDIR=str(self.root/'tmp'))
        config={'core.hooksPath':str(self.root/'no-hooks'),'credential.helper':'','protocol.allow':'never','protocol.file.allow':'always','user.name':'Dendra synthetic fixture','user.email':'dendra-fixture@example.invalid','commit.gpgSign':'false','tag.gpgSign':'false'}
        env['GIT_CONFIG_COUNT']=str(len(config))
        for i,(k,v) in enumerate(config.items()):env[f'GIT_CONFIG_KEY_{i}']=k;env[f'GIT_CONFIG_VALUE_{i}']=v
        self.env=env;self.commands=[]
    def path(self,p):
        p=Path(p).absolute();dc.require(all(not q.is_symlink() for q in (p,*p.parents)),'Symlink in disposable Git path');p=p.resolve();dc.require(p.is_relative_to(self.root),'Git path escapes fixture boundary');return p
    def remote(self,value):
        dc.require('://' not in value or value.startswith('file:///'),'Only a local absolute/file remote is allowed')
        if value.startswith('file://'):value=value[7:]
        dc.require(Path(value).is_absolute(),'Relative/SSH remote forbidden');return self.path(value)
    def guard(self,args,cwd):
        self.path(cwd)
        if args[0]!='git':return
        if args[1]=='init':self.path(args[-1])
        if args[1]=='remote' and args[2]=='add':self.remote(args[-1])
        if args[1] in ('push','fetch'):
            remote_name=next(a for a in args[2:] if not str(a).startswith('-'))
            remote=subprocess.check_output(['git','remote','get-url',remote_name],cwd=cwd,env=self.env,text=True).strip();self.remote(remote)
            # pushurl could differ; reject it independently before a real push.
            push=subprocess.check_output(['git','remote','get-url','--push',remote_name],cwd=cwd,env=self.env,text=True).strip();self.remote(push)
        if args[1]=='clone':self.remote(args[-2]);self.path(args[-1])
        if args[1]=='worktree' and args[2]=='add':self.path(args[-2])
        if args[1]=='worktree' and args[2]=='remove':self.path(args[-1])
    def run(self,*args,cwd=None):
        cwd=self.path(cwd or self.root);cmd=['git',*map(str,args)];self.guard(cmd,cwd)
        r=subprocess.run(cmd,cwd=cwd,env=self.env,capture_output=True,text=True);self.commands.append({'args':cmd,'cwd':str(cwd.relative_to(self.root)),'exit':r.returncode})
        if r.returncode:raise RuntimeError(r.stderr)
        return r.stdout.strip()
