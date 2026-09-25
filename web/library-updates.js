/** Compact catalogue metadata; independent from reading position and classification. */
export const MAX_UPDATE_REQUESTS = 2;
import {sourceEntryKey} from './book-identity.js';
const keyOf = sourceEntryKey;
const validTime = value => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
const clock = value => validTime(value) ? value : Date.now();
const sameSnapshot = (left, right) => !!left && !!right && left.fingerprint === right.fingerprint && left.chapterCount === right.chapterCount;
const sameVersion = (left, right) => !left && !right || sameSnapshot(left, right) && left.checkedAt === right.checkedAt;
const safeError = error => typeof error?.message === 'string' && error.message.trim() ?
  error.message.replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 240) : '目录检查失败，请稍后重试';

/** Invalid optional backup metadata must never erase a valid reading record. */
export function normalizeCatalogState(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      typeof value.fingerprint !== 'string' || !/^v1:[0-9a-f]{16}$/.test(value.fingerprint) ||
      !Number.isSafeInteger(value.chapterCount) || value.chapterCount <= 0 || value.chapterCount > 100000 ||
      !validTime(value.checkedAt) || !validTime(value.changedAt) || value.changedAt > value.checkedAt ||
      !['', 'new', 'changed'].includes(value.change)) return null;
  const {fingerprint, chapterCount, checkedAt, changedAt, change} = value;
  return {fingerprint, chapterCount, checkedAt, changedAt, change,
    ...(validTime(value.acknowledgedAt) ? {acknowledgedAt: value.acknowledgedAt} : {})};
}

export function mergeCatalogState(current, incoming) {
  const left = normalizeCatalogState(current), right = normalizeCatalogState(incoming);
  if (!right) return left;
  if (!left || right.checkedAt > left.checkedAt) return right;
  if (sameSnapshot(left, right) && right.checkedAt === left.checkedAt && (right.acknowledgedAt || 0) > (left.acknowledgedAt || 0)) return right;
  // Equal timestamps favour the current record, including a cleared notice.
  return left;
}

/** FNV-1a 64-bit over sorted, length-framed URLs; names/order never affect it. */
export function catalogSnapshot(detail) {
  if (!detail || ['partial', 'unknown'].includes(detail.catalogCompleteness) ||
      (detail.unavailableReason && detail.catalogCompleteness !== 'complete') || !Array.isArray(detail.chapters) || !detail.chapters.length) {
    throw new Error('漫画源未提供完整的非空目录，已保留原有记录');
  }
  const urls = new Set();
  for (const chapter of detail.chapters) {
    if (typeof chapter?.url !== 'string' || !chapter.url.trim()) throw new Error('漫画源目录包含无效章节地址，已保留原有记录');
    let url;
    try {url = new URL(chapter.url.trim());} catch {throw new Error('漫画源目录包含无效章节地址，已保留原有记录');}
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error('漫画源目录包含无效章节地址，已保留原有记录');
    urls.add(url.href);
  }
  if (urls.size > 100000) throw new Error('漫画源目录数量异常，已保留原有记录');
  let hash = 0xcbf29ce484222325n;
  for (const url of [...urls].sort()) {
    const framed = `${url.length}:${url}`;
    // Hash both bytes of each UTF-16 unit, independent of locale or URL order.
    for (let index = 0; index < framed.length; index++) {
      const unit = framed.charCodeAt(index);
      hash = BigInt.asUintN(64, (hash ^ BigInt(unit & 255)) * 0x100000001b3n);
      hash = BigInt.asUintN(64, (hash ^ BigInt(unit >>> 8)) * 0x100000001b3n);
    }
  }
  return {fingerprint: `v1:${hash.toString(16).padStart(16, '0')}`, chapterCount: urls.size};
}

function snapshotOutcome(previous, snapshot) {
  if (!previous) return 'baseline';
  if (sameSnapshot(previous, snapshot)) return 'unchanged';
  return snapshot.chapterCount > previous.chapterCount ? 'new' : 'changed';
}

export function catalogStateAfterCheck(previous, snapshot, now = Date.now()) {
  const current = normalizeCatalogState(previous);
  const checkedAt = Math.max(clock(now), current ? current.checkedAt + 1 : 0);
  const outcome = snapshotOutcome(current, snapshot);
  const state = {
    fingerprint: snapshot?.fingerprint, chapterCount: snapshot?.chapterCount, checkedAt,
    changedAt: outcome === 'baseline' ? 0 : outcome === 'unchanged' ? current.changedAt : checkedAt,
    change: outcome === 'baseline' ? '' : outcome === 'unchanged' ? current.change : outcome,
  };
  if (!normalizeCatalogState(state)) throw new Error('目录快照无效');
  return state;
}

/**
 * Apply directly to the latest book's catalogState. A cached matching directory
 * can acknowledge a notice without claiming another network check. A fresh
 * response may replace the baseline only if its captured version is still current.
 */
export function acknowledgeCatalog(previous, detail, options = {}) {
  const current = normalizeCatalogState(previous);
  let snapshot;
  try {snapshot = catalogSnapshot(detail);} catch {return current;}
  if (sameSnapshot(current, snapshot)) return current.change ? {...current, change: '', acknowledgedAt: clock(options.now)} : current;
  if (!current) return catalogStateAfterCheck(null, snapshot, options.now);
  const base = normalizeCatalogState(options.baseState);
  if (options.fresh !== true || !validTime(options.startedAt) || !Object.hasOwn(options, 'baseState') ||
      current.checkedAt > options.startedAt || !sameVersion(current, base)) return current;
  return {...catalogStateAfterCheck(current, snapshot, options.now), change: ''};
}

/**
 * getBook(key) returns the latest existing shelf record or null. Callbacks receive
 * only catalogue metadata, never an old book/progress snapshot. start preserves
 * the caller's reading/later/finished priority order and deduplicates book keys.
 */
export function createLibraryUpdates({api, getBook, onBookResult = () => {}, onStatus = () => {}, now = Date.now}) {
  if (typeof api !== 'function' || typeof getBook !== 'function') throw new Error('缺少书架更新检查接口');
  let active = null, transports = 0;
  const waiting = [];
  const isCurrent = run => active === run && run.status.running && !run.controller.signal.aborted;
  const emit = run => onStatus({...run.status});
  const exists = (item, book) => book && keyOf(book) === item.key;
  const resultFor = (item, value) => ({key: item.key, siteId: item.siteId, detailUrl: item.detailUrl, ...value});

  // An adapter may ignore abort. Its slot remains occupied until it settles,
  // including across stop/start, so rapid restarts cannot exceed the cap.
  function takeSlot(run) {
    if (!isCurrent(run)) return false;
    if (transports < MAX_UPDATE_REQUESTS) {transports++; return true;}
    return new Promise(resolve => {
      const entry = {run, resolve};
      entry.abort = () => {
        const index = waiting.indexOf(entry);
        if (index >= 0) waiting.splice(index, 1);
        resolve(false);
      };
      waiting.push(entry); run.controller.signal.addEventListener('abort', entry.abort, {once: true});
    });
  }
  function releaseSlot() {
    transports--;
    while (transports < MAX_UPDATE_REQUESTS && waiting.length) {
      const entry = waiting.shift();
      entry.run.controller.signal.removeEventListener('abort', entry.abort);
      if (!isCurrent(entry.run)) {entry.resolve(false); continue;}
      transports++; entry.resolve(true);
    }
  }

  function finish(run, cancelled = false) {
    if (active !== run || !run.status.running) return;
    run.status.running = false; run.status.active = 0;
    run.status.cancelled = cancelled ? run.status.total - run.status.completed : 0;
    active = null;
    if (cancelled) run.controller.abort();
    emit(run); run.resolve({...run.status});
  }
  function stop() {
    if (!active) return false;
    finish(active, true);
    return true;
  }

  async function inspect(run, item) {
    if (!exists(item, getBook(item.key))) return {skipped: true};
    const slot = takeSlot(run);
    if (!(typeof slot === 'boolean' ? slot : await slot)) return null;
    try {
      if (!isCurrent(run)) return null;
      const book = getBook(item.key);
      if (!exists(item, book)) return {skipped: true};
      const base = normalizeCatalogState(book.catalogState), startedAt = clock(now());
      try {
        const detail = await api('/api/details', {siteId: item.siteId, detailUrl: item.detailUrl, refresh: true}, run.controller.signal);
        if (!isCurrent(run)) return null;
        return {base, startedAt, snapshot: catalogSnapshot(detail), error: ''};
      } catch (error) {
        if (!isCurrent(run)) return null;
        return {base, startedAt, error: safeError(error)};
      }
    } finally {releaseSlot();}
  }

  function resultToCommit(item, inspected) {
    if (inspected.skipped) return inspected;
    // inspect() resolves across a microtask boundary. A detail acknowledgement,
    // import or deletion can happen there, so derive metadata only at commit.
    const book = getBook(item.key);
    if (!exists(item, book)) return {skipped: true};
    const current = normalizeCatalogState(book.catalogState);
    if (!sameVersion(current, inspected.base) || current && current.checkedAt > inspected.startedAt) return {skipped: true};
    if (inspected.error) return resultFor(item, {catalogState: null, error: inspected.error, outcome: 'error'});
    return resultFor(item, {catalogState: catalogStateAfterCheck(current, inspected.snapshot, now()), error: '', outcome: snapshotOutcome(current, inspected.snapshot)});
  }

  async function worker(run) {
    while (isCurrent(run) && run.cursor < run.items.length) {
      const item = run.items[run.cursor++];
      run.status.active++; emit(run);
      if (!isCurrent(run)) return;
      const inspected = await inspect(run, item);
      if (!isCurrent(run) || !inspected) return;
      const result = resultToCommit(item, inspected);
      run.status.active--; run.status.completed++;
      if (result.skipped) run.status.skipped++;
      else {
        if (result.error) run.status.failed++; else run.status.succeeded++;
        onBookResult(result);
      }
      if (!isCurrent(run)) return;
      if (run.status.completed === run.status.total) {finish(run); return;}
      emit(run);
    }
  }

  function start(books) {
    stop();
    const items = [], seen = new Set();
    for (const book of Array.isArray(books) ? books : []) {
      if (typeof book?.siteId !== 'string' || !book.siteId || typeof book.detailUrl !== 'string' || !/^https?:\/\//i.test(book.detailUrl)) continue;
      const key = keyOf(book);
      if (!seen.has(key)) {seen.add(key); items.push({key, siteId: book.siteId, detailUrl: book.detailUrl});}
    }
    const run = {items, cursor: 0, controller: new AbortController(), status: {
      running: true, total: items.length, completed: 0, succeeded: 0, failed: 0, skipped: 0, active: 0, cancelled: 0,
    }};
    const completion = new Promise(resolve => {run.resolve = resolve;});
    active = run; emit(run);
    if (!items.length) finish(run);
    else if (isCurrent(run)) for (let index = 0; index < Math.min(MAX_UPDATE_REQUESTS, items.length); index++) void worker(run);
    return completion;
  }
  return {start, stop, isRunning: () => !!active?.status.running};
}
