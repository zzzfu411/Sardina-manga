import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeRecommendations, isRecommendationOnShelf, recommendationReason, createRecommendationsModel,
  RECOMMENDATION_SOFT_TTL, RECOMMENDATION_POOL_LIMIT} from '../web/recommendations-model.js';
import {createRecommendationFeedback, RECOMMENDATION_FEEDBACK_KEY, FEEDBACK_LIMITS} from '../web/recommendations-feedback.js';
import {buildRecommendationProfile, rankRecommendations, preferenceWeight} from '../web/recommendations-ranking.js';

const book = (id, extra = {}) => ({siteId: 'manben', siteName: '漫本', title: `作品${id}`,
  detailUrl: `https://example.test/comic/${id}`, coverUrl: `https://images.example.test/${id}.jpg`, recommendationKind: 'popular', ...extra});
const books = (start, count) => Array.from({length: count}, (_, index) => book(start + index));
const payload = (items = books(0, 24), extra = {}) => ({items, fetchedAt: '2026-09-22T08:30:00Z',
  origins: [{siteId: 'manben', siteName: '漫本', kind: 'popular', sourceUrl: 'https://example.test/rank/', fetchedAt: '2026-09-22T08:30:00Z'}],
  warnings: [], nextBatch: null, ...extra});
function memoryStorage() {
  const data = new Map();
  return {data, getItem: key => data.get(key) || null, setItem: (key, value) => data.set(key, value)};
}
function harness(initialShelf = [], options = {}) {
  const calls = [], changes = []; let shelf = initialShelf, time = 2000000000000;
  const storage = options.storage || memoryStorage();
  const feedback = createRecommendationFeedback({storage, now: () => time});
  const model = createRecommendationsModel({getShelf: () => shelf, feedback, now: () => time,
    api: (path, body, signal) => new Promise((resolve, reject) => calls.push({path, body, signal, resolve, reject})),
    onChange: state => changes.push(state)});
  return {model, calls, changes, feedback, storage, advance(ms) {time += ms;}, setShelf(value) {shelf = value;}};
}
async function ready(data = payload(), shelf = [], options = {}) {
  const h = harness(shelf, options), opening = h.model.show(); h.calls[0].resolve(data); await opening; return h;
}
const tick = () => new Promise(resolve => setImmediate(resolve));
const keys = h => h.model.getState().cards.map(card => card.key);

test('strict known-author identity groups variants, retaining editions, conflicting and unknown authors', () => {
  const result = normalizeRecommendations(payload([
    book('a', {title: '三月的獅子', author: '羽海野千花'}), book('b', {title: '３月的狮子', author: '羽海野千花'}),
    book('c', {title: '3月的狮子 全彩版', author: '羽海野千花'}),
    book('d', {title: '逆光', author: '作者甲'}), book('e', {title: '逆光', author: '作者乙'}),
    book('f', {title: '逆光'}), book('g', {title: '逆光'}), book('a', {title: '三月的獅子'}),
  ]));
  assert.equal(result.candidates.length, 6);
  assert.equal(result.candidates[0].variants.length, 2);
  assert.equal(result.candidates[0].book.author, '羽海野千花');
  assert.equal(new Set(result.candidates.map(candidate => candidate.key)).size, 6);
});

test('shelf excludes stable entries and known works but never bridges missing author identities', () => {
  const saved = book('saved', {title: '三月的獅子', author: '羽海野千花'});
  assert.equal(isRecommendationOnShelf(book('other', {siteId: 'other', title: '3月的狮子'}), [saved]), false);
  assert.equal(isRecommendationOnShelf(book('other', {siteId: 'other', title: '3月的狮子', author: '羽海野千花'}), [saved]), true);
  assert.equal(isRecommendationOnShelf({...saved, title: '改过书名'}, [saved]), true);
  assert.equal(isRecommendationOnShelf(book('other', {title: '3月的狮子', author: '其他作者'}), [saved]), false);
  assert.equal(isRecommendationOnShelf(book('other', {title: '3月的狮子全彩版', author: saved.author}), [saved]), false);
});

test('validation admits actionable covered books and real source reasons only', () => {
  const data = normalizeRecommendations(payload([book(1, {coverUrl: ''}), book(2, {coverUrl: 'javascript:alert(1)'}),
    book(3, {recommendationKind: 'ai'}), book(4, {detailUrl: 'https://name:secret@example.test/4'}), book(5)],
  {warnings: ['部分来源暂不可用', '部分来源暂不可用'], origins: [{siteId: 'manben', siteName: '漫本', kind: 'popular', sourceUrl: 'javascript:alert(1)'}]}));
  assert.equal(data.candidates.length, 1);
  assert.equal(recommendationReason(data.candidates[0].book), '漫本热门');
  assert.equal(recommendationReason(book(6, {recommendationKind: 'latest'})), '漫本最近更新');
  assert.equal(recommendationReason(book(7, {recommendationKind: 'ai'})), '');
  assert.equal(data.origins[0].sourceUrl, ''); assert.deepEqual(data.warnings, ['部分来源暂不可用']);
  assert.throws(() => normalizeRecommendations({}), /重试/);
});

test('two batches exhaust a 24 pool without silently cycling back to the first', async () => {
  const h = await ready(), first = keys(h);
  h.model.nextBatch(); const second = keys(h);
  assert.equal(first.length, 12); assert.equal(second.length, 12);
  assert.equal(second.some(key => first.includes(key)), false);
  assert.equal(h.model.getState().canNext, false);
  h.model.hide(); await h.model.show(); assert.deepEqual(keys(h), second);
  h.model.nextBatch(); assert.deepEqual(keys(h), second);
  assert.equal(h.calls.length, 1);
});

test('exhaustion requests the next source batch and never repeats either preceding batch', async () => {
  const h = await ready(payload(books(0, 24), {nextBatch: 1})), first = keys(h);
  h.model.nextBatch(); const second = keys(h), supplement = h.model.nextBatch();
  assert.equal(h.calls.length, 2); assert.equal(h.calls[1].path, '/api/recommendations?batch=1');
  h.calls[1].resolve(payload(books(24, 36), {nextBatch: 2})); await supplement;
  assert.equal(keys(h).length, 12);
  assert.equal(keys(h).some(key => [...first, ...second].includes(key)), false);
});

test('small filtered pools are supplemented; a user action performs at most two list-batch requests', async () => {
  const h = harness(books(0, 23)), opening = h.model.show();
  h.calls[0].resolve(payload(books(0, 24), {nextBatch: 1})); await tick();
  assert.equal(h.calls[1].path, '/api/recommendations?batch=1');
  h.calls[1].resolve(payload(books(24, 24), {nextBatch: 2})); await opening;
  assert.equal(keys(h).length, 12);
  assert.equal(h.model.getState().cards.some(card => isRecommendationOnShelf(card.book, books(0, 23))), false);
  const failed = harness(), loading = failed.model.show();
  failed.calls[0].resolve(payload([], {warnings: ['源暂不可用'], nextBatch: 1})); await tick();
  failed.calls[1].resolve(payload([], {warnings: ['另一源暂不可用'], nextBatch: 2})); await loading;
  assert.equal(failed.calls.length, 2); assert.equal(failed.model.getState().hasMore, true);
  assert.equal(failed.model.getState().cards.length, 0);
});

test('candidate memory is bounded and saved candidates cannot crowd out supplementation', async () => {
  const h = harness(books(0, 200)), loading = h.model.show();
  h.calls[0].resolve(payload(books(0, 200), {nextBatch: 1})); await tick();
  h.calls[1].resolve(payload(books(200, 200))); await loading;
  assert.equal(h.model.getState().poolSize, RECOMMENDATION_POOL_LIMIT);
  assert.equal(keys(h).length, 12);
  assert.equal(h.model.getState().cards.every(card => Number(card.book.title.slice(2)) >= 200), true);
});

test('current cards reflect shelf changes immediately; subsequent batches exclude saved books', async () => {
  const h = await ready(), previous = keys(h), saved = h.model.getState().cards[0].book;
  h.setShelf([saved]); h.model.shelfChanged();
  assert.deepEqual(keys(h), previous); assert.equal(h.model.getState().cards[0].onShelf, true);
  h.model.nextBatch(); assert.equal(h.model.getState().cards.some(card => sameUrl(card.book, saved)), false);
  const small = await ready(payload([book(1), book(2)])); assert.equal(keys(small).length, 2);
  const allSaved = await ready(payload([book(1)]), [book(1)]);
  assert.equal(keys(allSaved).length, 0); assert.equal(allSaved.model.getState().emptyReason, 'shelf');
});
const sameUrl = (a, b) => a.detailUrl === b.detailUrl;

test('explicit favorite and content evidence alter ordering; trial reads have a weaker signal', () => {
  const candidates = normalizeRecommendations(payload([book('neutral'), book('author', {author: '羽海野千花'}),
    book('sports', {description: '围绕将棋与青春的日常故事'})])).candidates;
  const cold = rankRecommendations(candidates);
  assert.equal(cold[0].book.title, '作品neutral');
  const authorProfile = buildRecommendationProfile([book('favorite', {title: '三月的狮子', author: '羽海野千花', favorite: true})]);
  const personal = rankRecommendations(candidates, {profile: authorProfile});
  assert.equal(personal[0].book.title, '作品author'); assert.match(personal[0].reason, /收藏的《三月的狮子》作者相同.*漫本热门/);
  const content = rankRecommendations(candidates, {profile: buildRecommendationProfile([book('saved', {description: '将棋竞技', favorite: true})])});
  assert.equal(content[0].book.title, '作品sports'); assert.match(content[0].reason, /运动.*线索/);
  assert.ok(preferenceWeight({favorite: true}) > preferenceWeight({}) && preferenceWeight({}) > preferenceWeight({favorite: false}));
  const feedback = createRecommendationFeedback({storage: memoryStorage()}); feedback.setPersonalization(false);
  assert.equal(rankRecommendations(candidates, {profile: authorProfile, feedback})[0].book.title, '作品neutral');
});

test('unseen items precede previously exposed favorites and exposure survives reload', () => {
  const storage = memoryStorage(), feedback = createRecommendationFeedback({storage});
  const favorite = book('favorite', {author: '同作者'}), other = book('new');
  feedback.markExposed(favorite);
  const restored = createRecommendationFeedback({storage});
  const ranked = rankRecommendations(normalizeRecommendations(payload([favorite, other])).candidates,
    {feedback: restored, profile: buildRecommendationProfile([book('saved', {author: '同作者', favorite: true})])});
  assert.equal(ranked[0].book.title, '作品new');
  assert.equal(restored.snapshot().metrics.impressions, 1);
});

test('negative feedback persists locally, can be undone, and never removes unknown same-title entries', async () => {
  const h = await ready(), card = h.model.getState().cards[0];
  h.model.dismiss(card.id);
  assert.equal(keys(h).includes(card.key), false); assert.equal(h.model.getState().canUndo, true);
  const reloaded = await ready(payload(), [], {storage: h.storage});
  assert.equal(keys(reloaded).includes(card.key), false);
  h.model.undoDismiss(); assert.equal(keys(h)[0], card.key); assert.equal(h.model.getState().canUndo, false);
  const feedback = createRecommendationFeedback({storage: memoryStorage()});
  feedback.dismiss(book('a', {title: '逆光'}));
  assert.equal(feedback.isDismissed(book('b', {title: '逆光'})), false);
  feedback.clear(); assert.equal(feedback.snapshot().dismissed.length, 0);
});

test('feedback is bounded, clearable and storage failure remains visible without erasing unreadable records', () => {
  const storage = memoryStorage(), feedback = createRecommendationFeedback({storage});
  for (let index = 0; index < 650; index++) {feedback.dismiss(book(index)); feedback.markExposed(book(index));}
  assert.equal(feedback.snapshot().dismissed.length, FEEDBACK_LIMITS.dismissed);
  assert.equal(feedback.snapshot().exposures.length, FEEDBACK_LIMITS.exposures);
  feedback.clear(); assert.equal(feedback.snapshot().exposures.length, 0);
  const broken = createRecommendationFeedback({storage: {getItem: () => null, setItem() {throw new Error('quota');}}});
  broken.dismiss(book(1)); assert.equal(broken.isDismissed(book(1)), true); assert.match(broken.snapshot().warning, /无法保存/);
  storage.setItem(RECOMMENDATION_FEEDBACK_KEY, '{broken');
  const corrupt = createRecommendationFeedback({storage}); corrupt.markExposed(book(1));
  assert.equal(storage.getItem(RECOMMENDATION_FEEDBACK_KEY), '{broken'); assert.match(corrupt.snapshot().warning, /原记录已保留/);
});

test('cover failure tries one spare, then allows isolated retry; late events cannot mutate a newer card', async () => {
  const h = await ready(), first = h.model.getState().cards[0];
  h.model.coverFailed(first.id, first.revision); const replacement = h.model.getState().cards[0];
  assert.notEqual(replacement.key, first.key); assert.equal(replacement.replacements, 1);
  h.model.coverFailed(replacement.id, replacement.revision); const failed = h.model.getState().cards[0];
  assert.equal(failed.coverStatus, 'error');
  const others = h.model.getState().cards.slice(1).map(card => [card.key, card.revision]);
  h.model.retryCover(failed.id); const retry = h.model.getState().cards[0];
  assert.equal(retry.retry, 1); assert.equal(retry.coverStatus, 'loading'); assert.notEqual(retry.revision, failed.revision);
  h.model.coverFailed(first.id, first.revision); h.model.coverLoaded(first.id, first.revision);
  assert.equal(h.model.getState().cards[0].revision, retry.revision);
  assert.deepEqual(h.model.getState().cards.slice(1).map(card => [card.key, card.revision]), others);
  h.model.coverLoaded(retry.id, retry.revision); assert.equal(h.model.getState().cards[0].coverStatus, 'ready');
  assert.equal(h.calls.length, 1);
});

test('known-work cover alternatives retain the work instead of discarding its other source', async () => {
  const h = await ready(payload([book('a', {title: '同一作品', author: '作者'}), book('b', {siteId: 'other', siteName: '另一个源', title: '同一作品', author: '作者'})]));
  const original = h.model.getState().cards[0]; h.model.coverFailed(original.id, original.revision);
  const replacement = h.model.getState().cards[0];
  assert.equal(replacement.key, original.key); assert.equal(replacement.book.siteId, 'other'); assert.equal(replacement.coverStatus, 'loading');
  assert.match(replacement.reason, /另一个源热门/);
});

test('a cover without a spare remains retryable and hidden failures do not replace the work', async () => {
  const one = await ready(payload([book(1)])), card = one.model.getState().cards[0];
  one.model.coverFailed(card.id, card.revision); assert.equal(one.model.getState().cards[0].coverStatus, 'error');
  assert.equal(one.model.getState().cards[0].canReplace, false);
  const h = await ready(), hidden = h.model.getState().cards[0];
  h.model.hide(); h.model.coverFailed(hidden.id, hidden.revision);
  assert.equal(h.model.getState().cards[0].key, hidden.key); assert.equal(h.model.getState().cards[0].coverStatus, 'error');
});

test('hiding aborts requests and ignores late results; explicit refresh preserves GET protocol', async () => {
  const h = harness(), first = h.model.show(); h.model.hide(); assert.equal(h.calls[0].signal.aborted, true);
  const second = h.model.show(); h.calls[1].resolve(payload([book('new')])); await second;
  h.calls[0].resolve(payload([book('old')])); await first; assert.equal(h.model.getState().cards[0].book.title, '作品new');
  const refreshing = h.model.refresh(); assert.equal(h.calls.at(-1).path, '/api/recommendations?refresh=1');
  assert.equal(h.calls.at(-1).body, null); h.calls.at(-1).resolve(payload()); await refreshing;
  assert.equal(keys(h).length, 12);
});

test('failed refresh and supplementation retain usable cards and their timestamp', async () => {
  const h = await ready(), previous = keys(h), at = h.model.getState().fetchedAt;
  const refreshing = h.model.refresh(); h.calls.at(-1).reject(new Error('来源暂不可用')); await refreshing;
  assert.equal(h.model.getState().phase, 'error'); assert.deepEqual(keys(h), previous); assert.equal(h.model.getState().fetchedAt, at);
  const empty = harness(), opening = empty.model.show(); empty.calls[0].reject(new Error('加载失败')); await opening;
  assert.equal(empty.model.getState().phase, 'error'); assert.equal(keys(empty).length, 0);
});

test('explicit refresh can recover an exhausted card using the refreshed real cover URL', async () => {
  const h = await ready(payload([book(1)])), old = h.model.getState().cards[0];
  h.model.coverFailed(old.id, old.revision);
  const refreshing = h.model.refresh();
  h.calls[1].resolve(payload([book(1, {coverUrl: 'https://images.example.test/new-cover.jpg'})])); await refreshing;
  const current = h.model.getState().cards[0];
  assert.equal(current.coverStatus, 'loading'); assert.notEqual(current.revision, old.revision);
  assert.equal(current.book.coverUrl, 'https://images.example.test/new-cover.jpg');
});

test('soft TTL revalidates without replacing current cards or losing the remaining source rotation', async () => {
  const h = await ready(payload(books(0, 24), {nextBatch: 1})); h.model.nextBatch();
  const more = h.model.nextBatch(); h.calls[1].resolve(payload(books(24, 24), {nextBatch: 2})); await more;
  const current = keys(h); h.model.hide(); h.advance(RECOMMENDATION_SOFT_TTL + 1);
  const showing = h.model.show(); assert.equal(h.calls.length, 3); assert.deepEqual(keys(h), current);
  h.calls[2].resolve(payload(books(0, 24), {nextBatch: 1})); await showing;
  assert.deepEqual(keys(h), current); assert.equal(h.model.getState().hasMore, true);
  h.model.nextBatch(); const supplement = h.model.nextBatch();
  assert.equal(h.calls.at(-1).path, '/api/recommendations?batch=2');
  h.calls.at(-1).resolve(payload(books(48, 24))); await supplement;
});
