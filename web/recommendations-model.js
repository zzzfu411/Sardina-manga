import {sameWork, sourceEntryKey, workIdentity} from './book-identity.js';
import {createRecommendationMetadata} from './recommendations-metadata.js';
import {createRecommendationFeedback} from './recommendations-feedback.js';
import {buildRecommendationProfile, originReason, rankRecommendations} from './recommendations-ranking.js';

export const RECOMMENDATION_BATCH_SIZE = 12;
export const RECOMMENDATION_POOL_LIMIT = 200;
export const RECOMMENDATION_SOFT_TTL = 5 * 60000;
const MAX_SUPPLEMENT_REQUESTS = 2;
const text = value => typeof value === 'string' ? value.trim() : '';
const validId = value => typeof value === 'string' && /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$/.test(value);
const validKind = value => value === 'popular' || value === 'latest';

export function recommendationUrl(value) {
  try {
    const url = new URL(value);
    return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : '';
  } catch {return '';}
}
export const recommendationReason = originReason;
export const isRecommendationOnShelf = (book, shelf = []) => shelf.some(saved => sameWork(book, saved));

export function normalizeRecommendations(payload) {
  if (!payload || !Array.isArray(payload.items)) throw new Error('暂时无法读取推荐，请重试');
  const entries = new Map();
  for (const item of payload.items.slice(0, 1000)) {
    if (!item || !validId(item.siteId) || !text(item.siteName) || !text(item.title) || !validKind(item.recommendationKind)) continue;
    const detailUrl = recommendationUrl(item.detailUrl), coverUrl = recommendationUrl(item.coverUrl);
    if (!detailUrl || !coverUrl) continue;
    const book = {...item, detailUrl, coverUrl, title: text(item.title), siteName: text(item.siteName), author: text(item.author)};
    const key = sourceEntryKey(book), previous = entries.get(key);
    if (!previous) entries.set(key, book);
    else entries.set(key, {...previous, ...book, author: book.author || previous.author,
      description: text(book.description) || text(previous.description)});
  }
  const candidates = [], identities = new Map();
  for (const book of entries.values()) {
    // Unknown authors retain the source entry identity. Known works can share a
    // card while keeping every source variant available for cover recovery.
    const key = workIdentity(book), previous = identities.get(key);
    if (previous && sameWork(previous.book, book)) previous.variants.push(book);
    else {const candidate = {key, book, variants: [book]}; candidates.push(candidate); identities.set(key, candidate);}
  }
  const origins = [], originKeys = new Set();
  for (const origin of Array.isArray(payload.origins) ? payload.origins : []) {
    if (!validId(origin?.siteId) || !text(origin.siteName) || !validKind(origin.kind)) continue;
    const key = `${origin.siteId}:${origin.kind}:${text(origin.period)}`;
    if (originKeys.has(key)) continue;
    originKeys.add(key);
    origins.push({...origin, siteName: text(origin.siteName), sourceUrl: recommendationUrl(origin.sourceUrl), fetchedAt: text(origin.fetchedAt)});
  }
  const nextBatch = Number.isInteger(payload.nextBatch) && payload.nextBatch >= 0 && payload.nextBatch <= 31 ? payload.nextBatch : null;
  return {candidates, origins, fetchedAt: text(payload.fetchedAt), nextBatch,
    warnings: [...new Set((Array.isArray(payload.warnings) ? payload.warnings : []).map(text).filter(Boolean))]};
}

export function createRecommendationsModel({api, getShelf = () => [], onChange = () => {}, now = Date.now,
  feedback = createRecommendationFeedback({now}), metadata = createRecommendationMetadata({now})}) {
  let state = {visible: false, phase: 'idle', cards: [], fetchedAt: '', origins: [], warnings: [], error: ''};
  let pool = [], loaded = false, loadedAt = 0, nextBatch = null, revision = 0, request = null, generation = 0;
  let shelfCache = null, undo = null, enrichment = null;
  const metadataTried = new Set();
  const failed = new Set(), served = new Set(), fetchedBatches = new Set();
  function shelfData() {
    let books;
    try {books = getShelf();} catch {books = [];}
    if (!Array.isArray(books)) books = [];
    if (shelfCache?.books === books) return shelfCache;
    const keys = new Set();
    for (const book of books) if (book && typeof book === 'object') {keys.add(sourceEntryKey(book)); keys.add(workIdentity(book));}
    shelfCache = {books, keys, profile: buildRecommendationProfile(books)};
    return shelfCache;
  }
  const onShelf = book => shelfData().keys.has(sourceEntryKey(book)) || shelfData().keys.has(workIdentity(book));
  const wasServed = candidate => served.has(candidate.key) || candidate.variants.some(book => served.has(sourceEntryKey(book)));
  function available(input = pool, unseenOnly = true) {
    return input.flatMap(candidate => {
      if (unseenOnly && wasServed(candidate)) return [];
      if (candidate.variants.some(book => onShelf(book) || feedback.isDismissed(book))) return [];
      const book = candidate.variants.find(book => !failed.has(sourceEntryKey(book)));
      return book ? [{...candidate, book}] : [];
    });
  }
  function select(count = RECOMMENDATION_BATCH_SIZE, excluded = new Set()) {
    return rankRecommendations(available().filter(candidate => !excluded.has(candidate.key)), {profile: shelfData().profile, feedback, now: now(), limit: count});
  }
  function snapshot() {
    const choices = available(), shown = new Set(state.cards.map(card => card.key));
    const canReplace = choices.some(candidate => !shown.has(candidate.key));
    const local = feedback.snapshot();
    const allOnShelf = pool.length > 0 && pool.every(candidate => candidate.variants.some(onShelf));
    return {...state, cards: state.cards.map(card => ({...card, canReplace,
      onShelf: card.variants.some(onShelf)})), canNext: canReplace || nextBatch !== null,
      poolSize: pool.length, hasMore: nextBatch !== null, personalization: local.personalization,
      dismissedCount: local.dismissed.length, feedbackWarning: local.warning, metrics: local.metrics,
      canUndo: Boolean(undo), dismissedTitle: undo?.book.title || '',
      emptyReason: allOnShelf ? 'shelf' : pool.length && pool.every(candidate => feedback.isDismissed(candidate.book)) ? 'dismissed' :
        failed.size && !available(pool, false).length ? 'covers' : 'pool'};
  }
  const notify = () => onChange(snapshot());
  function cancel() {enrichment?.abort(); enrichment = null; generation++; request?.abort(); request = null;}
  function makeCard(candidate, id, replacements = 0, retry = 0) {
    served.add(candidate.key);
    for (const book of candidate.variants) served.add(sourceEntryKey(book));
    const reason = [candidate.preferenceReason, originReason(candidate.book)].filter(Boolean).join(' · ');
    return {...candidate, reason, id, revision: ++revision, coverStatus: 'loading', replacements, retry};
  }
  function showSelection(candidates) {
    state = {...state, cards: candidates.map((candidate, index) => makeCard(candidate, `recommendation-${index}`))};
  }
  function replace(card, automatic) {
    const excluded = new Set(state.cards.map(item => item.key)), candidate = select(1, excluded)[0];
    if (!candidate) return false;
    state = {...state, cards: state.cards.map(item => item.id === card.id ? makeCard(candidate, card.id, automatic ? card.replacements + 1 : 0) : item)};
    return true;
  }
  function merge(input, incoming) {
    const values = normalizeRecommendations({items: [...input, ...incoming].flatMap(candidate => candidate.variants)}).candidates;
    // Retire already-served entries first; exposure memory remains independently
    // bounded, so supplementing never grows the in-memory candidate catalogue.
    const useful = new Set(available(values).map(candidate => candidate.key));
    return [...values.filter(candidate => useful.has(candidate.key)), ...values.filter(candidate => !useful.has(candidate.key))].slice(0, RECOMMENDATION_POOL_LIMIT);
  }
  function updateReasons() {
    const ranked = rankRecommendations(state.cards, {profile: shelfData().profile, feedback, now: now(), limit: state.cards.length});
    const reasons = new Map(ranked.map(card => [card.key, card]));
    state = {...state, cards: state.cards.map(card => ({...card, preferenceReason: reasons.get(card.key)?.preferenceReason || '',
      reason: reasons.get(card.key)?.reason || originReason(card.book)}))};
  }
  async function load(mode = 'initial') {
    if (!state.visible) return;
    if (mode === 'more' && nextBatch === null) return;
    cancel();
    const controller = new AbortController(), turn = generation; request = controller;
    state = {...state, phase: 'loading', error: ''}; notify();
    const current = () => state.visible && !controller.signal.aborted && turn === generation;
    let stagedPool = [...pool], stagedNext = nextBatch, stagedOrigins = [...state.origins], stagedWarnings = [], stagedAt = state.fetchedAt;
    let successes = 0, batch = mode === 'more' ? nextBatch : 0, failure = '';
    const received = new Set(), previousBatches = mode === 'refresh' ? new Set() : fetchedBatches;
    try {
      for (let count = 0; count < (mode === 'soft' ? 1 : MAX_SUPPLEMENT_REQUESTS); count++) {
        const query = new URLSearchParams();
        if (batch) query.set('batch', String(batch));
        if (mode === 'refresh' && count === 0) query.set('refresh', '1');
        const payload = await api(`/api/recommendations${query.size ? '?' + query : ''}`, null, controller.signal);
        if (!current()) return;
        const result = normalizeRecommendations({...payload, items: payload.items?.map(book => metadata.apply(book))});
        received.add(batch); successes++;
        stagedPool = merge(stagedPool, result.candidates);
        stagedNext = result.nextBatch;
        if (stagedNext !== null && (received.has(stagedNext) || previousBatches.has(stagedNext))) {
          stagedNext = nextBatch !== null && !received.has(nextBatch) && !previousBatches.has(nextBatch) ? nextBatch : null;
        }
        stagedOrigins = normalizeRecommendations({items: [], origins: [...stagedOrigins, ...result.origins]}).origins.slice(-50);
        stagedWarnings = [...new Set([...stagedWarnings, ...result.warnings])].slice(-18);
        if (result.candidates.length) stagedAt = result.fetchedAt;
        if (mode === 'soft' || stagedNext === null || available(stagedPool).length >= RECOMMENDATION_BATCH_SIZE) break;
        batch = stagedNext;
      }
    } catch (error) {
      if (!current()) return;
      failure = text(error?.message) || '暂时无法读取推荐，请稍后重试';
    } finally {if (request === controller) request = null;}
    if (!current()) return;
    if (!successes) {state = {...state, phase: 'error', error: failure}; notify(); return;}
    pool = stagedPool; nextBatch = stagedNext;
    if (mode === 'refresh') fetchedBatches.clear();
    for (const value of received) fetchedBatches.add(value);
    loaded = true; loadedAt = now();
    if (mode === 'refresh') failed.clear();
    state = {...state, phase: 'ready', fetchedAt: stagedAt, origins: stagedOrigins,
      warnings: [...stagedWarnings, ...(failure ? [`补充推荐暂不可用：${failure}`] : [])], error: ''};
    if (mode !== 'soft' || !state.cards.length) {
      let candidates = select();
      if (!candidates.length && mode === 'refresh') {
        candidates = rankRecommendations(available(pool, false), {profile: shelfData().profile, feedback, now: now(), limit: RECOMMENDATION_BATCH_SIZE});
      }
      if (candidates.length) showSelection(candidates);
      else if (!state.cards.length || state.cards.every(card => onShelf(card.book) || feedback.isDismissed(card.book) || failed.has(sourceEntryKey(card.book)))) showSelection([]);
    }
    updateReasons(); notify(); void enrich();
  }
  function rememberMetadata(book, value) {
    metadata.remember(book, value);
    const apply = candidate => ({...candidate, book: metadata.apply(candidate.book), variants: candidate.variants.map(item => metadata.apply(item))});
    pool = pool.map(apply); state = {...state, cards: state.cards.map(apply)};
    if (state.visible) {updateReasons(); notify();}
  }
  async function enrich() {
    if (!state.visible) return;
    enrichment?.abort(); const controller = new AbortController(); enrichment = controller;
    const selected = state.cards.filter(card => card.book.metadataAvailable === true && !metadata.has(card.book) && !metadataTried.has(sourceEntryKey(card.book))).slice(0, 2);
    await Promise.all(selected.map(async ({book}) => {
      metadataTried.add(sourceEntryKey(book));
      try {
        const data = await api('/api/book-metadata', {siteId: book.siteId, detailUrl: book.detailUrl}, controller.signal);
        if (!controller.signal.aborted) rememberMetadata(book, data);
      } catch { /* Optional enrichment never delays the first cards or reading. */ }
    }));
    if (enrichment === controller) enrichment = null;
  }
  const unsubscribeFeedback = feedback.subscribe?.(() => {
    if (undo && !feedback.isDismissed(undo.book)) undo = null;
    for (const card of [...state.cards]) {
      if (!card.variants.some(book => feedback.isDismissed(book))) continue;
      if (!replace(card, false)) state = {...state, cards: state.cards.filter(item => item !== card)};
    }
    updateReasons(); notify();
  });
  return {
    rememberMetadata,
    recordRead: (book, progress) => feedback.recordRead(book, progress),
    recordFailure: (book, detail) => feedback.recordFailure(book, detail),
    recordRecovery: (book, detail) => feedback.recordRecovery(book, detail),
    destroy() {cancel(); unsubscribeFeedback?.(); feedback.destroy?.();},
    show() {
      if (state.visible) return;
      feedback.sync?.();
      state = {...state, visible: true};
      if (loaded) {
        if (now() - loadedAt >= RECOMMENDATION_SOFT_TTL) return load('soft');
        if (state.phase === 'idle') state = {...state, phase: 'ready'};
        updateReasons(); notify(); return;
      }
      return load();
    },
    hide() {cancel(); state = {...state, visible: false, phase: state.phase === 'loading' ? 'idle' : state.phase}; notify();},
    refresh() {return load('refresh');},
    nextBatch() {
      if (!state.visible || state.phase === 'loading' || !loaded) return;
      const candidates = select();
      if (candidates.length < RECOMMENDATION_BATCH_SIZE && nextBatch !== null) return load('more');
      if (candidates.length) showSelection(candidates);
      else if (state.cards.every(card => onShelf(card.book) || feedback.isDismissed(card.book) || failed.has(sourceEntryKey(card.book)))) showSelection([]);
      state = {...state, phase: 'ready', error: ''}; notify(); void enrich();
    },
    shelfChanged() {shelfCache = null; updateReasons(); notify();},
    dismiss(id) {
      if (!state.visible) return;
      const card = state.cards.find(item => item.id === id);
      if (!card) return;
      undo = {book: card.book, card, index: state.cards.indexOf(card)};
      feedback.dismiss(card.book);
      if (state.cards.includes(card) && !replace(card, false)) state = {...state, cards: state.cards.filter(item => item !== card)};
      notify();
    },
    undoDismiss() {
      if (!undo) return;
      const previous = undo; undo = null; feedback.undo(previous.book);
      const cards = [...state.cards], index = cards.findIndex(card => card.id === previous.card.id);
      const restored = makeCard(previous.card, previous.card.id);
      if (index >= 0) cards[index] = restored; else cards.splice(Math.min(previous.index, cards.length), 0, restored);
      state = {...state, cards}; updateReasons(); notify();
    },
    clearFeedback() {feedback.clear(); metadata.clear(); metadataTried.clear(); served.clear(); undo = null; updateReasons(); notify();},
    setPersonalization(value) {feedback.setPersonalization(value); updateReasons(); notify();},
    markExposed(id, token) {
      const card = state.cards.find(item => item.id === id && item.revision === token);
      if (state.visible && card) {
        const warning = feedback.snapshot().warning;
        feedback.markExposed(card.book);
        if (warning !== feedback.snapshot().warning) notify();
      }
    },
    opened(id) {
      const card = state.cards.find(item => item.id === id);
      if (card) {feedback.markExposed(card.book); feedback.opened(card.book);}
    },
    coverLoaded(id, token) {
      const card = state.cards.find(item => item.id === id && item.revision === token);
      if (!card || card.coverStatus !== 'loading') return;
      state = {...state, cards: state.cards.map(item => item === card ? {...item, coverStatus: 'ready'} : item)}; notify();
    },
    coverFailed(id, token) {
      const card = state.cards.find(item => item.id === id && item.revision === token);
      if (!card || card.coverStatus !== 'loading') return;
      failed.add(sourceEntryKey(card.book));
      // Prefer another validated cover for this known work before replacing it.
      const alternate = card.variants.find(book => !failed.has(sourceEntryKey(book)));
      if (state.visible && card.replacements < 1 && alternate) {
        state = {...state, cards: state.cards.map(item => item === card ? makeCard({...card, book: alternate}, card.id, card.replacements + 1) : item)};
      } else if (!(state.visible && card.replacements < 1 && replace(card, true))) {
        state = {...state, cards: state.cards.map(item => item === card ? {...item, coverStatus: 'error'} : item)};
      }
      notify();
    },
    retryCover(id) {
      if (!state.visible) return;
      const card = state.cards.find(item => item.id === id);
      if (!card || card.coverStatus !== 'error') return;
      failed.delete(sourceEntryKey(card.book));
      state = {...state, cards: state.cards.map(item => item === card ? makeCard(card, id, card.replacements, card.retry + 1) : item)}; notify();
    },
    replaceCover(id) {
      if (!state.visible) return;
      const card = state.cards.find(item => item.id === id);
      if (card && replace(card, false)) notify();
    },
    getState() {return snapshot();},
  };
}
