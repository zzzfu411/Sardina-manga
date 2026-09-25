import {bookKey, mergeShelfBackup, parseShelfBackup} from './library-model.js';

export const LEGACY_SHELF_KEY = 'revyunman.shelf.v1';
export const SHELF_PREFIX = 'revyunman.library.v2.book.';
const MIGRATED_KEY = 'revyunman.library.v2.migrated';
const clone = value => JSON.parse(JSON.stringify(value));
const recordKey = key => SHELF_PREFIX + encodeURIComponent(key);

/** Per-book records isolate independent edits; tombstones prevent stale-tab resurrection. */
export function createLibraryStore({storage, locks = globalThis.navigator?.locks, now = Date.now} = {}) {
  let baseline = [], queue = Promise.resolve(), clock = 0, generation = 0;
  const issues = new Set();
  const pending = new Map();
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
    return current.filter(book => !records.get(bookKey(book))?.deleted);
  }
  // Keep v1 as an immutable base and only persist changed records on top. A
  // full duplicate migration would consume quota before a user could remove a
  // book. Existing fully-migrated records (marker 1) remain supported by read().
  function writePending() {
    for (const operation of [...pending.values()].sort((a, b) => Number(b.deleted === true) - Number(a.deleted === true))) {
      const {key, book, stamp: writtenAt, deleted} = operation;
      const latest = entry(key);
      if (deleted) {
        if (!latest || latest.writtenAt <= writtenAt) storage.setItem(recordKey(key), JSON.stringify({deleted: true, writtenAt}));
      } else if (!latest?.deleted || latest.writtenAt < writtenAt) {
        const merged = latest && !latest.deleted ? mergeShelfBackup([latest.book], [book])[0] : book;
        storage.setItem(recordKey(key), JSON.stringify({writtenAt: Math.max(writtenAt, latest?.writtenAt || 0), book: merged}));
      }
      if (pending.get(key) === operation) pending.delete(key);
    }
    return read();
  }
  function locked(action) {return locks?.request ? locks.request('revyunman-library-v2', async () => action()) : Promise.resolve().then(action);}
  baseline = read();
  return {
    get books() {return clone(baseline);},
    get issues() {return [...issues];},
    save(books) {
      const next = mergeShelfBackup([], books), before = new Map(baseline.map(book => [bookKey(book), book]));
      const after = new Map(next.map(book => [bookKey(book), book]));
      const stamp = clock = Math.max(now(), clock + 1), turn = ++generation;
      const changes = [...after].filter(([key, book]) => JSON.stringify(book) !== JSON.stringify(before.get(key))).map(([key, book]) => ({key, book: clone(book)}));
      const removals = [...before.keys()].filter(key => !after.has(key));
      for (const change of changes) pending.set(change.key, {...change, stamp});
      for (const key of removals) pending.set(key, {key, deleted: true, stamp});
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
        else baseline = mergeShelfBackup(baseline, [operation.book]);
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
