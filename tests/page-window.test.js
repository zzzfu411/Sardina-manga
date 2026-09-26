import test from 'node:test';
import assert from 'node:assert/strict';
import {pageWindow, pageForIndex} from '../web/page-window.js';

test('a large filtered collection remains reachable with a fixed DOM budget', () => {
  const all = Array.from({length: 2000}, (_, index) => ({index, title: `第${index + 1}话`}));
  const seen = [];
  for (let page = 0; page < 25; page++) {
    const window = pageWindow(all, {page, size: 80});
    assert.equal(window.items.length, 80);
    seen.push(...window.items.map(item => item.index));
  }
  assert.deepEqual(seen, all.map(item => item.index));
  const matches = all.filter(item => item.title.includes('1999'));
  assert.equal(pageWindow(matches, {page: 20, size: 80}).items[0].index, 1998);
  assert.equal(all.length, 2000);
});

test('removing the last item on a page returns to a valid page without losing earlier items', () => {
  const page = pageWindow(Array.from({length: 48}, (_, index) => index), {page: 1});
  assert.equal(page.page, 0);
  assert.equal(page.hasNext, false);
  assert.equal(page.items.at(-1), 47);
  assert.equal(pageWindow([], {page: 90}).page, 0);
  assert.equal(pageForIndex(1999, 80), 24);
});
