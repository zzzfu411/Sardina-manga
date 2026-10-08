import {bookKey, bookRelevance, buildSearchModel, canonicalTitle, metadataText, normalizeAuthor, normalizeTitle, SOURCE_PRIORITY} from './search-model.js';
import {simplifySearchText} from './search-characters.js';

// These are display groups, not persistent work identities. Source metadata is
// too inconsistent to let a writer/artist/publisher credit create another card.
// Keep every source entry intact for selection, reading and library storage.
const compare = (a, b) => a < b ? -1 : a > b ? 1 : 0;
const rank = site => { const index = SOURCE_PRIORITY.indexOf(site); return index < 0 ? SOURCE_PRIORITY.length : index; };
const preferred = (a, b) => rank(a.siteId) - rank(b.siteId) || b.matchScore - a.matchScore || compare(bookKey(a), bookKey(b));
const simplified = value => simplifySearchText(metadataText(value).normalize('NFKC')).replace(/\p{Cf}/gu, '');
const SCRIPT_SUFFIX = /\s*[(【\[](?:简体|繁体)(?:中文)?(?:版)?[)】\]]\s*$/u;

export function searchTitleKey(value) {
  return canonicalTitle(simplified(value).replace(SCRIPT_SUFFIX, ''));
}

function languageKey(value) {
  const language = simplified(value).toLowerCase();
  return /^(?:zh(?:[-_](?:cn|tw|hk|hans|hant))?|中文|(?:简体|繁体)(?:中文)?(?:版)?)$/u.test(language) ? '' : normalizeTitle(language);
}

export function buildSearchResults(groups = [], keyword = '', filter = '') {
  const identities = buildSearchModel(groups, keyword, filter), buckets = new Map();
  const query = searchTitleKey(keyword);
  for (const identity of [...identities.works, ...identities.related, ...identities.hidden]) {
    for (const original of identity.books) {
      const titleKey = searchTitleKey(original.title);
      const match = bookRelevance({...original, title: titleKey,
        alternateTitles: Array.isArray(original.alternateTitles) ? original.alternateTitles.slice(0, 30).map(searchTitleKey) : [],
        matchedTitle: searchTitleKey(original.matchedTitle)}, query);
      const book = {...original, matchScore: match.score, matchKind: match.kind};
      const edition = normalizeTitle(simplified(book.edition)), language = languageKey(book.language);
      const key = JSON.stringify(['search-title', titleKey, edition, language]);
      if (!buckets.has(key)) buckets.set(key, {key, canonicalTitle: titleKey, books: [], titles: new Map(), identityByBook: new Map()});
      const bucket = buckets.get(key);
      bucket.books.push(book);
      bucket.titles.set(bookKey(book), identity.title);
      bucket.identityByBook.set(bookKey(book), identity.key);
    }
  }
  const all = [...buckets.values()].map(({titles, ...group}) => {
    group.books.sort(preferred);
    const best = [...group.books].sort((a, b) => b.matchScore - a.matchScore || preferred(a, b))[0];
    return {...group, title: titles.get(bookKey(best)), preferred: group.books[0], score: best.matchScore, kind: best.matchKind,
      sourceCount: new Set(group.books.map(book => book.siteId)).size,
      hasDifferentCredits: new Set(group.books.map(book => normalizeAuthor(book.author)).filter(Boolean)).size > 1};
  });
  all.sort((a, b) => b.score - a.score || rank(a.preferred.siteId) - rank(b.preferred.siteId) || compare(a.key, b.key));
  const threshold = all.some(work => work.score >= 98) ? 98 : 70;
  const works = all.filter(work => work.score >= threshold), related = all.filter(work => work.score < threshold && work.score >= 35);
  const hidden = all.filter(work => work.score < 35);
  return {works, related, hidden, rawCount: identities.rawCount, candidateCount: identities.candidateCount,
    workCount: works.length, relatedCount: related.length, hiddenCount: hidden.length, totalWorkCount: all.length};
}
