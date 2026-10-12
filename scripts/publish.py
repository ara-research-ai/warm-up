"""Fast-forward the exact local Git commit through authenticated Composio."""
from datetime import datetime
import json
from pathlib import Path
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
API='https://api.github.com/repos/ara-research-ai/warm-up'
BRANCH='production'


def git(*args):
    return subprocess.check_output(['git',*args],cwd=ROOT,text=True).strip()


def api(path,method='GET',body=None):
    command=['composio','proxy',API+path,'--toolkit','github','--method',method]
    with tempfile.TemporaryDirectory(prefix='outreach-api-') as tmp:
        if body is not None:
            payload=Path(tmp)/'payload.json'; payload.write_text(json.dumps(body))
            command+=['--header','Content-Type: application/json','--data','@'+str(payload)]
        result=subprocess.run(command,capture_output=True,text=True,timeout=120)
    if result.returncode:
        raise RuntimeError('GitHub API request failed for '+path)
    response=json.loads(result.stdout)
    if 'message' in response and 'documentation_url' in response:
        raise RuntimeError('GitHub refused '+path+': '+response['message'])
    return response


def publish():
    if git('status','--porcelain'):
        raise RuntimeError('Uncommitted changes prevent publication.')
    if git('branch','--show-current')!=BRANCH:
        raise RuntimeError('Expected production branch.')
    if git('config','user.name')!='Aravind' or git('config','user.email')!='this.aravind@gmail.com':
        raise RuntimeError('Git identity does not match Aravind.')
    head=git('rev-parse','HEAD')
    remote=api('/git/ref/heads/'+BRANCH)['object']['sha']
    if remote==head:
        return head
    if git('rev-parse','HEAD^')!=remote:
        raise RuntimeError('Remote branch changed or multiple unpublished commits exist. Preserve work and reconcile before publishing.')
    tree=[]
    for line in git('ls-tree','-r','HEAD').splitlines():
        meta,path=line.split('\t',1); mode,kind,sha=meta.split()
        if path.split('/')[0] in {'data','docs','.wrangler','node_modules'} or path.startswith(('.env','.dev.vars')):
            raise RuntimeError('Private state cannot be published to the code repository.')
        if kind!='blob':
            raise RuntimeError('Unexpected Git entry')
        content=subprocess.check_output(['git','show','HEAD:'+path],cwd=ROOT).decode('utf-8')
        tree.append({'path':path,'mode':mode,'type':'blob','content':content})
    new_tree=api('/git/trees','POST',{'tree':tree})
    if new_tree['sha']!=git('rev-parse','HEAD^{tree}'):
        raise RuntimeError('Remote tree differs from the verified local tree.')
    author={'name':git('show','-s','--format=%an','HEAD'),'email':git('show','-s','--format=%ae','HEAD'),'date':git('show','-s','--format=%aI','HEAD')}
    committer={'name':git('show','-s','--format=%cn','HEAD'),'email':git('show','-s','--format=%ce','HEAD'),'date':git('show','-s','--format=%cI','HEAD')}
    if author['name']!='Aravind' or committer['name']!='Aravind' or author['email']!='this.aravind@gmail.com' or committer['email']!=author['email']:
        raise RuntimeError('Commit attribution does not match Aravind.')
    raw=subprocess.check_output(['git','cat-file','commit','HEAD'],cwd=ROOT,text=True)
    message=raw.split('\n\n',1)[1]
    commit=api('/git/commits','POST',{'tree':new_tree['sha'],'parents':[remote],'message':message,'author':author,'committer':committer})
    if commit['sha']!=head:
        raise RuntimeError('Remote commit differs from the verified local commit; branch was not changed.')
    api('/git/refs/heads/'+BRANCH,'PATCH',{'sha':head,'force':False})
    subprocess.run(['git','fetch','--prune','origin'],cwd=ROOT,check=True)
    if git('rev-parse','origin/'+BRANCH)!=head:
        raise RuntimeError('Remote publication read-back failed.')
    return head


if __name__=='__main__':
    print(json.dumps({'commit':publish(),'url':'https://github.com/ara-research-ai/warm-up'}))
