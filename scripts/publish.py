"""Fast-forward GitHub's production branch to the exact local commit.

The push goes over SSH with the server's warm-up deploy key: the checkout's push URL is PUSH_URL and its
core.sshCommand selects the key. Fetches stay on anonymous HTTPS, so the daily run does not need the key.
"""
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
BRANCH='production'
PUSH_URL='git@github.com:ara-research-ai/warm-up.git'


def git(*args):
    return subprocess.check_output(['git',*args],cwd=ROOT,text=True).strip()


def publish():
    if git('status','--porcelain'):
        raise RuntimeError('Uncommitted changes prevent publication.')
    if git('branch','--show-current')!=BRANCH:
        raise RuntimeError('Expected production branch.')
    if git('config','user.name')!='Aravind' or git('config','user.email')!='this.aravind@gmail.com':
        raise RuntimeError('Git identity does not match Aravind.')
    subprocess.run(['git','fetch','--prune','origin'],cwd=ROOT,check=True)
    head=git('rev-parse','HEAD')
    remote=git('rev-parse','origin/'+BRANCH)
    if remote==head:
        return head
    if git('rev-list','--parents','-n','1','HEAD').split()[1:]!=[remote]:
        raise RuntimeError('Remote branch changed or multiple unpublished commits exist. Preserve work and reconcile before publishing.')
    for line in git('ls-tree','-r','HEAD').splitlines():
        meta,path=line.split('\t',1); mode,kind,sha=meta.split()
        if path.split('/')[0] in {'data','docs','.wrangler','node_modules'} or path.startswith(('.env','.dev.vars')):
            raise RuntimeError('Private state cannot be published to the code repository.')
        if kind!='blob':
            raise RuntimeError('Unexpected Git entry')
    if git('show','-s','--format=%an|%ae|%cn|%ce','HEAD')!='Aravind|this.aravind@gmail.com|Aravind|this.aravind@gmail.com':
        raise RuntimeError('Commit attribution does not match Aravind.')
    if git('config','--default','','--get','remote.origin.pushurl')!=PUSH_URL:
        raise RuntimeError('The push URL is not the deploy-key SSH remote '+PUSH_URL+'.')
    # A plain push, never forced: GitHub accepts it only as a fast-forward of the commit checked above.
    subprocess.run(['git','push','origin',head+':refs/heads/'+BRANCH],cwd=ROOT,check=True)
    subprocess.run(['git','fetch','--prune','origin'],cwd=ROOT,check=True)
    if git('rev-parse','origin/'+BRANCH)!=head:
        raise RuntimeError('Remote publication read-back failed.')
    return head


if __name__=='__main__':
    print(json.dumps({'commit':publish(),'url':'https://github.com/ara-research-ai/warm-up'}))
