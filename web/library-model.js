export const READING_STATES = {reading: '在读', later: '想读', finished: '已读'};
import {sourceEntryKey} from './book-identity.js';
export const bookKey = sourceEntryKey;
export const MAX_BACKUP_BYTES = 50 * 1024 * 1024;
const time = value => Number.isFinite(Number(value)) ? Math.max(0, Number(value)) : 0;
const hasProgress = book => typeof book?.chapterUrl === 'string' && book.chapterUrl.length > 0;
export const readingState = book => Object.hasOwn(READING_STATES, book?.readingState) ? book.readingState : hasProgress(book) ? 'reading' : 'later';
export const readTime = book => hasProgress(book) ? time(book.readAt) || time(book.openedAt) : 0;
const progressFields = ['chapterUrl', 'chapterName', 'page', 'pageOffset', 'totalPages', 'readAt'];
const normalized = value => String(value || '').normalize('NFKC').toLocaleLowerCase();
const validUrl = value => {try {const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password;} catch {return false;}};

export function normalizeChapterStates(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
  return Object.fromEntries(Object.entries(value).filter(([url, entry]) => validUrl(url) && typeof entry?.read === 'boolean' && Number.isSafeInteger(entry.updatedAt) && entry.updatedAt >= 0)
    .map(([url, entry]) => [url, {read: entry.read, updatedAt: entry.updatedAt}]));
}
export function mergeChapterStates(left, right) {
  const merged = normalizeChapterStates(left);
  for (const [url, entry] of Object.entries(normalizeChapterStates(right))) if (!merged[url] || entry.updatedAt > merged[url].updatedAt) merged[url] = entry;
  return merged;
}
export const isChapterRead = (book, chapter) => book?.chapterStates?.[typeof chapter === 'string' ? chapter : chapter?.url]?.read === true;
export function markChapterRead(book, chapter, read, now = Date.now()) {
  if (!validUrl(chapter?.url)) return book;
  const states = normalizeChapterStates(book?.chapterStates);
  states[chapter.url] = {read: read === true, updatedAt: Math.max(time(now), (states[chapter.url]?.updatedAt || 0) + 1)};
  return {...book, chapterStates: states};
}

/** A details refresh must not replace the latest position with an old book snapshot. */
export function rememberBook(previous, book, progress = {}, now = Date.now()) {
  const saved = {...previous, ...book, ...progress, openedAt: now};
  for (const field of progressFields) {
    if (previous?.[field] !== undefined && !Object.hasOwn(progress, field)) saved[field] = previous[field];
  }
  // Pin the legacy reading clock before openedAt is refreshed by a details view.
  if (previous && hasProgress(previous) && !time(previous.readAt)) saved.readAt = readTime(previous);
  saved.readingState = readingState(previous || saved);
  saved.stateChangedAt = time(previous?.stateChangedAt || saved.stateChangedAt);
  if (hasProgress(progress)) {
    saved.readAt = now;
    if (saved.readingState !== 'finished') {
      if (!previous || saved.readingState !== 'reading') saved.stateChangedAt = now;
      saved.readingState = 'reading';
    }
  }
  const catalogState = mergeCatalogState(previous?.catalogState, book?.catalogState);
  if (catalogState) saved.catalogState = catalogState; else delete saved.catalogState;
  saved.chapterStates = mergeChapterStates(previous?.chapterStates, book?.chapterStates);
  if (previous && !Object.hasOwn(progress, 'favorite')) {
    if (Object.hasOwn(previous, 'favorite')) saved.favorite = previous.favorite;
    saved.favoriteChangedAt = time(previous.favoriteChangedAt);
  }
  if (Object.hasOwn(progress, 'favorite')) saved.favoriteChangedAt = now;
  return saved;
}

export function setReadingState(book, value, now = Date.now()) {
  if (!Object.hasOwn(READING_STATES, value)) return book;
  return {...book, readingState: value, stateChangedAt: now};
}

export function libraryCounts(books) {
  const counts = {all: books.length, reading: 0, later: 0, finished: 0};
  books.forEach(book => counts[readingState(book)]++);
  return counts;
}

export function filterLibrary(books, {query = '', state = 'all', sort = 'recent'} = {}) {
  const needle = normalized(query).trim();
  return books.filter(book => (state === 'all' || readingState(book) === state) &&
    (!needle || [book.title, book.author, book.siteName, book.siteId].some(value => normalized(value).includes(needle))))
    .sort(sort === 'title' ? (a, b) => a.title.localeCompare(b.title, 'zh-CN') :
      (a, b) => (readTime(b) || time(b.openedAt)) - (readTime(a) || time(a.openedAt)));
}

export function continueBook(books) {
  return books.filter(book => hasProgress(book) && readingState(book) === 'reading')
    .sort((a, b) => readTime(b) - readTime(a))[0] || null;
}

/** Read v1 and v2 with the same limits used by the exporter. Unknown sources are retained. */
export function parseShelfBackup(data, knownSiteIds) {
  if (![1, 2].includes(data?.version) || !Array.isArray(data.books)) throw new Error('不是有效的 Sardina 书架备份');
  const fields = ['siteId', 'siteName', 'detailUrl', 'title', 'coverUrl', 'chapterUrl', 'chapterName', 'author', 'description', 'edition', 'language', 'status', 'latestChapter', 'sourceEntryId'];
  return data.books.map(book => {
    if (!book || typeof book.siteId !== 'string' || !/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$/.test(book.siteId) || typeof book.title !== 'string' || !book.title.trim() || typeof book.detailUrl !== 'string' || !validUrl(book.detailUrl)) throw new Error('备份中有无效的漫画信息');
    const clean = {};
    for (const field of fields) if (typeof book[field] === 'string') clean[field] = book[field];
    for (const field of ['alternateTitles', 'tags', 'genres']) if (Array.isArray(book[field])) clean[field] = book[field].filter(value => typeof value === 'string');
    clean.page = Math.trunc(Math.max(0, Math.min(10000, Number(book.page) || 0)));
    clean.pageOffset = Math.max(0, Math.min(1, Number(book.pageOffset) || 0));
    if (Number.isFinite(Number(book.totalPages)) && Number(book.totalPages) > 0) clean.totalPages = Math.trunc(Math.min(10001, Number(book.totalPages)));
    clean.openedAt = time(book.openedAt);
    clean.readAt = hasProgress(clean) ? time(book.readAt) || clean.openedAt : 0;
    clean.readingState = readingState(book);
    clean.stateChangedAt = time(book.stateChangedAt);
    if (typeof book.favorite === 'boolean') clean.favorite = book.favorite;
    clean.favoriteChangedAt = time(book.favoriteChangedAt);
    clean.chapterStates = normalizeChapterStates(book.chapterStates);
    const catalogState = normalizeCatalogState(book.catalogState);
    if (catalogState) clean.catalogState = catalogState;
    return clean;
  });
}

export function serializeShelfBackup(books) {
  const clean = parseShelfBackup({version: 2, books});
  const serialized = JSON.stringify({version: 2, books: clean}, null, 2);
  if (new TextEncoder().encode(serialized).length > MAX_BACKUP_BYTES) throw new Error('书架超过单份备份的 50 MB 上限，请先减少简介等元数据；现有记录未删除');
  return serialized;
}
export function readShelfBackup(serialized) {
  if (typeof serialized !== 'string' || new TextEncoder().encode(serialized).length > MAX_BACKUP_BYTES) throw new Error('备份文件超过 50 MB 上限');
  return parseShelfBackup(JSON.parse(serialized));
}

export function mergeShelfBackup(current, incoming) {
  const merged = new Map();
  for (const book of [...current, ...incoming]) {
    const old = merged.get(bookKey(book));
    if (!old) {merged.set(bookKey(book), book); continue;}
    const newest = time(book.openedAt) > time(old.openedAt) ? book : old;
    const next = {...old, ...newest};
    const progress = readTime(book) > readTime(old) ? book : old;
    // Transfer the entire progress tuple, including absent fields, as one record.
    for (const field of progressFields) {
      if (progress[field] !== undefined) next[field] = progress[field]; else delete next[field];
    }
    if (hasProgress(progress)) next.readAt = readTime(progress);
    const status = time(book.stateChangedAt) > time(old.stateChangedAt) ? book : old;
    next.readingState = readingState(status);
    next.stateChangedAt = time(status.stateChangedAt);
    if (!next.stateChangedAt) next.readingState = readingState(progress);
    const favorite = time(book.favoriteChangedAt) > time(old.favoriteChangedAt) ? book : old;
    if (typeof favorite.favorite === 'boolean') next.favorite = favorite.favorite;
    next.favoriteChangedAt = time(favorite.favoriteChangedAt);
    next.chapterStates = mergeChapterStates(old.chapterStates, book.chapterStates);
    const catalogState = mergeCatalogState(old.catalogState, book.catalogState);
    if (catalogState) next.catalogState = catalogState; else delete next.catalogState;
    merged.set(bookKey(book), next);
  }
  return [...merged.values()].sort((a, b) => time(b.openedAt) - time(a.openedAt));
}
import {mergeCatalogState, normalizeCatalogState} from './library-updates.js';
