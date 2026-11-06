"""Confirm listed warming accounts through the existing date-deduplicated action."""
from datetime import datetime, timezone
import json
import re
import urllib.request

from snapshots import validate

MANAGER_URL = 'https://focused-barracuda-773.convex.cloud'


def manager_call(kind, path, args):
    request = urllib.request.Request(
        MANAGER_URL + '/api/' + kind,
        data=json.dumps({'path': path, 'args': args, 'format': 'json'}).encode(),
        headers={'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(request, timeout=120 if kind == 'action' else 30) as response:
            payload = json.load(response)
        if payload.get('status') != 'success':
            raise ValueError('Unacknowledged response')
        return payload['value']
    except Exception:
        # Responses and exceptions may contain private account data.
        raise RuntimeError('Warm-up counter ' + kind + ' failed. Retry the same date to reconcile any accepted credits.') from None


def credited_on(phone, date_key):
    return phone.get('lastDayIncrementDateKey') == date_key or date_key in phone.get('creditedDateKeys', [])


def advance_warmup_days(snapshot, dry_run=False):
    captured = datetime.fromisoformat(snapshot['capturedAt'].replace('Z', '+00:00'))
    if (snapshot.get('source') != 'live' or captured.tzinfo is None or
            captured.astimezone(timezone.utc).date() != datetime.now(timezone.utc).date()):
        raise RuntimeError('Warm-up counter requires a fresh successful live collection for today.')
    validate(snapshot)
    # The existing Manager credit ledger uses UTC dates. At 09:00 MYT its date
    # equals the Malaysia date; use that same key to deduplicate manual/AI credits.
    date_key = captured.astimezone(timezone.utc).date().isoformat()
    rows = [row for row in snapshot['rows'] if row['group'] == 'warmup']
    result = {'dateKey': date_key, 'eligible': len(rows), 'excluded': len(snapshot['rows']) - len(rows),
              'credited': 0, 'alreadyCredited': 0, 'skipped': 0}
    if not rows:
        return {**result, **({'pending': 0, 'dryRun': True} if dry_run else {})}
    if len(rows) > 100:
        raise RuntimeError('Warm-up counter selection exceeds the supported 100-account bound.')

    phones = manager_call('query', 'queries/phones:list', {'isActive': True})
    if not isinstance(phones, list):
        raise RuntimeError('Warm-up counter Manager query returned an invalid list.')
    selected = []
    for row in rows:
        matches = [phone for phone in phones
                   if re.sub(r'\D', '', phone.get('phoneNumber', '')) == row['number']]
        if len(matches) != 1:
            raise RuntimeError('Warm-up counter identity is missing or ambiguous. No accounts were credited.')
        phone = matches[0]
        account_id = row.get('accountId', '')
        if (not re.fullmatch(r'[0-9a-f]{24}', account_id) or
                phone.get('sourceSimExternalId') != account_id or
                phone.get('source') != 'adacash_new_sim' or not phone.get('_id') or
                phone.get('isActive') is not True or phone.get('warmupState') != 'warming'):
            raise RuntimeError('Warm-up counter identity or warming eligibility changed. No accounts were credited.')
        selected.append(phone)

    if dry_run:
        result['alreadyCredited'] = sum(credited_on(phone, date_key) for phone in selected)
        return {**result, 'pending': len(selected) - result['alreadyCredited'], 'dryRun': True}

    confirmed = manager_call('action', 'actions/warmup:markManualWarmupDone',
                             {'phoneIds': [phone['_id'] for phone in selected], 'dateKey': date_key})
    if (not isinstance(confirmed, dict) or confirmed.get('dateKey') != date_key or
            not isinstance(confirmed.get('failed'), list) or
            any(type(confirmed.get(key)) not in (int, float) or confirmed[key] < 0 or
                confirmed[key] != int(confirmed[key]) for key in ('credited', 'alreadyCredited', 'skipped')) or
            sum(confirmed[key] for key in ('credited', 'alreadyCredited', 'skipped')) + len(confirmed['failed']) != len(selected)):
        raise RuntimeError('Warm-up counter confirmation failed: invalid acknowledgement. Retry the same date.')
    if confirmed['failed'] or confirmed['skipped']:
        raise RuntimeError('Warm-up counter confirmation failed for ' +
                           str(len(confirmed['failed']) + int(confirmed['skipped'])) +
                           ' account(s). Accepted credits are preserved; retry the same date.')

    for before in selected:
        after = manager_call('query', 'queries/phones:getById', {'id': before['_id']})
        minimum = before.get('warmupMinimumDays', 7)
        current_day = max(before['dayNumber'], before.get('warmupCompletedDays', 0) + 1)
        expected_day = current_day if credited_on(before, date_key) else min(current_day + 1, minimum + 1)
        if (not isinstance(after, dict) or after.get('sourceSimExternalId') != before['sourceSimExternalId'] or
                after.get('startedAt') != before.get('startedAt') or
                not credited_on(after, date_key) or after.get('dayNumber', 0) < expected_day or
                after.get('warmupCompletedDays', 0) < min(after.get('dayNumber', 1) - 1, minimum)):
            raise RuntimeError('Warm-up counter read-back failed. Accepted credits are preserved; retry the same date.')
    return {**result, **{key: int(confirmed[key]) for key in ('credited', 'alreadyCredited', 'skipped')}}


if __name__ == '__main__':
    # Standalone execution is a live read-only preflight, never an apply command.
    try:
        from collect import collect
        snapshot = collect('counter-preflight-' + datetime.now(timezone.utc).isoformat())
        print(json.dumps(advance_warmup_days(snapshot, dry_run=True)))
    except Exception as error:
        print(json.dumps({'error': str(error) if isinstance(error, (RuntimeError, ValueError)) else type(error).__name__}))
        raise SystemExit(1)
