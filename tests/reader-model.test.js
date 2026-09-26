import test from 'node:test';
import assert from 'node:assert/strict';
import {MAX_IMAGE_REQUESTS, clampPage, clampOffset, restorePosition, normalizeRatio, imageCandidates, prefetchPageCount, progressSnapshot, createChapterScope, createReadingFeedback} from '../web/reader-model.js';

test('old shelves with only a page restore, and chapter mismatch never borrows a position', () => {
  assert.deepEqual(restorePosition({chapterUrl: 'old', page: 42}, 'old', 209), {page: 42, pageOffset: 0});
  assert.deepEqual(restorePosition({chapterUrl: 'old', page: 42, pageOffset: 0.61}, 'old', 209), {page: 42, pageOffset: 0.61});
  assert.deepEqual(restorePosition({chapterUrl: 'old', page: 42, pageOffset: 0.61}, 'new', 209), {page: 0, pageOffset: 0});
});

test('damaged imports and changed page counts clamp to a valid position', () => {
  assert.deepEqual(restorePosition({chapterUrl: 'a', page: 999, pageOffset: 20}, 'a', 12), {page: 11, pageOffset: 1});
  assert.deepEqual(restorePosition({chapterUrl: 'a', page: -2, pageOffset: 'bad'}, 'a', 12), {page: 0, pageOffset: 0});
  assert.equal(clampPage(Infinity, 12), 11);
  assert.equal(clampPage(4, 0), 0);
  assert.equal(clampOffset(Infinity), 0);
});

test('restoring page 180 queues that page first without loading the preceding 179 pages', () => {
  const candidates = imageCandidates({total: 209, page: 179, first: 179, last: 179});
  assert.equal(candidates[0], 179);
  assert.equal(MAX_IMAGE_REQUESTS, 4);
  assert.ok(candidates.length <= 8);
  assert.ok(candidates.every(page => page >= 178 && page <= 185));
  assert.equal(new Set(candidates).size, candidates.length);
});

test('preload settings preserve quota and data-saving defaults and bound forward requests', () => {
  assert.equal(prefetchPageCount({siteId: 'komiic'}), 0);
  assert.equal(prefetchPageCount({saveData: true}), 0);
  assert.equal(prefetchPageCount({prefetch: 'off'}), 0);
  assert.equal(prefetchPageCount({mode: 'paged'}), 3);
  assert.equal(prefetchPageCount({}), 6);
  assert.equal(prefetchPageCount({prefetch: 'more', siteId: 'komiic'}), 10);
  assert.ok(imageCandidates({total: 1000, page: 500, ahead: 10000}).length <= 12);
});

test('short images visible together precede background prefetch and chapter edges stay in range', () => {
  const candidates = imageCandidates({total: 209, page: 22, first: 22, last: 26});
  assert.deepEqual(new Set(candidates.slice(0, 5)), new Set([22, 23, 24, 25, 26]));
  assert.deepEqual(imageCandidates({total: 1, page: 0}), [0]);
  assert.deepEqual(imageCandidates({total: 0, page: 0}), []);
  assert.ok(imageCandidates({total: 3, page: 2}).every(page => page >= 0 && page < 3));
});

test('failures and incomplete restoration cannot replace the saved reading point', () => {
  const state = {chapter: {url: 'chapter-a', name: '第 12 话'}, total: 30, position: {page: 18, pageOffset: 0.4}, restoring: false, loaded: true};
  assert.deepEqual(progressSnapshot(state), {chapterUrl: 'chapter-a', chapterName: '第 12 话', page: 18, pageOffset: 0.4, totalPages: 30});
  assert.equal(progressSnapshot({...state, restoring: true}), null);
  assert.equal(progressSnapshot({...state, loaded: false}), null);
  assert.equal(progressSnapshot({...state, total: 0}), null);
  assert.equal(progressSnapshot({...state, chapter: {name: '无链接'}}), null);
});

test('a delayed chapter result remains invalid even if an adapter ignores cancellation', async () => {
  const sessions = createChapterScope();
  const old = sessions.start();
  let finish;
  const delayed = new Promise(resolve => {finish = resolve;});
  const committed = [];
  const operation = delayed.then(() => {if (sessions.isCurrent(old)) committed.push('old');});
  const current = sessions.start();
  assert.equal(old.controller.signal.aborted, true);
  finish(); await operation;
  assert.deepEqual(committed, []);
  assert.equal(sessions.isCurrent(current), true);
  sessions.stop();
  assert.equal(current.controller.signal.aborted, true);
  assert.equal(sessions.isCurrent(current), false);
});

test('layout ratios accept wide and long manga pages but reject corrupt size caches', () => {
  assert.equal(normalizeRatio(0.7), 0.7);
  assert.equal(normalizeRatio(12), 12);
  for (const value of [0, -1, 1000, Infinity, null, 'bad']) assert.equal(normalizeRatio(value), 1.42);
});

test('reading quality preserves a failure and reports recovery only for the failed page once', () => {
  const failures = [], recoveries = [];
  const feedback = createReadingFeedback({sessionId: 'reading-a', onFailure: detail => failures.push(detail), onRecovery: detail => recoveries.push(detail)});
  const bad = {chapterUrl: 'chapter-1', pageIndex: 4, kind: 'image_decode'};
  assert.equal(feedback.recover({...bad, kind: 'image_decoded'}), false);
  assert.equal(feedback.fail(bad), true);
  assert.equal(feedback.fail(bad), false);
  assert.equal(feedback.fail({...bad, kind: 'image_network'}), false);
  assert.equal(feedback.recover({...bad, chapterUrl: 'chapter-2'}), false);
  assert.equal(feedback.recover({...bad, pageIndex: 5}), false);
  assert.equal(feedback.recover({...bad, kind: 'image_decoded'}), true);
  assert.equal(feedback.recover({...bad, kind: 'image_decoded'}), false);
  assert.deepEqual(failures, [{...bad, sessionId: 'reading-a'}]);
  assert.deepEqual(recoveries, [{...bad, kind: 'image_decoded', sessionId: 'reading-a', failureKind: 'image_decode'}]);
});

test('chapter failure only recovers after a page in that chapter decodes, without erasing the failure', () => {
  const events = [];
  const feedback = createReadingFeedback({sessionId: 'same-on-retry', onFailure: detail => events.push(['failure', detail]), onRecovery: detail => events.push(['recovery', detail])});
  feedback.fail({chapterUrl: 'chapter-a', pageIndex: null, kind: 'chapter'});
  assert.equal(feedback.recover({chapterUrl: 'chapter-b', pageIndex: 0, kind: 'image_decoded'}), false);
  feedback.fail({chapterUrl: 'chapter-a', pageIndex: null, kind: 'chapter'});
  feedback.recover({chapterUrl: 'chapter-a', pageIndex: 2, kind: 'image_decoded'});
  assert.deepEqual(events.map(([kind]) => kind), ['failure', 'recovery']);
  assert.equal(events[0][1].sessionId, events[1][1].sessionId);
  assert.equal(events[1][1].failureKind, 'chapter');
});
