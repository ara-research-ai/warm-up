"""Read production through the approved URI helper, without storing credentials."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
SKILL=Path.home()/'.codex'/'skills'/'mongodb-production-readonly'
MYT=ZoneInfo('Asia/Kuala_Lumpur')


def calendar_warmup_day(started_at, captured_at):
    if not isinstance(started_at, str):
        raise ValueError('Current warm-up cycle has no start date.')
    start=datetime.fromisoformat(started_at.replace('Z','+00:00'))
    captured=datetime.fromisoformat(captured_at.replace('Z','+00:00'))
    if start.tzinfo is None or captured.tzinfo is None or start > captured:
        raise ValueError('Invalid or future warm-up start date.')
    return (captured.astimezone(MYT).date()-start.astimezone(MYT).date()).days+1


def greetings(day):
    weekday=day.strftime('%A')
    prefixes=['Hi!', 'Hello!', 'Hey!', 'Hi there!', 'Hello there!', 'Hey there!']
    questions=[
        'How are you doing today?', 'How has your day been so far?',
        'Hope everything is going well.', 'How are things on your side?',
        'Just stopping by to say hello.', 'Hope you have had a pleasant day.',
        'Anything nice planned for today?', 'Wishing you a lovely day.',
        'How have you been lately?', 'Hope all is well with you.',
        'What are you looking forward to this week?', 'Hope today treats you well.',
        'Have you done anything nice recently?', 'How has your week been going?',
        'Hope you get some time to relax today.', 'Wishing you a good week ahead.',
        'What has been the best part of your day?', 'Hope your day is going well.'
    ]
    pool=[f'{prefix} {question}' for prefix in prefixes for question in questions]
    offset=day.toordinal()%len(pool)
    return [f'Happy {weekday}! How is your day going?', *pool[offset:], *pool[:offset]]


def collect(run_key):
    check=subprocess.run([str(SKILL/'scripts'/'check-readonly-access.sh')],capture_output=True,text=True,timeout=40)
    if check.returncode:
        raise RuntimeError('Production read-only access check failed. No snapshot was written.')
    access=json.loads(check.stdout)
    if not access.get('connected') or access.get('knownMutatingRoles'):
        raise RuntimeError('Production role check refused access.')
    request=urllib.request.Request('https://focused-barracuda-773.convex.cloud/api/query',data=json.dumps({'path':'queries/phones:list','args':{'isActive':True},'format':'json'}).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=30) as response:
        manager=json.load(response)
    if manager.get('status')!='success' or not isinstance(manager.get('value'),list):
        raise RuntimeError('Warmup Manager query failed. No snapshot was written.')
    manager_map={re.sub(r'\D','',row['phoneNumber']):row for row in manager['value']}
    command=['mongosh','--nodb','--quiet','--norc','--file',str(ROOT/'scripts'/'query.js')]
    # The query reads the URI from its environment; never log database errors.
    try:
        query=subprocess.run(command,capture_output=True,text=True,timeout=40)
    except subprocess.TimeoutExpired:
        raise RuntimeError('Production read timed out. No snapshot was written.') from None
    if query.returncode:
        raise RuntimeError('Production read failed. No snapshot was written.')
    snapshot=json.loads(query.stdout)
    day=datetime.fromisoformat(snapshot['capturedAt'].replace('Z','+00:00')).astimezone(MYT)
    messages=greetings(day)
    rows=sorted(snapshot['rows'],key=lambda row:(['warmup','cooldown','ready'].index(row['group']),row['name']))
    if len(rows)>len(messages):
        raise RuntimeError('Not enough unique greetings. No snapshot was written.')
    for row,message in zip(rows,messages):
        row['number']=re.sub(r'\D','',row['number'])
        if row['group']=='warmup' and 'calendarStartedAt' in row:
            row['day']=calendar_warmup_day(row.pop('calendarStartedAt'), snapshot['capturedAt'])
        elif row['group']=='warmup' and row['number'] in manager_map:
            row['day']=int(manager_map[row['number']]['dayNumber'])
        row['message']=message
    snapshot.update({'source':'live','coverage':'Warmup, cooldown and ready to activate. Inactive Super Admin clients and DUITGINI excluded. RenMoney 23-27 show calendar days in the current warm-up cycle.','runKey':run_key,'rows':rows})
    return snapshot


if __name__=='__main__':
    print(json.dumps(collect('manual-'+datetime.now(timezone.utc).isoformat())))
