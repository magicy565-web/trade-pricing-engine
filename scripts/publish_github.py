#!/usr/bin/env python3
"""Create a PRIVATE standalone repository and push without overwriting remote work.
Requires Git and an authenticated GitHub CLI on the user's machine.
Does not accept a token argument, print credentials or change global Git settings.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(args, check=True):
    p = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, timeout=180)
    if check and p.returncode:
        raise RuntimeError(f"Command failed: {args[0]} {args[1]}\n{p.stderr.strip()}")
    return p


def sensitive(path: str) -> bool:
    parts=Path(path).parts
    name=Path(path).name
    return any(p in {"private", "data", ".venv", "node_modules"} for p in parts) or name.endswith((".token", ".sqlite", ".sqlite-wal", ".sqlite-shm", ".db", ".pem", ".key")) or name==".env" or (name.startswith(".env.") and name!=".env.example")


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--owner',default='magicy565-web')
    ap.add_argument('--name',default='trade-pricing-engine')
    ap.add_argument('--dry-run',action='store_true',help='Print offline plan; no network or changes')
    ap.add_argument('--resume-empty',action='store_true',help='Explicitly allow an existing PRIVATE empty repository')
    args=ap.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}',args.owner) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',args.name):
        raise ValueError('Invalid repository owner/name')
    full=f'{args.owner}/{args.name}'
    if args.dry_run:
        print(json.dumps({'mode':'DRY_RUN_NO_CHANGES','repository':full,'visibility':'private',
                          'checks':['authenticated account equals owner','no tracked private files','clean working tree',
                                    'no conflicting origin','absent repository or explicitly allowed empty private repository',
                                    'no force-push','remote main SHA must equal local HEAD']},indent=2))
        return
    for executable in ['git','gh']:
        if not shutil.which(executable):raise RuntimeError(f'{executable} is not installed. Install Git and GitHub CLI first.')
    profile=json.loads(run(['gh','api','user']).stdout)
    if profile.get('login','').lower()!=args.owner.lower():
        raise RuntimeError('Authenticated GitHub account does not match --owner. No repository created.')
    if not (ROOT/'.git').exists():
        run(['git','init','-b','main'])
        # Repository-local identity only; no changes to global Git configuration.
        if run(['git','config','user.name'],False).returncode:
            run(['git','config','user.name',profile['login']])
        if run(['git','config','user.email'],False).returncode:
            run(['git','config','user.email',f"{profile['id']}+{profile['login']}@users.noreply.github.com"])
        run(['git','add','.'])
        staged=run(['git','diff','--cached','--name-only']).stdout.splitlines()
        if any(sensitive(p) for p in staged):raise RuntimeError('Sensitive local file staged; stopped before commit or upload.')
        run(['git','commit','-m','Initial release: evidence-backed Trade Pricing Engine v0.1.0'])
    branch=run(['git','branch','--show-current']).stdout.strip()
    if branch!='main':raise RuntimeError('Expected main branch; no branches will be renamed automatically.')
    if run(['git','status','--porcelain']).stdout.strip():
        raise RuntimeError('Working tree is not clean. Review and commit changes before publishing.')
    if any(sensitive(p) for p in run(['git','ls-files']).stdout.splitlines()):
        raise RuntimeError('Tracked private credential/database detected; refusing to publish.')
    head=run(['git','rev-parse','HEAD']).stdout.strip()
    expected=f'https://github.com/{full}.git'
    origin=run(['git','remote','get-url','origin'],False)
    if origin.returncode==0 and origin.stdout.strip() not in [expected,f'git@github.com:{full}.git']:
        raise RuntimeError('A different origin already exists. It will not be replaced.')
    probe=run(['gh','api',f'repos/{full}'],False)
    if probe.returncode==0:
        repo=json.loads(probe.stdout)
        if not repo.get('private'):raise RuntimeError('Existing repository is not private; refusing to alter it.')
        remote_head=run(['gh','api',f'repos/{full}/git/ref/heads/main','--jq','.object.sha'],False)
        if remote_head.returncode==0:
            if remote_head.stdout.strip()==head:
                print(json.dumps({'status':'already_published','repository':full,'sha':head,'url':repo['html_url']}));return
            raise RuntimeError('Repository already has content. No force-push or overwrite will be attempted.')
        if not args.resume_empty or repo.get('size',0)!=0:
            raise RuntimeError('Repository exists. Inspect it, then use --resume-empty only when it is empty.')
        # Verify it is genuinely empty, not merely missing main.
        refs=run(['gh','api',f'repos/{full}/git/matching-refs/heads'],False)
        if refs.returncode==0 and json.loads(refs.stdout):
            raise RuntimeError('Repository has another branch; refusing to upload.')
        if refs.returncode!=0 and '409' not in refs.stderr:
            raise RuntimeError('Could not verify that the repository has no branches.')
    else:
        if '404' not in probe.stderr:
            raise RuntimeError('Cannot verify repository absence; stopped without creating anything.')
        run(['gh','repo','create',full,'--private','--description','Evidence-backed export pricing, contribution margins, human approval, and MCP'])
    if origin.returncode:
        run(['git','remote','add','origin',expected])
    # Use gh's credential broker for this command only. Never place a token in a URL.
    run(['git','-c','credential.helper=','-c','credential.helper=!gh auth git-credential','push','-u','origin','main'])
    remote=run(['gh','api',f'repos/{full}/git/ref/heads/main','--jq','.object.sha']).stdout.strip()
    if remote!=head:raise RuntimeError('Remote verification failed: main SHA differs from local HEAD.')
    print(json.dumps({'status':'published_and_verified','repository':full,'visibility':'private','sha':head,
                      'url':f'https://github.com/{full}'},indent=2))


if __name__=='__main__':
    try:main()
    except (ValueError,RuntimeError,OSError,subprocess.SubprocessError) as exc:
        print(f'PUBLISH STOPPED: {exc}',file=sys.stderr);sys.exit(1)
