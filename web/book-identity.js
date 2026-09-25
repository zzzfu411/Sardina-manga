import {canonicalTitle, normalizeAuthor} from './search-model.js';

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

export function workIdentity(book) {
  const title = canonicalTitle(book?.title), author = normalizeAuthor(book?.author);
  const edition = text(book?.edition).normalize('NFKC').toLowerCase(), language = text(book?.language).toLowerCase();
  return title && author ? JSON.stringify([title, author, edition, language]) : sourceEntryKey(book);
}

/** Missing authors must never bridge two unrelated same-title works. */
export function sameWork(left, right) {
  if (!left || !right) return false;
  if (sourceEntryKey(left) === sourceEntryKey(right)) return true;
  return !!normalizeAuthor(left.author) && !!normalizeAuthor(right.author) && workIdentity(left) === workIdentity(right);
}
