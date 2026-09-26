import test from 'node:test';
import assert from 'node:assert/strict';
import {createRecommendationFeedback as createFeedback, RECOMMENDATION_FEEDBACK_KEY} from '../web/recommendations-feedback.js';
import {createRecommendationsModel} from '../web/recommendations-model.js';

const createRecommendationFeedback = options => createFeedback({locks: null, ...options});
const book = (id, extra = {}) => ({siteId: 'manben', siteName: '漫本', title: `作品${id}`, detailUrl: `https://www.manben.com/${id}/`, coverUrl: `https://images.example.test/${id}.jpg`, recommendationKind: 'popular', ...extra});
const storage = () => {
  const values = new Map();
  return {getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, String(value))};
};
const events = () => {
  const handlers = new Set();
  return {addEventListener: (_event, listener) => handlers.add(listener), removeEventListener: (_event, listener) => handlers.delete(listener),
    fire: store => {for (const listener of handlers) listener({key: RECOMMENDATION_FEEDBACK_KEY, storageArea: store});}};
};
const serialLocks = () => {
  let pending = Promise.resolve();
  return {request: (_key, action) => pending = pending.catch(() => {}).then(action)};
};

test('ordinary exposure preserves another tab explicit preferences and exclusions', () => {
  const shared = storage(), a = createRecommendationFeedback({storage: shared}), b = createRecommendationFeedback({storage: shared});
  a.dismiss(book(1)); a.setPersonalization(false); b.markExposed(book(2));
  const restored = createRecommendationFeedback({storage: shared});
  assert.equal(restored.snapshot().personalization, false); assert.equal(restored.isDismissed(book(1)), true);
  assert.equal(restored.snapshot().metrics.impressions, 1); assert.equal(restored.snapshot().metrics.dismissals, 1);
});

test('concurrent operation batches preserve independent counts and deduplicate shared exposure in the lock', async () => {
  const shared = storage(), locks = serialLocks();
  const a = createRecommendationFeedback({storage: shared, locks}), b = createRecommendationFeedback({storage: shared, locks});
  a.markExposed(book(1)); b.markExposed(book(1)); a.opened(book(1)); b.opened(book(2));
  a.setPersonalization(false); b.dismiss(book(3));
  await Promise.all([a.settled(), b.settled()]); a.sync(); b.sync();
  assert.deepEqual(a.snapshot(), b.snapshot());
  assert.equal(a.snapshot().metrics.impressions, 1); assert.equal(a.snapshot().metrics.opens, 2);
  assert.equal(a.snapshot().personalization, false); assert.equal(a.isDismissed(book(3)), true);
});

test('undo tombstones reject an older delayed exclusion, then a new explicit dismissal works', async () => {
  const shared = storage(); let release;
  const a = createRecommendationFeedback({storage: shared, now: () => 100, actorId: 'a', locks: {request: (_name, action) => new Promise(resolve => {release = () => resolve(action());})}});
  const b = createRecommendationFeedback({storage: shared, now: () => 200, actorId: 'b', locks: null});
  a.dismiss(book(1)); await new Promise(resolve => setImmediate(resolve)); b.undo(book(1));
  release(); await a.settled(); b.sync(); assert.equal(b.isDismissed(book(1)), false);
  a.dismiss(book(1)); await new Promise(resolve => setImmediate(resolve)); release(); await a.settled(); b.sync();
  assert.equal(b.isDismissed(book(1)), true);
});

test('clear generation drops pending old metrics and exclusions and expires old reading sessions', async () => {
  const shared = storage(); let release;
  const a = createRecommendationFeedback({storage: shared, now: () => 100, locks: {request: (_name, action) => new Promise(resolve => {release = () => resolve(action());})}});
  const b = createRecommendationFeedback({storage: shared, now: () => 200, locks: null});
  b.setPersonalization(false);
  a.opened(book(1)); a.dismiss(book(2)); await new Promise(resolve => setImmediate(resolve));
  b.clear(); release(); await new Promise(resolve => setImmediate(resolve)); release(); await a.settled();
  a.recordRead(book(1), {chapterUrl: 'chapter', page: 0}); a.recordFailure(book(1));
  const restored = createRecommendationFeedback({storage: shared});
  assert.equal(restored.snapshot().dismissed.length, 0); assert.equal(restored.snapshot().personalization, false);
  assert.equal(Object.values(restored.snapshot().metrics).reduce((a, b) => a + b, 0), 0);
  b.markExposed(book(3)); assert.equal(b.snapshot().metrics.impressions, 1);
});

test('a clear also invalidates live sessions after a cross-tab storage event', () => {
  const shared = storage(), hub = events();
  const a = createRecommendationFeedback({storage: shared, events: hub}), b = createRecommendationFeedback({storage: shared, events: hub});
  a.opened(book(1)); b.clear(); hub.fire(shared);
  a.recordRead(book(1), {chapterUrl: 'chapter', page: 0}); a.recordFailure(book(1));
  assert.equal(a.snapshot().metrics.readingStarts, 0); assert.equal(a.snapshot().metrics.openFailures, 0);
  a.destroy(); b.destroy();
});

test('v1 migration keeps explicit choices and all previous counters', () => {
  const shared = storage(); shared.setItem(RECOMMENDATION_FEEDBACK_KEY, JSON.stringify({version: 1, personalization: false,
    dismissed: [{book: book(1), at: 100}], exposures: [{key: 'old', entryKey: 'old', at: 100}], metrics: {impressions: 8, opens: 4, dismissals: 3, readingStarts: 2, continuedReads: 1, openFailures: 1}}));
  const feedback = createRecommendationFeedback({storage: shared, now: () => 200}); feedback.markExposed(book(2));
  const restored = createRecommendationFeedback({storage: shared, now: () => 200});
  assert.equal(restored.isDismissed(book(1)), true); assert.equal(restored.snapshot().personalization, false);
  assert.deepEqual(restored.snapshot().metrics, {impressions: 9, opens: 4, dismissals: 3, readingStarts: 2, continuedReads: 1, openFailures: 1, openRecoveries: 0});
  assert.equal(JSON.parse(shared.getItem(RECOMMENDATION_FEEDBACK_KEY)).version, 2);
});

test('decode failure and matching recovery remain separate and are counted once per recommended open', () => {
  const feedback = createRecommendationFeedback({storage: storage()}), target = book(1);
  feedback.opened(target);
  for (let index = 0; index < 3; index++) feedback.recordFailure(target, {sessionId: 'first-chapter', kind: 'decode'});
  feedback.recordRecovery(target, {sessionId: 'unrelated'}); assert.equal(feedback.snapshot().metrics.openRecoveries, 0);
  for (let index = 0; index < 3; index++) feedback.recordRecovery(target, {sessionId: 'first-chapter'});
  feedback.recordRead(target, {chapterUrl: 'chapter', page: 0});
  assert.equal(feedback.snapshot().metrics.openFailures, 1); assert.equal(feedback.snapshot().metrics.openRecoveries, 1);
  assert.equal(feedback.snapshot().metrics.readingStarts, 1);
  feedback.opened(target); feedback.recordRecovery(target, {sessionId: 'first-chapter'});
  assert.equal(feedback.snapshot().metrics.openRecoveries, 1);
});

test('storage events update the visible recommendation model and remove externally dismissed cards', async () => {
  const shared = storage(), hub = events(), a = createRecommendationFeedback({storage: shared, events: hub}), b = createRecommendationFeedback({storage: shared});
  let renders = 0;
  const model = createRecommendationsModel({api: async () => ({items: [book(1), book(2)]}), feedback: a, onChange: () => renders++});
  await model.show(); const before = renders;
  b.setPersonalization(false); b.dismiss(book(1)); hub.fire(shared);
  assert.equal(model.getState().personalization, false); assert.equal(model.getState().dismissedCount, 1);
  assert.ok(model.getState().cards.every(card => card.book.title !== book(1).title)); assert.ok(renders > before);
  b.undo(book(1)); hub.fire(shared); assert.equal(model.getState().dismissedCount, 0);
  model.destroy();
});

test('external dismissal discovered by a local event updates cards before the storage event arrives', async () => {
  const shared = storage(), a = createRecommendationFeedback({storage: shared}), b = createRecommendationFeedback({storage: shared});
  const model = createRecommendationsModel({api: async () => ({items: [book(1), book(2)]}), feedback: a});
  await model.show(); b.dismiss(book(1)); a.markExposed(book(2));
  assert.ok(model.getState().cards.every(card => card.book.title !== book(1).title));
  model.destroy();
});

test('a recent reader failure stays penalized after a later successful reader or cover request', async () => {
  const {rankRecommendations} = await import('../web/recommendations-ranking.js');
  const {normalizeRecommendations} = await import('../web/recommendations-model.js');
  const now = 2000000000000;
  const candidates = normalizeRecommendations({items: [book(1, {readingHealth: {
    image: {status: 'ok', checkedAt: new Date(now).toISOString(), lastFailureAt: new Date(now - 60000).toISOString()},
    coverImage: {status: 'ok', checkedAt: new Date(now).toISOString()}}}), book(2)]}).candidates;
  assert.equal(rankRecommendations(candidates, {now})[0].book.title, book(2).title);
  assert.equal(rankRecommendations(candidates, {now: now + 16 * 60000})[0].book.title, book(1).title);
  candidates[0].book.readingHealth = {coverImage: {status: 'error', lastFailureAt: new Date(now).toISOString()}};
  assert.equal(rankRecommendations(candidates, {now})[0].book.title, book(1).title);
});
