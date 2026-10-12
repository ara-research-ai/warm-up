"""One idempotent server-side run invoked by T3 Code Automation."""
import argparse
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo

from collect import collect
from deploy import deploy
from publish import git, ROOT
from snapshots import build, store_snapshot, STATE


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--publish',action='store_true',help='Deploy private generated pages to Cloudflare Pages.')
    parser.add_argument('--daily',action='store_true',help='Reuse an already completed daily snapshot on retry.')
    args=parser.parse_args()
    os.umask(0o077)
    STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
    with open(STATE/'daily.lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if git('branch','--show-current')!='production':
            raise RuntimeError('Expected production branch; no changes made.')
        if git('status','--porcelain'):
            raise RuntimeError('Existing local work prevents an automatic update. Preserve it and report it.')
        subprocess.run(['git','fetch','--prune','origin'],cwd=ROOT,check=True)
        ahead,behind=map(int,git('rev-list','--left-right','--count','HEAD...origin/production').split())
        if ahead and behind:
            raise RuntimeError('Branch has diverged. No reset, rebase or force-push is authorized.')
        if ahead:
            raise RuntimeError('Unpublished source commits prevent an automatic run. Preserve them and report it.')
        subprocess.run(['git','pull','--ff-only','origin','production'],cwd=ROOT,check=True)
        if git('config','user.name')!='Aravind' or git('config','user.email')!='this.aravind@gmail.com':
            raise RuntimeError('Git identity does not match Aravind.')
        local=datetime.now(ZoneInfo('Asia/Kuala_Lumpur'))
        run_key=('daily-'+local.strftime('%Y-%m-%d')+'-0900') if args.daily else ('manual-'+local.isoformat())
        previous=[json.loads(path.read_text()) for path in (STATE/'data').glob('*.json')]
        existing=next((item for item in previous if item.get('runKey')==run_key),None)
        snapshot=existing or collect(run_key)
        store_snapshot(STATE,snapshot)
        archive=build()
        if args.publish:
            deployment=deploy()
        else:
            deployment=None
        print(json.dumps({'snapshot':snapshot['id'],'deployment':deployment,'path':next(item['path'] for item in archive['snapshots'] if item['id']==snapshot['id']),'count':len(snapshot['rows']),'capturedAt':snapshot['capturedAt']}))


if __name__=='__main__':
    try:
        main()
    except Exception as error:
        # Do not print a traceback or subprocess command containing credentials.
        print(json.dumps({'error':str(error) if isinstance(error,(RuntimeError,ValueError,BlockingIOError)) else type(error).__name__}))
        raise SystemExit(1)
