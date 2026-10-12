const { test } = require('node:test');
const assert = require('node:assert/strict');

test('Malaysia uses +60, while New Zealand stays in non-Malaysia', () => {
  const { countryMatches } = require('../web/app.js');
  assert.equal(countryMatches('+60 11-0000 0000', 'malaysia'), true);
  assert.equal(countryMatches('+64 21 123 4567', 'malaysia'), false);
  assert.equal(countryMatches('+234 900 000 0000', 'other'), true);
  assert.equal(countryMatches('+60 11-0000 0000', 'other'), false);
  assert.equal(countryMatches('+234 900 000 0000', 'all'), true);
});

test('each browser retains its country selection across snapshots', () => {
  const { loadCountry, saveCountry } = require('../web/app.js');
  const map = new Map();
  const browser = { getItem: key => map.get(key), setItem: (key,value) => map.set(key,value) };
  assert.equal(loadCountry(browser), 'all');
  saveCountry(browser, 'malaysia');
  assert.equal(loadCountry(browser), 'malaysia');
  saveCountry(browser, 'other');
  assert.equal(loadCountry(browser), 'other');
  assert.equal(loadCountry({ getItem: () => 'broken' }), 'all');
  assert.equal(loadCountry({ getItem: () => { throw Error('denied'); } }), 'all');
  assert.doesNotThrow(() => saveCountry({ setItem: () => { throw Error('denied'); } }, 'other'));
});
