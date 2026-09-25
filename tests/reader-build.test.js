import test from 'node:test';
import assert from 'node:assert/strict';
import {buildReaderPages, PAGE_BUILD_BATCH, createChapterScope} from '../web/reader-model.js';

test('a 500-page chapter yields between bounded batches while preserving the complete ordered result', async () => {
  const urls = Array.from({length: 500}, (_, index) => `page-${index}`), sizes = [];
  let built = 0;
  const pages = await buildReaderPages(urls, {
    createPage: (url, index) => {built++; return {url, index};},
    isCurrent: () => true, yieldControl: async () => {sizes.push(built);},
  });
  assert.equal(PAGE_BUILD_BATCH, 80);
  assert.deepEqual(sizes, [80, 160, 240, 320, 400, 480]);
  assert.deepEqual(pages.map(page => page.url), urls);
  assert.equal(pages.at(-1).index, 499);
});

test('closing while a batch yields discards the private result and builds no later pages', async () => {
  const scopes = createChapterScope(), current = scopes.start();
  let resume, built = 0;
  const promise = buildReaderPages(Array(500).fill('image'), {
    createPage: () => ++built, isCurrent: () => scopes.isCurrent(current),
    yieldControl: () => new Promise(resolve => {resume = resolve;}),
  });
  assert.equal(built, 80);
  scopes.stop(); resume();
  assert.equal(await promise, null);
  assert.equal(built, 80);
});

test('switching chapters during construction cannot commit old nodes after the new chapter finishes', async () => {
  const scopes = createChapterScope(), old = scopes.start();
  let resume;
  const pending = buildReaderPages(Array(180).fill('old'), {
    createPage: url => url, isCurrent: () => scopes.isCurrent(old),
    yieldControl: () => new Promise(resolve => {resume = resolve;}),
  });
  const fresh = scopes.start();
  const pages = await buildReaderPages(['new-1', 'new-2'], {createPage: url => url, isCurrent: () => scopes.isCurrent(fresh)});
  resume();
  assert.deepEqual(pages, ['new-1', 'new-2']);
  assert.equal(await pending, null);
});

test('a cancelled generation does not start building or expose an empty stale result', async () => {
  let built = 0;
  assert.equal(await buildReaderPages(['one'], {createPage: () => ++built, isCurrent: () => false}), null);
  assert.equal(await buildReaderPages([], {createPage: () => ++built, isCurrent: () => false}), null);
  assert.equal(built, 0);
});
