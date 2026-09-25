import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeReaderPreferences, imageCandidates, pageNavigation, pageTurnDelta, fitPageWidth, restorePosition, progressSnapshot} from '../web/reader-model.js';

test('existing v2 preferences keep continuous reading and retain theme, width and immersion', () => {
  assert.deepEqual(normalizeReaderPreferences({theme: 'light', width: 920, focused: true}), {
    theme: 'light', width: 920, focused: true, mode: 'continuous', direction: 'ltr', fit: 'width', zoom: 1, prefetch: 'auto',
  });
  assert.deepEqual(normalizeReaderPreferences(null, {light: true, width: 700}), {
    theme: 'light', width: 700, focused: false, mode: 'continuous', direction: 'ltr', fit: 'width', zoom: 1, prefetch: 'auto',
  });
});

test('paged and direction preferences survive storage while corrupt imported values fall back safely', () => {
  const saved = {theme: 'dark', width: 800, focused: false, mode: 'paged', direction: 'rtl', fit: 'page', zoom: 1, prefetch: 'more'};
  assert.deepEqual(normalizeReaderPreferences(JSON.parse(JSON.stringify(saved))), saved);
  assert.deepEqual(normalizeReaderPreferences({theme: 'unknown', width: 'bad', focused: 'yes', mode: 'auto', direction: 'up'}), {
    theme: 'dark', width: 800, focused: false, mode: 'continuous', direction: 'ltr', fit: 'width', zoom: 1, prefetch: 'auto',
  });
  assert.equal(normalizeReaderPreferences({width: 10}).width, 480);
  assert.equal(normalizeReaderPreferences({width: 2000}).width, 1200);
  assert.equal(normalizeReaderPreferences([]).mode, 'continuous');
});

test('paged reading buffers nearby pages without using stale continuous viewport bounds', () => {
  const common = {total: 209, page: 179, first: 0, last: 208};
  assert.deepEqual(imageCandidates({...common, mode: 'paged'}), [179, 180, 181, 182, 178]);
  assert.deepEqual(imageCandidates({...common, mode: 'paged', ahead: 0}), [179]);
  assert.deepEqual(imageCandidates({total: 1, page: 0, mode: 'paged'}), [0]);
  assert.deepEqual(imageCandidates({total: 0, page: 10, mode: 'paged'}), []);
  assert.deepEqual(imageCandidates({...common, page: 999, mode: 'paged'}), [208, 207]);
});

test('switching to paged with data saving removes hidden prefetch without replacing the current request', () => {
  const state = {total: 100, page: 42, first: 42, last: 42};
  const continuous = imageCandidates(state), paged = imageCandidates({...state, mode: 'paged', ahead: 0});
  assert.equal(continuous[0], paged[0]);
  assert.deepEqual(paged, [42]);
  assert.ok(continuous.filter(index => !paged.includes(index)).length > 0);
  assert.equal(new Set(continuous).size, continuous.length);
});

test('first, last, single-page and empty chapters never manufacture a cross-chapter page target', () => {
  assert.deepEqual(pageNavigation(0, 8), {previous: null, next: 1});
  assert.deepEqual(pageNavigation(7, 8), {previous: 6, next: null});
  assert.deepEqual(pageNavigation(0, 1), {previous: null, next: null});
  assert.deepEqual(pageNavigation(0, 0), {previous: null, next: null});
  assert.deepEqual(pageNavigation(999, 8), {previous: 6, next: null});
});

test('reading direction changes arrow intent, preserving ascending logical page and image order', () => {
  assert.equal(pageTurnDelta('ArrowRight', 'ltr'), 1);
  assert.equal(pageTurnDelta('ArrowLeft', 'ltr'), -1);
  assert.equal(pageTurnDelta('ArrowLeft', 'rtl'), 1);
  assert.equal(pageTurnDelta('ArrowRight', 'rtl'), -1);
  assert.equal(pageTurnDelta('Escape', 'rtl'), 0);
  for (const [direction, forwardKey] of [['ltr', 'ArrowRight'], ['rtl', 'ArrowLeft']]) {
    const target = pageNavigation(5, 9)[pageTurnDelta(forwardKey, direction) > 0 ? 'next' : 'previous'];
    assert.equal(target, 6);
    assert.deepEqual(imageCandidates({total: 9, page: target, mode: 'paged', ahead: 0}), [6]);
  }
});

test('whole-page fitting respects viewport height, width preference, landscape and long strips', () => {
  for (const input of [
    {ratio: 1.42, viewportWidth: 1440, viewportHeight: 700, preferredWidth: 800},
    {ratio: 0.5, viewportWidth: 375, viewportHeight: 530, preferredWidth: 800},
    {ratio: 10, viewportWidth: 390, viewportHeight: 640, preferredWidth: 800},
    {ratio: 1, viewportWidth: 1400, viewportHeight: 1000, preferredWidth: 480},
  ]) {
    const width = fitPageWidth(input);
    assert.ok(width > 0);
    assert.ok(width <= input.viewportWidth);
    assert.ok(width <= input.preferredWidth);
    assert.ok(width * input.ratio <= input.viewportHeight + 0.00001);
  }
  assert.equal(fitPageWidth({ratio: 10, viewportWidth: 390, viewportHeight: 640, preferredWidth: 800}), 64);
});

test('mode-independent shelf positions preserve page offset and cannot record a merely selected unloaded page', () => {
  const saved = {chapterUrl: 'chapter-1', page: 42, pageOffset: 0.61};
  const position = restorePosition(saved, 'chapter-1', 100);
  const state = {chapter: {url: 'chapter-1', name: '第1话'}, total: 100, position, restoring: false, loaded: true};
  assert.equal(progressSnapshot(state).pageOffset, 0.61);
  assert.deepEqual(restorePosition(progressSnapshot(state), 'chapter-1', 100), position);
  assert.equal(progressSnapshot({...state, position: {page: 43, pageOffset: 0}, loaded: false}), null);
  assert.equal(progressSnapshot({...state, restoring: true}), null);
});
