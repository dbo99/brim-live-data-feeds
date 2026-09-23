"""Reuse actual shared-publisher race/failure regressions under SM2B-only authority."""
import argparse
import archive_git_transactions as scenarios
from full_pipeline_transactions import Boundary
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--root',required=True);a=p.parse_args()
 scenarios.LocalGit=lambda root:Boundary(root,create=True)
 scenarios.run(a.root)
