import {sameWork, sourceEntryKey, workIdentity} from './book-identity.js';

export const RECOMMENDATION_FEEDBACK_KEY = 'revyunman.recommendations.v1';
export const FEEDBACK_LIMITS = Object.freeze({dismissed: 300, exposures: 600, exposureDays: 28});
const DAY = 86400000;
const text = value => typeof value === 'string' ? value.trim() : '';
const minimalBook = book => Object.fromEntries(['siteId', 'detailUrl', 'title', 'author', 'edition', 'language']
  .map(field => [field, text(book?.[field])]));
const validBook = book => book && typeof book === 'object' && text(book.siteId) && text(book.title) && text(book.detailUrl);
const time = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
const count = value => Number.isSafeInteger(value) && value >= 0 ? Math.min(value, 10000000) : 0;

/** Local-only, bounded feedback. Failed writes remain visible and in memory. */
export function createRecommendationFeedback({storage, now = Date.now} = {}) {
  let warning = '', corrupt = false;
  const openedBooks = new Map();
  let state = {version: 1, personalization: true, dismissed: [], exposures: [], metrics: {impressions: 0, opens: 0, dismissals: 0, readingStarts: 0, continuedReads: 0, openFailures: 0}};
  try {
    if (storage === undefined) storage = globalThis.localStorage;
    const raw = storage?.getItem(RECOMMENDATION_FEEDBACK_KEY);
    if (raw) {
      const value = JSON.parse(raw);
      if (!value || value.version !== 1 || !Array.isArray(value.dismissed) || !Array.isArray(value.exposures)) throw new Error('format');
      state = {version: 1, personalization: value.personalization !== false,
        dismissed: value.dismissed.filter(row => validBook(row?.book) && time(row.at))
          .sort((a, b) => b.at - a.at).slice(0, FEEDBACK_LIMITS.dismissed).map(row => ({book: minimalBook(row.book), at: row.at})),
        exposures: value.exposures.filter(row => text(row?.key) && text(row.entryKey) && time(row.at) && row.at > now() - FEEDBACK_LIMITS.exposureDays * DAY)
          .sort((a, b) => b.at - a.at).slice(0, FEEDBACK_LIMITS.exposures)
          .map(row => ({key: row.key, entryKey: row.entryKey, at: row.at})),
        metrics: Object.fromEntries(['impressions', 'opens', 'dismissals', 'readingStarts', 'continuedReads', 'openFailures'].map(key => [key, count(value.metrics?.[key])]))};
    }
  } catch {
    corrupt = true;
    warning = '本地推荐记录未能读取，原记录已保留；可在推荐设置中清空后重建。';
  }
  function persist() {
    if (corrupt) return false;
    try {storage?.setItem(RECOMMENDATION_FEEDBACK_KEY, JSON.stringify(state)); warning = ''; return true;}
    catch {warning = '推荐记录暂时无法保存，本次选择仍有效；请检查浏览器存储空间。'; return false;}
  }
  function exposure(book) {
    const key = workIdentity(book), entry = sourceEntryKey(book), cutoff = now() - FEEDBACK_LIMITS.exposureDays * DAY;
    return state.exposures.find(row => row.at > cutoff && (row.key === key || row.entryKey === entry)) || null;
  }
  return {
    isDismissed(book) {return state.dismissed.some(row => sameWork(row.book, book));},
    exposure,
    dismiss(book) {
      if (!validBook(book)) return;
      state.dismissed = [{book: minimalBook(book), at: now()}, ...state.dismissed.filter(row => !sameWork(row.book, book))].slice(0, FEEDBACK_LIMITS.dismissed);
      state.metrics.dismissals = count(state.metrics.dismissals + 1); persist();
    },
    undo(book) {state.dismissed = state.dismissed.filter(row => !sameWork(row.book, book)); persist();},
    markExposed(book) {
      if (!validBook(book)) return;
      const previous = exposure(book);
      // IntersectionObserver can fire repeatedly as a card scrolls in/out.
      if (previous && now() - previous.at < 30 * 60000) return;
      state.exposures = [{key: workIdentity(book), entryKey: sourceEntryKey(book), at: now()},
        ...state.exposures.filter(row => row !== previous && row.at > now() - FEEDBACK_LIMITS.exposureDays * DAY)].slice(0, FEEDBACK_LIMITS.exposures);
      state.metrics.impressions = count(state.metrics.impressions + 1); persist();
    },
    opened(book) {if (validBook(book)) {
      const key = sourceEntryKey(book); openedBooks.delete(key); openedBooks.set(key, {at: now(), pages: new Set()});
      while (openedBooks.size > 30) openedBooks.delete(openedBooks.keys().next().value);
      state.metrics.opens = count(state.metrics.opens + 1); persist();
    }},
    recordRead(book, progress) {
      const row = openedBooks.get(sourceEntryKey(book));
      if (!row || now() - row.at > 6 * 3600000 || !progress?.chapterUrl || !Number.isInteger(progress.page)) return;
      const page = progress.chapterUrl + ':' + progress.page; if (row.pages.has(page)) return;
      row.pages.add(page); let changed = false;
      if (row.pages.size === 1) {state.metrics.readingStarts = count(state.metrics.readingStarts + 1); changed = true;}
      if (row.pages.size === 3 && !row.continued) {row.continued = true; state.metrics.continuedReads = count(state.metrics.continuedReads + 1); changed = true;}
      if (row.pages.size > 3) row.pages.delete(row.pages.values().next().value);
      if (changed) persist();
    },
    recordFailure(book) {
      const row = openedBooks.get(sourceEntryKey(book));
      if (!row || row.failed || now() - row.at > 6 * 3600000) return;
      row.failed = true; state.metrics.openFailures = count(state.metrics.openFailures + 1); persist();
    },
    setPersonalization(value) {state.personalization = value === true; persist();},
    clear() {
      openedBooks.clear(); state = {...state, dismissed: [], exposures: [], metrics: {impressions: 0, opens: 0, dismissals: 0, readingStarts: 0, continuedReads: 0, openFailures: 0}};
      corrupt = false; persist();
    },
    snapshot() {return {...state, dismissed: state.dismissed.map(row => ({...row, book: {...row.book}})),
      exposures: state.exposures.map(row => ({...row})), metrics: {...state.metrics}, warning};},
  };
}
