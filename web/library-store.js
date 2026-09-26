import {bookKey, mergeShelfBackup, parseShelfBackup} from './library-model.js';

export const LEGACY_SHELF_KEY = 'revyunman.shelf.v1';
export const SHELF_PREFIX = 'revyunman.library.v2.book.';
const MIGRATED_KEY = 'revyunman.library.v2.migrated';
const clone = value => JSON.parse(JSON.stringify(value));
const recordKey = key => SHELF_PREFIX + encodeURIComponent(key);
const deletionEpoch = record => typeof record?.deletionEpoch === 'string' ? record.deletionEpoch : record?.deleted ? `legacy:${record.writtenAt}` : '';
const newEpoch = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}:${Math.random().toString(36).slice(2)}`;

/** Per-book records isolate independent edits; tombstones prevent stale-tab resurrection. */
export function createLibraryStore({storage, locks = globalThis.navigator?.locks, now = Date.now} = {}) {
  let baseline = [], queue = Promise.resolve(), clock = 0, generation = 0;
  const issues = new Set();
  const pending = new Map();
  const baselineEpochs = new Map();
  function legacy() {
    const raw = storage.getItem(LEGACY_SHELF_KEY);
    if (!raw) return [];
    try {
      const books = JSON.parse(raw);
      if (!Array.isArray(books)) throw new Error();
      const kept = [];
      for (const book of books) {
        try {kept.push(...parseShelfBackup({version: 1, books: [book]}));}
        catch {issues.add('部分旧记录无法识别，原始书架已保留，可导出原始记录恢复');}
      }
      return mergeShelfBackup([], kept);
    } catch {issues.add('旧书架无法解析，原始记录已保留，可导出原始记录恢复'); return [];}
  }
  function entry(key) {
    const raw = storage.getItem(recordKey(key));
    if (!raw) return null;
    const record = JSON.parse(raw);
    if (!record || !Number.isFinite(record.writtenAt) || (!record.deleted && (!record.book || bookKey(record.book) !== key))) throw new Error('部分书架记录无法解析，原始记录已保留');
    if (!record.deleted) parseShelfBackup({version: 2, books: [record.book]});
    return record;
  }
  function read() {
    const records = new Map();
    for (let i = 0; i < storage.length; i++) {
      const name = storage.key(i);
      if (!name?.startsWith(SHELF_PREFIX)) continue;
      try {const key = decodeURIComponent(name.slice(SHELF_PREFIX.length)); records.set(key, entry(key));}
      catch {issues.add('部分书架记录无法解析，原始记录已保留');}
    }
    const books = storage.getItem(MIGRATED_KEY) === '1' ? [] : legacy();
    const current = mergeShelfBackup(books, [...records.values()].filter(record => record && !record.deleted).map(record => record.book));
    const visible = current.filter(book => !records.get(bookKey(book))?.deleted);
    for (const book of visible) baselineEpochs.set(bookKey(book), deletionEpoch(records.get(bookKey(book))));
    return visible;
  }
  function currentEpoch(key) {
    const operation = pending.get(key), latest = entry(key);
    if (operation?.deleted) return operation.deletionEpoch;
    if (operation?.priorDeletion && (!latest || latest.writtenAt <= operation.priorDeletion.stamp)) return operation.priorDeletion.deletionEpoch;
    return deletionEpoch(latest);
  }
  function canSaveProgress(book, context) {
    if (!context || context.key !== bookKey(book) || typeof context.deletionEpoch !== 'string') return false;
    return currentEpoch(context.key) === context.deletionEpoch;
  }
  // Keep v1 as an immutable base and only persist changed records on top. A
  // full duplicate migration would consume quota before a user could remove a
  // book. Existing fully-migrated records (marker 1) remain supported by read().
  function writePending(readAll = true) {
    const updated = new Map();
    for (const operation of [...pending.values()].sort((a, b) => Number(b.deleted === true) - Number(a.deleted === true))) {
      const {key, book, stamp: writtenAt, deleted, progressContext} = operation;
      let latest = entry(key);
      if (operation.priorDeletion && (!latest || latest.writtenAt <= operation.priorDeletion.stamp)) {
        const prior = operation.priorDeletion;
        latest = {deleted: true, writtenAt: prior.stamp, deletionEpoch: prior.deletionEpoch};
        storage.setItem(recordKey(key), JSON.stringify(latest));
      }
      if (deleted) {
        if (!latest || latest.writtenAt <= writtenAt) storage.setItem(recordKey(key), JSON.stringify({deleted: true, writtenAt, deletionEpoch: operation.deletionEpoch}));
      } else if ((!operation.passive || !latest?.deleted) && (!progressContext || progressContext.deletionEpoch === deletionEpoch(latest)) && (!latest?.deleted || latest.writtenAt < writtenAt)) {
        const merged = latest && !latest.deleted ? mergeShelfBackup([latest.book], [book])[0] : book;
        storage.setItem(recordKey(key), JSON.stringify({writtenAt: Math.max(writtenAt, latest?.writtenAt || 0), deletionEpoch: deletionEpoch(latest), book: merged}));
        baselineEpochs.set(key, deletionEpoch(latest));
        updated.set(key, merged);
      }
      if (pending.get(key) === operation) pending.delete(key);
    }
    return readAll ? read() : updated;
  }
  function locked(action) {return locks?.request ? locks.request('revyunman-library-v2', async () => action()) : Promise.resolve().then(action);}
  baseline = read();
  return {
    get books() {return clone(baseline);},
    get issues() {return [...issues];},
    // The epoch follows the deletion, even after an explicit re-add. A new
    // timestamp from an old reader is therefore never mistaken for new intent.
    beginProgressSession(book) {const key = bookKey(book); return Object.freeze({key, deletionEpoch: currentEpoch(key)});},
    canSaveProgress,
    updateBook(book, {progressContext} = {}) {
      const normalized = parseShelfBackup({version: 2, books: [book]})[0], key = bookKey(normalized);
      if (progressContext && !canSaveProgress(normalized, progressContext)) return Promise.resolve(null);
      const priorDeletion = pending.get(key)?.deleted ? pending.get(key) : pending.get(key)?.priorDeletion;
      const stamp = clock = Math.max(now(), clock + 1, progressContext ? (entry(key)?.writtenAt || 0) + 1 : 0), turn = ++generation;
      pending.set(key, {key, book: normalized, stamp, progressContext, priorDeletion});
      baseline = [normalized, ...baseline.filter(item => bookKey(item) !== key)];
      queue = queue.catch(() => {}).then(() => locked(() => {
        const updated = writePending(false), latest = entry(key);
        const allowed = !progressContext || deletionEpoch(latest) === progressContext.deletionEpoch;
        const result = allowed ? updated.get(key) || latest?.book : null;
        if (turn === generation) baseline = latest?.deleted ? baseline.filter(item => bookKey(item) !== key) :
          latest?.book ? [latest.book, ...baseline.filter(item => bookKey(item) !== key)] : baseline;
        return result ? clone(result) : null;
      }));
      return queue;
    },
    save(books, {intent = 'explicit', contexts} = {}) {
      const next = mergeShelfBackup([], books), before = new Map(baseline.map(book => [bookKey(book), book]));
      const after = new Map(next.map(book => [bookKey(book), book]));
      const stamp = clock = Math.max(now(), clock + 1), turn = ++generation;
      const changes = [...after].filter(([key, book]) => JSON.stringify(book) !== JSON.stringify(before.get(key))).map(([key, book]) => ({key, book: clone(book)}));
      const passive = intent === 'passive';
      const removals = passive ? [] : [...before.keys()].filter(key => !after.has(key));
      for (const change of changes) {
        const prior = pending.get(change.key), priorDeletion = prior?.deleted ? prior : prior?.priorDeletion;
        const supplied = contexts instanceof Map ? contexts.get(change.key) : contexts?.[change.key];
        const progressContext = passive ? prior?.progressContext || supplied || {key: change.key, deletionEpoch: baselineEpochs.get(change.key) ?? currentEpoch(change.key)} : undefined;
        // Never replace a valid queued progress update with stale metadata. A
        // passive snapshot is also unable to turn an old read into a new add.
        if (passive && (prior?.deleted || entry(change.key)?.deleted || !canSaveProgress(change.book, progressContext) || supplied && !canSaveProgress(change.book, supplied))) continue;
        pending.set(change.key, {...change, stamp, priorDeletion, progressContext, passive});
      }
      for (const key of removals) pending.set(key, {key, deleted: true, stamp, deletionEpoch: newEpoch()});
      baseline = clone(next);
      queue = queue.catch(() => {}).then(() => locked(() => {
        const result = writePending();
        if (turn === generation) baseline = clone(result);
        return result;
      }));
      return queue;
    },
    // pagehide/visibilitychange cannot wait for a future Web Locks callback.
    // Re-read each record and merge its independent clocks before this final
    // synchronous flush; queued callbacks later see an empty pending map.
    flush() {baseline = writePending(); return clone(baseline);},
    async sync() {
      await queue.catch(() => {});
      baseline = read();
      for (const operation of pending.values()) {
        if (operation.deleted) baseline = baseline.filter(book => bookKey(book) !== operation.key);
        else if (!operation.progressContext || canSaveProgress(operation.book, operation.progressContext)) baseline = mergeShelfBackup(baseline, [operation.book]);
      }
      return clone(baseline);
    },
    recovery() {
      const result = {};
      for (let i = 0; i < storage.length; i++) {const key = storage.key(i); if (key === LEGACY_SHELF_KEY || key?.startsWith(SHELF_PREFIX)) result[key] = storage.getItem(key);}
      return JSON.stringify({format: 'yunman-raw-recovery', records: result}, null, 2);
    },
  };
}
