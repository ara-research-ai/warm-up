'use strict';

const COUNTRY_KEY = 'ara.whatsapp-outreach.country.v1';
const COUNTRIES = ['all', 'malaysia', 'other'];

function countryMatches(number, country) {
  const malaysia = String(number).replace(/\D/g, '').startsWith('60');
  return country === 'all' || (country === 'malaysia' ? malaysia : !malaysia);
}

function loadCountry(storage) {
  try {
    const value = storage.getItem(COUNTRY_KEY);
    return COUNTRIES.includes(value) ? value : 'all';
  } catch { return 'all'; }
}

function saveCountry(storage, country) {
  try { storage.setItem(COUNTRY_KEY, country); } catch { /* Filtering still works without storage. */ }
}

if (typeof module !== 'undefined') module.exports = { countryMatches, loadCountry, saveCountry };

if (typeof document !== 'undefined' && document.getElementById('country-filters')) {
  const settings = window.OUTREACH;
  let storage;
  try { storage = window.localStorage; } catch { storage = null; }
  let country = loadCountry(storage);
  const cards = [...document.querySelectorAll('[data-phone]')];
  const filters = [...document.querySelectorAll('[data-country]')];
  const countryNames = { all: 'all lines', malaysia: 'Malaysian lines', other: 'non-Malaysian lines' };

  async function copyNumber(number) {
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(number);
        return;
      } catch { /* Try the selection-based fallback when clipboard access is denied. */ }
    }
    const field = document.createElement('textarea');
    field.value = number;
    field.readOnly = true;
    field.style.cssText = 'position:fixed;top:0;left:-9999px;font-size:16px';
    const focused = document.activeElement;
    document.body.append(field);
    try {
      field.focus();
      field.select();
      field.setSelectionRange(0, number.length);
      if (!document.execCommand('copy')) throw Error('Copy unavailable');
    } finally {
      field.remove();
      if (focused) focused.focus({ preventScroll: true });
    }
  }

  for (const card of cards) {
    const button = card.querySelector('.copy-number');
    const error = card.querySelector('.copy-error');
    const status = document.getElementById('copy-status');
    let feedbackTimer;
    let copying = false;
    button.hidden = false;
    button.addEventListener('click', async () => {
      if (copying) return;
      copying = true;
      clearTimeout(feedbackTimer);
      button.setAttribute('aria-busy', 'true');
      error.hidden = true;
      status.textContent = '';
      try {
        await copyNumber('+' + card.dataset.phone);
        button.textContent = 'Copied';
        button.dataset.copied = 'true';
        status.textContent = `Number copied for ${card.querySelector('.card-name').textContent}.`;
      } catch {
        button.textContent = 'Try again';
        delete button.dataset.copied;
        error.hidden = false;
        status.textContent = error.textContent;
      } finally {
        copying = false;
        button.removeAttribute('aria-busy');
        feedbackTimer = setTimeout(() => {
          button.textContent = 'Copy';
          delete button.dataset.copied;
        }, 2000);
      }
    });
  }

  function applyCountry(value) {
    country = COUNTRIES.includes(value) ? value : 'all';
    for (const button of filters) button.setAttribute('aria-pressed', String(button.dataset.country === country));
    let visible = 0;
    for (const card of cards) {
      card.hidden = !countryMatches(card.dataset.phone, country);
      if (!card.hidden) visible++;
    }
    for (const section of document.querySelectorAll('[data-group-section]')) {
      const count = [...section.querySelectorAll('[data-phone]')].filter(card => !card.hidden).length;
      section.hidden = count === 0;
      section.querySelector('[data-group-count]').textContent = count;
      const stat = document.querySelector(`[data-stat="${section.dataset.groupSection}"]`);
      if (stat) stat.textContent = count;
    }
    document.getElementById('visible-count').textContent = visible;
    document.getElementById('filter-description').textContent = `Showing ${countryNames[country]}. Your choice is saved in this browser.`;
    const empty = document.getElementById('empty-filter');
    empty.hidden = visible !== 0 || cards.length === 0;
    document.getElementById('empty-filter-label').textContent = `No ${countryNames[country]} in this snapshot.`;
  }

  for (const button of filters) button.addEventListener('click', () => {
    saveCountry(storage, button.dataset.country);
    applyCountry(button.dataset.country);
  });
  document.getElementById('show-all').addEventListener('click', () => { saveCountry(storage, 'all'); applyCountry('all'); });
  applyCountry(country);

  const root = settings.root;
  const datePicker = document.getElementById('archive-date');
  const timePicker = document.getElementById('archive-time');
  const openButton = document.getElementById('open-snapshot');
  fetch(root + 'archive.json', { cache: 'no-store' }).then(response => {
    if (!response.ok) throw Error('Archive unavailable');
    return response.json();
  }).then(archive => {
    const snapshots = archive.snapshots;
    const latest = snapshots[0];
    const dates = [...new Set(snapshots.map(item => item.date))];
    datePicker.replaceChildren(...dates.map(date => new Option(new Date(date+'T12:00:00+08:00').toLocaleDateString('en-GB', { timeZone:'Asia/Kuala_Lumpur', day:'numeric',month:'long',year:'numeric' }),date)));
    datePicker.value = settings.date;
    function updateTimes() {
      const available = snapshots.filter(item => item.date === datePicker.value);
      timePicker.replaceChildren(...available.map(item => new Option(`${item.time} MYT${item.correction ? ' · correction' : ''}`, item.path)));
      const current = available.find(item => item.id === settings.id);
      if (current) timePicker.value = current.path;
    }
    datePicker.addEventListener('change', updateTimes);
    openButton.addEventListener('click', () => { if (timePicker.value) window.location.href = root + timePicker.value; });
    updateTimes();
    datePicker.disabled = timePicker.disabled = openButton.disabled = false;
    if (settings.id !== latest.id) {
      const banner = document.getElementById('snapshot-notice');
      banner.hidden = false;
      banner.querySelector('span').textContent = 'You are viewing an older snapshot. Phone statuses may have changed.';
      banner.querySelector('a').href = root + latest.path;
    }
    const current = snapshots.find(item => item.id === settings.id);
    if (current && current.correctedBy) {
      const correction = snapshots.find(item => item.id === current.correctedBy);
      if (correction) {
        const note=document.getElementById('correction-notice');
        note.hidden=false; note.querySelector('a').href=root+correction.path;
      }
    }
    if (settings.home) {
      const local = new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Kuala_Lumpur',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',hourCycle:'h23'}).formatToParts(new Date());
      const parts=Object.fromEntries(local.map(p=>[p.type,p.value]));
      const today=`${parts.year}-${parts.month}-${parts.day}`;
      if (latest.date < today && Number(parts.hour) >= 9) {
        document.getElementById('freshness-notice').hidden=false;
      }
    }
  }).catch(() => { document.getElementById('archive-error').hidden=false; });
}
