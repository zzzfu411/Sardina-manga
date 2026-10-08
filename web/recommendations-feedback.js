import {createWorkRelations, mergeAuthorCredit, preserveWorkEvidence, sourceEntryKey, workIdentity, workAuthorKey} from './book-identity.js';

export const RECOMMENDATION_FEEDBACK_KEY = 'revyunman.recommendations.v1';
export const FEEDBACK_LIMITS = Object.freeze({dismissed: 300, exposures: 600, exposureDays: 28});
const DAY = 86400000;
const METRICS = ['impressions', 'opens', 'dismissals', 'readingStarts', 'continuedReads', 'openFailures', 'openRecoveries'];
const text = value => typeof value === 'string' ? value.trim() : '';
const minimalBook = (book, context = []) => {
  const saved = Object.fromEntries(['siteId', 'detailUrl', 'title', 'author', 'edition', 'language'].map(field => [field, text(book?.[field])]));
  const evidence = preserveWorkEvidence([book], context)[0].identityAuthors;
  if (evidence?.length) saved.identityAuthors = evidence;
  return saved;
};
const validBook = book => book && typeof book === 'object' && text(book.siteId) && text(book.title) && text(book.detailUrl);
const time = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
const count = value => Number.isSafeInteger(value) && value >= 0 ? Math.min(value, 10000000) : 0;
const initialStamp = () => ({at: 0, id: ''});
const stamp = value => ({at: time(value?.at) ? value.at : 0, id: text(value?.id)});
const compare = (left, right) => left.at - right.at || left.id.localeCompare(right.id);
const emptyMetrics = () => Object.fromEntries(METRICS.map(key => [key, 0]));
const empty = () => ({version: 2, personalization: true, preferenceStamp: initialStamp(), epoch: initialStamp(),
  revisionAt: 0, dismissalFloor: initialStamp(), dismissed: [], exposures: [], metrics: emptyMetrics()});
const copy = value => JSON.parse(JSON.stringify(value));

function normalize(value, now) {
  if (!value || ![1, 2].includes(value.version) || !Array.isArray(value.dismissed) || !Array.isArray(value.exposures)) throw new Error('format');
  const state = {...empty(), personalization: value.personalization !== false,
    preferenceStamp: stamp(value.preferenceStamp), epoch: stamp(value.epoch), dismissalFloor: stamp(value.dismissalFloor),
    dismissed: value.dismissed.filter(row => validBook(row?.book) && time(row.at))
      .map(row => ({book: minimalBook(row.book), at: row.at, id: text(row.id), removed: row.removed === true})),
    exposures: value.exposures.filter(row => text(row?.key) && text(row.entryKey) && time(row.at))
      .map(row => ({key: row.key, entryKey: row.entryKey, at: row.at, id: text(row.id)})),
    metrics: Object.fromEntries(METRICS.map(key => [key, count(value.metrics?.[key])]))};
  state.revisionAt = Math.max(time(value.revisionAt) ? value.revisionAt : 0, state.epoch.at, state.preferenceStamp.at,
    ...state.dismissed.map(row => row.at), ...state.exposures.map(row => row.at));
  return bound(state, now);
}
function bound(state, now) {
  // Undo records carry the same clock as exclusions. Truncation advances a
  // floor, so an old queued exclusion cannot revive a discarded tombstone.
  state.dismissed.sort((a, b) => compare(b, a));
  const removed = state.dismissed.splice(FEEDBACK_LIMITS.dismissed);
  if (removed[0] && compare(removed[0], state.dismissalFloor) > 0) state.dismissalFloor = stamp(removed[0]);
  state.exposures = state.exposures.filter(row => row.at > now - FEEDBACK_LIMITS.exposureDays * DAY)
    .sort((a, b) => compare(b, a)).slice(0, FEEDBACK_LIMITS.exposures);
  return state;
}
const exposureIn = (state, book, now) => state.exposures.find(row => row.at > now - FEEDBACK_LIMITS.exposureDays * DAY &&
  (row.key === workIdentity(book) || row.entryKey === sourceEntryKey(book))) || null;

function apply(state, operation, now, context = []) {
  state.revisionAt = Math.max(state.revisionAt, operation.at);
  if (operation.type === 'preference') {
    if (compare(operation, state.preferenceStamp) > 0) {
      state.personalization = operation.value; state.preferenceStamp = stamp(operation);
    }
    return state;
  }
  if (operation.type === 'clear') {
    if (compare(operation, state.epoch) > 0) {
      state.epoch = stamp(operation); state.dismissed = []; state.exposures = [];
      state.dismissalFloor = initialStamp(); state.metrics = emptyMetrics();
    }
    return state;
  }
  // Passive events made before a clear belong to the old generation. Explicit
  // preferences are independent: clearing reading feedback keeps that choice.
  if (compare(operation.epoch, state.epoch) !== 0) return state;
  const savedBook = (book, incoming = []) => {
    const previous = state.dismissed.find(row => sourceEntryKey(row.book) === sourceEntryKey(book))?.book;
    const next = {...previous, ...book, author: mergeAuthorCredit(previous?.author, book.author)};
    const evidence = [...(previous?.identityAuthors || []), ...(book.identityAuthors || [])];
    if (evidence.length) next.identityAuthors = evidence;
    return preserveWorkEvidence([next], [...state.dismissed.map(row => row.book), ...context, ...incoming])[0];
  };
  if (operation.type === 'metadata') {
    const book = savedBook(operation.book);
    state.dismissed = state.dismissed.map(row => sourceEntryKey(row.book) === sourceEntryKey(operation.book) ?
      {...row, book} : row);
  } else if (operation.type === 'import') {
    if (compare(operation, state.preferenceStamp) > 0) {
      state.personalization = operation.personalization; state.preferenceStamp = stamp(operation);
    }
    const books = operation.books.map(book => savedBook(book, operation.books));
    const relations = createWorkRelations([...state.dismissed.map(row => row.book), ...context, ...books]);
    for (const book of books) {
      state.dismissed = [{book, ...stamp(operation), removed: false}, ...state.dismissed.filter(row => !relations.sameWork(row.book, book))];
    }
  } else if (operation.type === 'dismiss' || operation.type === 'undo') {
    const book = savedBook(operation.book), relations = createWorkRelations([...state.dismissed.map(row => row.book), ...context, book]);
    const previous = state.dismissed.filter(row => relations.sameWork(row.book, book));
    if (compare(operation, state.dismissalFloor) <= 0 || previous.some(row => compare(row, operation) >= 0)) return state;
    state.dismissed = [{book, ...stamp(operation), removed: operation.type === 'undo'},
      ...state.dismissed.filter(row => !relations.sameWork(row.book, book))];
    if (operation.type === 'dismiss') state.metrics.dismissals = count(state.metrics.dismissals + 1);
  } else if (operation.type === 'exposure') {
    const previous = exposureIn(state, operation.book, operation.at);
    if (previous && operation.at - previous.at < 30 * 60000) return state;
    state.exposures = [{key: workIdentity(operation.book), entryKey: sourceEntryKey(operation.book), ...stamp(operation)},
      ...state.exposures.filter(row => row !== previous)];
    state.metrics.impressions = count(state.metrics.impressions + 1);
  } else if (operation.type === 'metrics') {
    for (const key of operation.metrics) if (METRICS.includes(key)) state.metrics[key] = count(state.metrics[key] + 1);
  }
  return bound(state, now);
}

/** Field operations are replayed against fresh storage inside one cross-tab
 * lock. Optimistic state keeps clicks instant, without replaying whole snapshots.
 * v1 remains readable; v2 retains undo clocks and the clear generation. */
export function createRecommendationFeedback({storage, now = Date.now, locks = globalThis.navigator?.locks,
  events = globalThis, actorId = globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2)} = {}) {
  let warning = '', corrupt = false, state = empty(), clock = 0, sequence = 0, queue = Promise.resolve(), observedRaw;
  let workContext = [], relationCache = null;
  const openedBooks = new Map(), listeners = new Set(), pending = [];
  const storedRaw = () => storage?.getItem(RECOMMENDATION_FEEDBACK_KEY) || null;
  try {if (storage === undefined) storage = globalThis.localStorage; observedRaw = storedRaw(); state = read(observedRaw);}
  catch {corrupt = true; warning = '本地推荐记录未能读取，原记录已保留；可在推荐设置中清空后重建。';}
  function read(raw = storedRaw()) {return raw ? normalize(JSON.parse(raw), now()) : empty();}
  function adopt(next) {
    if (compare(next.epoch, state.epoch) !== 0) openedBooks.clear();
    state = next; clock = Math.max(clock, next.revisionAt);
  }
  function emit(external = false) {for (const listener of listeners) listener({external});}
  function sync(external = false) {
    if (corrupt) return;
    let changed = false;
    try {
      const raw = storedRaw(); if (raw === observedRaw) return;
      const next = read(raw);
      for (const operation of pending) apply(next, operation, now(), workContext);
      changed = JSON.stringify(next) !== JSON.stringify(state);
      adopt(next); observedRaw = raw;
    } catch {corrupt = true; warning = '本地推荐记录未能读取，原记录已保留；可在推荐设置中清空后重建。'; changed = true;}
    if (changed) emit(external);
  }
  function commit() {
    if (!pending.length || (corrupt && !pending.some(operation => operation.type === 'clear'))) return;
    const operations = [...pending];
    try {
      let next;
      try {next = read();} catch {if (!operations.some(operation => operation.type === 'clear')) throw new Error('corrupt'); next = empty();}
      for (const operation of operations) apply(next, operation, now(), workContext);
      const serialized = JSON.stringify(next);
      if (!storage?.setItem) throw new Error('unavailable');
      storage.setItem(RECOMMENDATION_FEEDBACK_KEY, serialized); observedRaw = serialized;
      pending.splice(0, operations.length); corrupt = false; warning = '';
      for (const operation of pending) apply(next, operation, now(), workContext);
      adopt(next);
    } catch (error) {
      if (error.message === 'corrupt') {corrupt = true; warning = '本地推荐记录未能读取，原记录已保留；可在推荐设置中清空后重建。';}
      else warning = '推荐记录暂时无法保存，本次选择仍有效；请检查浏览器存储空间。';
    }
    emit();
  }
  function submit(type, data = {}) {
    const operation = {type, ...data, epoch: {...state.epoch}, at: clock = Math.max(now(), state.revisionAt + 1, clock + 1), id: `${actorId}:${++sequence}`};
    pending.push(operation); adopt(apply(copy(state), operation, now(), workContext));
    if (locks?.request) queue = queue.catch(() => {}).then(() => locks.request('revyunman-recommendations-v2', commit));
    else commit();
    return operation.id;
  }
  function session(book) {
    sync(); const row = openedBooks.get(sourceEntryKey(book));
    return row && now() - row.at <= 6 * 3600000 ? row : null;
  }
  const onStorage = event => {
    if ((event.key === RECOMMENDATION_FEEDBACK_KEY || event.key === null) && (!event.storageArea || event.storageArea === storage)) sync(true);
  };
  events?.addEventListener?.('storage', onStorage);
  function relationsFor(book) {
    if (relationCache?.rows !== state.dismissed || relationCache?.context !== workContext) {
      const books = [...state.dismissed.map(row => row.book), ...workContext];
      relationCache = {rows: state.dismissed, context: workContext, books, relations: createWorkRelations(books)};
    }
    return relationCache.relations.has(book) ? relationCache.relations : createWorkRelations([...relationCache.books, book]);
  }
  return {
    setWorkContext(books) {workContext = books;},
    isDismissed(book) {
      const relations = relationsFor(book);
      return state.dismissed.some(row => !row.removed && relations.sameWork(row.book, book));
    },
    exposure: book => exposureIn(state, book, now()),
    dismiss(book) {if (validBook(book)) {sync(); submit('dismiss', {book: minimalBook(book, workContext)});}},
    undo(book) {if (validBook(book)) {sync(); submit('undo', {book: minimalBook(book, workContext)});}},
    rememberBook(book) {
      if (!validBook(book) || !workAuthorKey(book.author)) return;
      sync();
      const previous = state.dismissed.find(row => sourceEntryKey(row.book) === sourceEntryKey(book));
      const next = {...previous?.book, ...Object.fromEntries(Object.entries(minimalBook(book)).filter(([, value]) => value)),
        author: mergeAuthorCredit(previous?.book.author, book.author)};
      const saved = preserveWorkEvidence([next], workContext)[0];
      if (previous && JSON.stringify(previous.book) !== JSON.stringify(saved)) submit('metadata', {book: saved});
    },
    markExposed(book) {
      if (!validBook(book)) return;
      sync(); const previous = exposureIn(state, book, now());
      if (!previous || now() - previous.at >= 30 * 60000) submit('exposure', {book: minimalBook(book)});
    },
    opened(book) {
      if (!validBook(book)) return;
      sync(); const key = sourceEntryKey(book); openedBooks.delete(key);
      openedBooks.set(key, {at: now(), pages: new Set(), failures: new Set()});
      while (openedBooks.size > 30) openedBooks.delete(openedBooks.keys().next().value);
      submit('metrics', {metrics: ['opens']});
    },
    recordRead(book, progress) {
      const row = session(book);
      if (!row || !progress?.chapterUrl || !Number.isInteger(progress.page) || progress.page < 0) return;
      const page = progress.chapterUrl + ':' + progress.page; if (row.pages.has(page)) return;
      row.pages.add(page); const metrics = [];
      if (!row.started) {row.started = true; metrics.push('readingStarts');}
      if (row.pages.size === 3 && !row.continued) {row.continued = true; metrics.push('continuedReads');}
      if (row.pages.size > 3) row.pages.delete(row.pages.values().next().value);
      if (metrics.length) submit('metrics', {metrics});
    },
    recordFailure(book, detail = {}) {
      const row = session(book); if (!row) return;
      const id = String(detail.sessionId || 'open'); row.failures.add(id);
      if (row.failed) return;
      row.failed = true; submit('metrics', {metrics: ['openFailures']});
    },
    recordRecovery(book, detail = {}) {
      const row = session(book);
      if (!row || row.recovered || !row.failures.has(String(detail.sessionId || 'open'))) return;
      row.recovered = true; submit('metrics', {metrics: ['openRecoveries']});
    },
    setPersonalization(value) {sync(); submit('preference', {value: value === true});},
    exportPreferences() {
      sync();
      return {version: 1, personalization: state.personalization, dismissed: state.dismissed.filter(row => !row.removed).map(row => ({...row.book}))};
    },
    async importPreferences(value) {
      if (value?.version !== 1 || typeof value.personalization !== 'boolean' || !Array.isArray(value.dismissed) || value.dismissed.some(book => !validBook(book))) throw new Error('推荐偏好格式无效');
      sync(); submit('import', {personalization: value.personalization, books: value.dismissed.slice(0, FEEDBACK_LIMITS.dismissed).map(book => minimalBook(book, workContext))});
      await queue;
      if (warning) throw new Error(warning);
    },
    clear() {sync(); openedBooks.clear(); submit('clear');},
    sync() {sync(true);},
    async settled() {await queue;},
    subscribe(listener) {listeners.add(listener); return () => listeners.delete(listener);},
    destroy() {events?.removeEventListener?.('storage', onStorage); listeners.clear();},
    snapshot() {return {version: 2, personalization: state.personalization,
      dismissed: state.dismissed.filter(row => !row.removed).map(row => ({book: {...row.book}, at: row.at})),
      exposures: state.exposures.map(({key, entryKey, at}) => ({key, entryKey, at})), metrics: {...state.metrics}, warning};},
  };
}
