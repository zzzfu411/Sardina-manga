import test from 'node:test';
import assert from 'node:assert/strict';
import {SOURCE_PREFERENCES_KEY, loadSourcePreferences, saveSourcePreferences, sortSources, toggleFavorite} from '../web/source-preferences.js';

function memoryStorage(initial = null) {
  let value = initial;
  return {
    getItem(key) {assert.equal(key, SOURCE_PREFERENCES_KEY); return value;},
    setItem(key, next) {assert.equal(key, SOURCE_PREFERENCES_KEY); value = next;},
  };
}

test('preferences round-trip without changing the available source registry', () => {
  const storage = memoryStorage();
  const preferences = toggleFavorite(loadSourcePreferences(storage), 'mangabz');
  assert.equal(saveSourcePreferences(preferences, storage), true);
  assert.deepEqual(loadSourcePreferences(storage), {favoriteIds: ['mangabz']});
  assert.deepEqual(toggleFavorite(preferences, 'mangabz'), {favoriteIds: []});
  assert.deepEqual(preferences, {favoriteIds: ['mangabz']});
});

test('favorite sorting preserves all 25 sources and each group’s original order', () => {
  const sources = Object.freeze(Array.from({length: 25}, (_, n) => Object.freeze({siteId: `source-${n}`})));
  const sorted = sortSources(sources, {favoriteIds: ['source-24', 'source-7', 'unknown-source']});
  assert.deepEqual(sorted, [sources[7], sources[24], ...sources.filter((_, n) => n !== 7 && n !== 24)]);
  assert.equal(sorted.length, 25);
  assert.equal(new Set(sorted).size, 25);
  assert.equal(sources[0].siteId, 'source-0');
  assert.ok(sorted.every(site => sources.includes(site)));
});

test('unknown, duplicate, and invalid saved IDs do not invent or remove search sources', () => {
  const storage = memoryStorage(JSON.stringify({favoriteIds: ['ghost', 'dm5', 'dm5', '../dm5', 'https://x', '', 12, {}, null, 'Baozi'], enabledIds: ['ghost']}));
  const preferences = loadSourcePreferences(storage);
  assert.deepEqual(preferences, {favoriteIds: ['ghost', 'dm5']});
  const sources = [{siteId: 'baozi'}, {siteId: 'dm5'}, {siteId: 'baozi'}, {}];
  assert.deepEqual(sortSources(sources, preferences), [sources[1], sources[0], sources[2], sources[3]]);
});

test('malformed storage and disabled storage degrade to the full default list', () => {
  for (const raw of [null, '{broken', 'null', '[]', 'false', '"dm5"', '{"favoriteIds":"dm5"}']) {
    assert.deepEqual(loadSourcePreferences(memoryStorage(raw)), {favoriteIds: []});
  }
  const blocked = {getItem() {throw new Error('blocked');}, setItem() {throw new Error('quota');}};
  assert.deepEqual(loadSourcePreferences(blocked), {favoriteIds: []});
  assert.equal(saveSourcePreferences({favoriteIds: ['dm5']}, blocked), false);
  assert.deepEqual(sortSources([{siteId: 'dm5'}, {siteId: 'baozi'}], loadSourcePreferences(blocked)), [{siteId: 'dm5'}, {siteId: 'baozi'}]);
});

test('localStorage getter denial is caught and a failed save is observable', () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  Object.defineProperty(globalThis, 'localStorage', {configurable: true, get() {throw new Error('SecurityError');}});
  try {
    assert.deepEqual(loadSourcePreferences(), {favoriteIds: []});
    assert.equal(saveSourcePreferences({favoriteIds: ['dm5']}), false);
  } finally {
    if (descriptor) Object.defineProperty(globalThis, 'localStorage', descriptor);
    else delete globalThis.localStorage;
  }
});

test('toggle ignores invalid IDs and bounds corrupt oversized preference arrays', () => {
  const initial = Object.freeze({favoriteIds: Object.freeze(['dm5'])});
  for (const bad of [null, {}, 'a'.repeat(65), 'D M 5', '/dm5']) {
    assert.deepEqual(toggleFavorite(initial, bad), {favoriteIds: ['dm5']});
  }
  const oversized = {favoriteIds: Array.from({length: 300}, (_, n) => `source-${n}`)};
  const storage = memoryStorage(JSON.stringify(oversized));
  assert.equal(loadSourcePreferences(storage).favoriteIds.length, 256);
  assert.equal(toggleFavorite(loadSourcePreferences(storage), 'one-more').favoriteIds.length, 256);
  assert.equal(toggleFavorite(loadSourcePreferences(storage), 'source-0').favoriteIds.length, 255);
});
