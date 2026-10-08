import {buildSearchModel, metadataText, normalizeAuthor, normalizeTitle} from './search-model.js';
import {searchTitleKey} from './search-results-model.js';
import {simplifySearchText} from './search-characters.js';

const text = value => typeof value === 'string' ? value.trim() : '';
const token = value => {
  if (!/^[A-Za-z0-9_-]{3,64}$/.test(value)) return '';
  try {const decoded = atob(value.replaceAll('-', '+').replaceAll('_', '/')); return /^m:\d+$/.test(decoded) ? decoded : '';} catch {return '';}
};

/** Only verified source URL shapes may replace a URL with a stable identity. */
export function sourceEntryKey(book) {
  const site = text(book?.siteId), raw = text(book?.detailUrl);
  try {
    const url = new URL(raw);
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) return `${site}::${raw}`;
    if (site === 'cocoecar' && url.hostname === 'keke2026.com' && /^\/comic\/\d+\/?$/.test(url.pathname) && !url.search && !url.hash) {
      return `${site}::https://www.cocoecar.com${url.pathname.replace(/\/$/, '')}`;
    }
    if (site === 'hipmh' && ['m.hipmh.com', 'reader.hipmh.top'].includes(url.hostname)) {
      const supplied = new URLSearchParams(url.hash.slice(1)).getAll('mid');
      const query = url.searchParams.getAll('mid');
      if (supplied.length > 1 || query.length > 1) return `${site}::${raw}`;
      const path = url.pathname.match(/^\/(manga|works)\/([^/]+)\/?$/);
      const mid = supplied[0] || query[0] || (path && (path[1] === 'works' ? path[2].split('-')[0] : path[2])) || '';
      const id = token(mid);
      if (id) return `${site}::${id}`;
    }
    const rules = {
      manhuagui: ['www.manhuagui.com', /^\/comic\/(\d+)\/?$/],
      komiic: ['komiic.com', /^\/comic\/(\d+)\/?$/],
      mangacopy: ['www.mangacopy.com', /^\/comic\/([A-Za-z0-9_-]+)\/?$/],
    };
    const rule = rules[site], match = rule && url.hostname === rule[0] && !url.search && !url.hash && url.pathname.match(rule[1]);
    if (match) return `${site}::id:${match[1]}`;
  } catch {}
  return `${site}::${raw}`;
}

const corporateCredit = /(?:studios?|工作室|出版社|出版|文化|漫画|动漫|传媒|极直社)$/u;
export const workAuthorKey = value => normalizeAuthor(simplifySearchText(metadataText(value).normalize('NFKC')))
  .split('+').filter(name => name && !corporateCredit.test(name)).join('+');
const editionKey = value => normalizeTitle(simplifySearchText(metadataText(value)));
const languageKey = value => {
  const language = simplifySearchText(metadataText(value)).toLowerCase();
  return /^(?:zh(?:[-_](?:cn|tw|hk|hans|hant))?|中文|(?:简体|繁体)(?:中文)?(?:版)?)$/u.test(language) ? '' : normalizeTitle(language);
};

export function workIdentity(book) {
  const title = searchTitleKey(book?.title), author = workAuthorKey(book?.author);
  const edition = editionKey(book?.edition), language = languageKey(book?.language);
  return title && author ? JSON.stringify([title, author, edition, language]) : sourceEntryKey(book);
}

/** Missing authors must never bridge two unrelated same-title works. */
export function sameWork(left, right) {
  if (!left || !right) return false;
  if (sourceEntryKey(left) === sourceEntryKey(right)) return true;
  if (!searchTitleKey(left.title) || searchTitleKey(left.title) !== searchTitleKey(right.title) ||
      editionKey(left.edition) !== editionKey(right.edition) || languageKey(left.language) !== languageKey(right.language)) return false;
  const a = workAuthorKey(left.author).split('+').filter(Boolean), b = workAuthorKey(right.author).split('+').filter(Boolean);
  if (!a.length || !b.length) return false;
  if (a.join('+') === b.join('+')) return true;
  const shared = a.filter(name => b.includes(name));
  return shared.length > 0 && (a.every(name => b.includes(name)) || b.every(name => a.includes(name))) || shared.length >= 2;
}

/** Group using the same credit evidence as search. Unknown authors stay separate;
 * a partial credit shared by conflicting identities never bridges those groups. */
export function groupWorks(books = []) {
  const entries = [...new Map(books.map(book => [sourceEntryKey(book), book])).values()];
  const byKey = new Map(entries.map(book => [sourceEntryKey(book), book]));
  const known = entries.filter(book => workAuthorKey(book.author));
  const model = buildSearchModel(known.map(book => ({siteId: book.siteId, results: [{...book,
    title: searchTitleKey(book.title), author: workAuthorKey(book.author),
    edition: editionKey(book.edition), language: languageKey(book.language)}]})));
  const groups = model.works.map(group => ({key: group.key, books: group.books.map(book => byKey.get(sourceEntryKey(book)))}));
  groups.push(...entries.filter(book => !workAuthorKey(book.author)).map(book => ({key: sourceEntryKey(book), books: [book]})));
  const order = new Map(entries.map((book, index) => [sourceEntryKey(book), index]));
  return groups.map(group => ({...group, books: group.books.sort((a, b) => order.get(sourceEntryKey(a)) - order.get(sourceEntryKey(b)))}))
    .sort((a, b) => order.get(sourceEntryKey(a.books[0])) - order.get(sourceEntryKey(b.books[0])));
}
