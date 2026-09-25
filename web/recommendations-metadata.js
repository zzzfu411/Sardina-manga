import {sourceEntryKey} from './book-identity.js';
export const METADATA_KEY = 'sardina.recommendations.metadata.v1';
const TTL = 7 * 86400000;
export function createRecommendationMetadata({storage, now = Date.now} = {}) {
  let rows = {};
  try {if (storage === undefined && typeof window !== 'undefined') storage = globalThis.localStorage; const value = JSON.parse(storage?.getItem(METADATA_KEY) || '{}'); if (value && typeof value === 'object' && !Array.isArray(value)) rows = value;} catch {}
  function read(book) {const row = rows[sourceEntryKey(book)]; return row && typeof row.at === 'number' && row.at <= now() && row.at > now() - TTL ? row : null;}
  function clean(value) {
    const result = {};
    for (const field of ['author', 'description', 'status']) if (typeof value?.[field] === 'string' && value[field].trim()) result[field] = value[field].trim().slice(0, 4000);
    for (const field of ['tags', 'genres']) if (Array.isArray(value?.[field])) {
      const tags = value[field].filter(tag => typeof tag === 'string' && tag.trim()).slice(0, 20).map(tag => tag.slice(0, 80));
      if (tags.length) result[field] = tags;
    }
    return result;
  }
  return {
    has: book => !!read(book),
    apply(book) {return {...book, ...clean(read(book)?.value)};},
    remember(book, value) {
      rows[sourceEntryKey(book)] = {at: now(), value: {...clean(read(book)?.value), ...clean(value)}};
      rows = Object.fromEntries(Object.entries(rows).filter(([, row]) => typeof row?.at === 'number' && row.at <= now() && row.at > now() - TTL).sort((a, b) => b[1].at - a[1].at).slice(0, 200));
      try {storage?.setItem(METADATA_KEY, JSON.stringify(rows));} catch { /* Optional metadata never blocks reading. */ }
    },
    clear() {rows = {}; try {storage?.removeItem(METADATA_KEY);} catch {}},
  };
}
