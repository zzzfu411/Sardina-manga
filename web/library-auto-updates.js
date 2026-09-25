import {readingState} from './library-model.js';
export const AUTO_UPDATE_INTERVAL = 6 * 60 * 60 * 1000;

/** Auto checks only started ongoing books; manual checks remain unrestricted. */
export function dueUpdateBooks(books, {now = Date.now(), attempts = {}, availableSites = null, limit = 20} = {}) {
  const sites = availableSites && new Set(availableSites);
  return books.filter(book => readingState(book) === 'reading' && book.chapterUrl && (!sites || sites.has(book.siteId)) &&
    !/完结|完結|completed|finished/i.test(book.status || '') &&
    now - Math.max(book.catalogState?.checkedAt || 0, attempts[`${book.siteId}::${book.detailUrl}`] || 0) >= AUTO_UPDATE_INTERVAL)
    .sort((a, b) => (b.readAt || b.openedAt || 0) - (a.readAt || a.openedAt || 0)).slice(0, limit);
}
