"""Build static pages from validated, immutable outreach snapshots."""
from datetime import datetime
import base64
from html import escape
import json
from pathlib import Path
import re
from urllib.parse import quote
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
STATE = Path.home() / '.local' / 'share' / 'ara-warm-up'
MYT = ZoneInfo('Asia/Kuala_Lumpur')
GROUPS = [('warmup', 'Warmup'), ('cooldown', 'Cooldown'), ('ready', 'Ready to activate'), ('unknown', 'Status not recorded')]
LOGO = 'data:image/svg+xml;base64,' + base64.b64encode((ROOT/'web'/'logo.svg').read_bytes()).decode('ascii')


def validate(snapshot):
    datetime.fromisoformat(snapshot['capturedAt'].replace('Z', '+00:00'))
    seen = set()
    for row in snapshot['rows']:
        row['number'] = re.sub(r'\D', '', row['number'])
        if not re.fullmatch(r'[1-9]\d{7,14}', row['number']):
            raise ValueError('Invalid phone number')
        if row['number'] in seen:
            raise ValueError('Duplicate phone number in snapshot')
        seen.add(row['number'])
        if row['group'] not in dict(GROUPS) or not row['name']:
            raise ValueError('Invalid phone group or name')


def store_snapshot(root, snapshot):
    validate(snapshot)
    local = datetime.fromisoformat(snapshot['capturedAt'].replace('Z', '+00:00')).astimezone(MYT)
    snapshot.setdefault('id', local.strftime('%Y-%m-%dT%H-%M-%S'))
    data = root / 'data'; data.mkdir(parents=True, exist_ok=True)
    path = data / (snapshot['id'] + '.json')
    for existing in data.glob('*.json'):
        previous = json.loads(existing.read_text())
        if previous.get('runKey') == snapshot.get('runKey') or existing == path:
            if previous != snapshot:
                raise ValueError('Refusing to overwrite an existing snapshot')
            return existing
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + '\n')
    return path


def formatted_number(number):
    if number.startswith('234') and len(number) == 13:
        return '+234 ' + number[3:6] + ' ' + number[6:9] + ' ' + number[9:]
    if number.startswith('60') and len(number) == 12:
        return '+60 ' + number[2:4] + '-' + number[4:8] + ' ' + number[8:]
    return '+' + number


def summary(snapshot):
    local = datetime.fromisoformat(snapshot['capturedAt'].replace('Z', '+00:00')).astimezone(MYT)
    counts = {key: sum(row['group'] == key for row in snapshot['rows']) for key, _ in GROUPS}
    return {'id': snapshot['id'], 'date': local.strftime('%Y-%m-%d'), 'time': local.strftime('%H:%M:%S'), 'capturedAt': snapshot['capturedAt'], 'path': 'snapshots/' + local.strftime('%Y-%m-%d') + '/' + snapshot['id'] + '.html', 'count': len(snapshot['rows']), 'counts': counts, 'coverage': snapshot['coverage'], 'correction': bool(snapshot.get('correction')), 'correctedBy': snapshot.get('correctedBy')}


def shell(title, body, style, script=''):
    return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><meta name="theme-color" content="#1655a8"><link rel="icon" href="' + LOGO + '"><title>' + escape(title) + '</title><style>' + style + '</style></head><body><main>' + body + '</main>' + script + '</body></html>\n'


def render(snapshot, style, app, root, home=False):
    meta = summary(snapshot)
    local = datetime.fromisoformat(snapshot['capturedAt'].replace('Z', '+00:00')).astimezone(MYT)
    date = f'{local.day} {local.strftime("%B %Y")}'
    malaysia = sum(row['number'].startswith('60') for row in snapshot['rows'])
    kind = 'Latest list' if home else 'Saved snapshot'
    body = f'''<nav class="brand"><span class="brand-mark"><img src="{LOGO}" alt="" width="32" height="32"></span> Ara Warm Up<a href="{root}archive.html">History ↗</a></nav>
<div class="eyebrow">{kind}</div><h1>Your daily Warm Up list.</h1><p class="subtitle">Choose your lines, then open a draft in WhatsApp.</p>
<div class="date-line"><strong>{date}</strong><span class="tag">{meta['time']} MYT</span><a class="tag" href="{root}index.html">Latest</a></div>
<div id="snapshot-notice" class="notice" hidden><span>You are viewing an older snapshot.</span> <a href="{root}index.html">Go to latest →</a></div>
<div id="freshness-notice" class="notice" hidden>Today's update has not been published yet. This is the last available list.</div>
<div id="correction-notice" class="notice" hidden>A corrected version of this list is available. <a href="#">Open correction →</a></div>
<details class="history"><summary>Choose a date &amp; time</summary><div class="history-controls"><label>Date<select id="archive-date" disabled><option>{date}</option></select></label><label>Time<select id="archive-time" disabled><option>{meta['time']} MYT</option></select></label><button class="secondary" id="open-snapshot" disabled>View list →</button></div><p class="subtitle" id="archive-error" hidden>History could not load. <a href="{root}archive.html">Open the archive</a>.</p></details>
<div class="filters-wrap"><div id="country-filters" class="filters" role="group" aria-label="Country filter"><button data-country="malaysia" aria-pressed="false">Malaysia <small>{malaysia}</small></button><button data-country="other" aria-pressed="false">Non-Malaysia <small>{len(snapshot['rows'])-malaysia}</small></button><button data-country="all" aria-pressed="true">All <small>{len(snapshot['rows'])}</small></button></div><p class="filter-description" id="filter-description">Showing all lines. Your choice is saved in this browser.</p></div>
<div class="stats">''' + ''.join(f'<div class="stat"><strong data-stat="{key}">{meta["counts"][key]}</strong><span>{label}</span></div>' for key, label in GROUPS[:3]) + f'''</div><p class="draft-note">Showing <strong id="visible-count" aria-live="polite">{len(snapshot['rows'])}</strong> lines. Opens WhatsApp with a draft. Tap Send in WhatsApp to send it.</p>
<noscript><p class="notice">JavaScript is required for country filters. All numbers are shown below.</p></noscript>
<div id="empty-filter" class="empty" hidden><p id="empty-filter-label"></p><button class="secondary" id="show-all">Show all lines</button></div>'''
    if not snapshot['rows']:
        body += '<p class="empty">No phones in this snapshot.</p>'
    for key, label in GROUPS:
        rows = sorted((r for r in snapshot['rows'] if r['group'] == key), key=lambda r: (r['name'].lower().rsplit(' ', 1)[0], int(r['name'].rsplit(' ', 1)[-1]) if r['name'].rsplit(' ', 1)[-1].isdigit() else 0, r['name']))
        if not rows:
            continue
        body += f'<section class="group" data-group-section="{key}"><div class="group-head"><h2>{label}</h2><span class="group-count" data-group-count>{len(rows)}</span></div><div class="cards">'
        for row in rows:
            status = label + (f', Day {row["day"]}' if key == 'warmup' and row.get('day') else '')
            link = 'https://wa.me/' + row['number'] + ('?text=' + quote(row['message'], safe='') if row.get('message') else '')
            message = escape(row.get('message') or 'No greeting was recorded for this historical link.')
            body += f'<article class="phone-card" data-phone="{row["number"]}"><div class="card-head"><div class="card-name">{escape(row["name"])}</div><span class="badge {key}">{status}</span></div><div class="phone-number">{escape(formatted_number(row["number"]))}</div><p class="draft">{message}</p><a class="open-whatsapp" target="_blank" rel="noopener noreferrer" href="{escape(link, quote=True)}">Open in WhatsApp <span aria-hidden="true">↗</span></a></article>'
        body += '</div></section>'
    body += f'<footer class="footer"><span>{escape(snapshot["coverage"])}</span><a href="{root}archive.html">Browse history →</a></footer>'
    config = {'id': snapshot['id'], 'date': meta['date'], 'root': root, 'home': home}
    script = '<script>window.OUTREACH=' + json.dumps(config).replace('<', '\\u003c') + ';</script><script>' + app + '</script>'
    return shell('Ara Warm Up · ' + date, body, style, script)


def build(root=STATE):
    snapshots = [json.loads(path.read_text()) for path in (root/'data').glob('*.json')]
    if not snapshots:
        raise ValueError('No snapshots to build')
    snapshots.sort(key=lambda item: datetime.fromisoformat(item['capturedAt'].replace('Z', '+00:00')), reverse=True)
    for snapshot in snapshots:
        validate(snapshot)
    style = (ROOT/'web'/'style.css').read_text()
    app = (ROOT/'web'/'app.js').read_text()
    docs = root/'docs'; docs.mkdir(parents=True, exist_ok=True)
    archive = {'timezone':'Asia/Kuala_Lumpur', 'snapshots':[summary(item) for item in snapshots]}
    (docs/'archive.json').write_text(json.dumps(archive, ensure_ascii=False, indent=2)+'\n')
    (docs/'index.html').write_text(render(snapshots[0],style,app,'./',True))
    for snapshot, meta in zip(snapshots, archive['snapshots']):
        path=docs/meta['path']; path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(render(snapshot,style,app,'../../'))
    body=f'<nav class="brand"><span class="brand-mark"><img src="{LOGO}" alt="" width="32" height="32"></span> Ara Warm Up<a href="index.html">Latest list ↗</a></nav><div class="eyebrow">Saved updates</div><h1>Saved lists.</h1><p class="subtitle">Choose any date and time. All times are Malaysia time. Dates without a recorded list are not filled in.</p>'
    month = None
    for item in archive['snapshots']:
        local=datetime.fromisoformat(item['capturedAt'].replace('Z','+00:00')).astimezone(MYT)
        key=local.strftime('%B %Y')
        if key != month:
            if month is not None: body+='</ul>'
            body+=f'<h2 class="archive-month">{key}</h2><ul class="archive-list">'; month=key
        correction=' · correction' if item['correction'] else ''
        body+=f'<li><div><a href="{item["path"]}">{local.day} {local.strftime("%B")} · {item["time"]} MYT{correction}</a><small>{escape(item["coverage"])}</small></div><span class="tag">{item["count"]} lines</span></li>'
    body+='</ul>'
    (docs/'archive.html').write_text(shell('Ara Warm Up · History',body,style))
    (docs/'.nojekyll').touch()
    (docs/'robots.txt').write_text('User-agent: *\nDisallow: /\n')
    (docs/'_routes.json').write_text(json.dumps({'version':1, 'include':['/*'], 'exclude':[]})+'\n')
    (docs/'_headers').write_text('/*\n  Cache-Control: private, no-store\n  X-Robots-Tag: noindex, nofollow\n')
    return archive


if __name__=='__main__':
    result=build()
    print(json.dumps({'snapshots':len(result['snapshots']), 'latest':result['snapshots'][0]['path']}))
