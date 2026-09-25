import {normalizeAuthor} from './search-model.js';
import {workIdentity} from './book-identity.js';

const text = value => typeof value === 'string' ? value.trim() : '';
const THEMES = {
  '推理': ['推理', '悬疑', '懸疑', '侦探', '偵探'], '恋爱': ['恋爱', '戀愛', '爱情', '愛情'],
  '冒险': ['冒险', '冒險'], '科幻': ['科幻', 'science fiction'], '奇幻': ['奇幻', '魔法', '玄幻'],
  '运动': ['运动', '運動', '竞技', '競技', '篮球', '籃球', '足球', '排球', '将棋', '將棋'],
  '校园': ['校园', '校園', '学园', '學園'], '日常': ['日常', '生活'],
  '治愈': ['治愈', '治癒'], '喜剧': ['喜剧', '喜劇', '搞笑'], '历史': ['历史', '歷史'],
};

/** Keywords are evidence in metadata, not inferred genres or an AI label. */
export function contentFeatures(book) {
  const tags = [...(Array.isArray(book?.tags) ? book.tags : []), ...(Array.isArray(book?.genres) ? book.genres : [])].filter(tag => typeof tag === 'string');
  const metadata = [text(book?.title), text(book?.description), ...tags].join(' ').normalize('NFKC').toLowerCase().slice(0, 6000);
  return Object.entries(THEMES).filter(([, words]) => words.some(word => metadata.includes(word))).map(([theme]) => theme);
}
const authors = book => normalizeAuthor(book?.author).split('+').filter(Boolean);

export function preferenceWeight(book) {
  if (book?.favorite === true) return book.readingState === 'finished' ? 4.5 : book.readAt ? 4 : 3;
  // Auto-shelved trial reads are weaker than an intentional collection action.
  return book?.favorite === false ? .2 : .6;
}

export function buildRecommendationProfile(shelf = []) {
  const profile = {authors: new Map(), themes: new Map()}, seen = new Set();
  // Bound profile construction for large imported libraries; explicit favorites
  // and their most recent action take precedence over automatically added reads.
  const books = shelf.filter(book => book && typeof book === 'object').sort((a, b) =>
    Number(b.favorite === true) - Number(a.favorite === true) ||
    Number(b.favoriteChangedAt || b.readAt || b.openedAt || 0) - Number(a.favoriteChangedAt || a.readAt || a.openedAt || 0)).slice(0, 500);
  for (const book of books) {
    const key = workIdentity(book);
    if (seen.has(key)) continue;
    seen.add(key);
    const weight = preferenceWeight(book), evidence = {title: text(book.title), explicit: book.favorite === true};
    for (const [map, features] of [[profile.authors, authors(book)], [profile.themes, contentFeatures(book)]]) {
      for (const feature of features) {
        const previous = map.get(feature);
        map.set(feature, {weight: Math.min(8, (previous?.weight || 0) + weight),
          evidence: !previous || weight > previous.evidenceWeight ? evidence : previous.evidence,
          evidenceWeight: Math.max(weight, previous?.evidenceWeight || 0)});
      }
    }
  }
  return profile;
}

export function originReason(book) {
  if (!text(book?.siteName) || !['popular', 'latest'].includes(book?.recommendationKind)) return '';
  return `${book.siteName}${book.recommendationKind === 'popular' ? '热门' : '最近更新'}`;
}

/** Transparent initial rules, deliberately not a trained recommendation model. */
export function rankRecommendations(candidates, {profile = buildRecommendationProfile(), feedback, limit = 12, now = Date.now()} = {}) {
  const personalization = feedback?.snapshot().personalization !== false;
  const available = candidates.map((candidate, index) => {
    const book = candidate.book;
    const matches = personalization ? authors(book).map(author => profile.authors.get(author)).filter(Boolean).sort((a, b) => b.weight - a.weight) : [];
    const themes = personalization ? contentFeatures(book).map(theme => ({theme, match: profile.themes.get(theme)})).filter(row => row.match).sort((a, b) => b.match.weight - a.match.weight) : [];
    const author = matches[0], theme = themes[0];
    const affinity = Math.min(6, author?.weight || 0) * 3 + Math.min(4, theme?.match.weight || 0) * 1.2;
    const preference = author ? `与${author.evidence.explicit ? '收藏' : '读过'}的《${author.evidence.title}》作者相同` :
      theme ? `与你的书架同含“${theme.theme}”线索` : '';
    const exposure = feedback?.exposure(book);
    const exposurePenalty = exposure ? 3 * Math.exp(-Math.max(0, now - exposure.at) / (7 * 86400000)) : 0;
    const recentFailure = Object.values(book.readingHealth || {}).some(row => row?.status === 'error' && now - Date.parse(row.checkedAt) >= 0 && now - Date.parse(row.checkedAt) < 15 * 60000);
    return {...candidate, score: affinity + (book.recommendationKind === 'popular' ? 1 : .8) - exposurePenalty - (recentFailure ? 1.5 : 0), exposurePenalty,
      preferenceReason: preference, reason: [preference, originReason(book)].filter(Boolean).join(' · '),
      exposed: Boolean(exposure), index, author: authors(book)[0] || ''};
  });
  const selected = [], sourceCounts = new Map(), authorCounts = new Map();
  while (available.length && selected.length < limit) {
    available.sort((a, b) =>
      (b.score - (sourceCounts.get(b.book.siteId) || 0) * 2 - (authorCounts.get(b.author) || 0) * 3) -
      (a.score - (sourceCounts.get(a.book.siteId) || 0) * 2 - (authorCounts.get(a.author) || 0) * 3) || a.index - b.index);
    const candidate = available.shift();
    selected.push(candidate);
    sourceCounts.set(candidate.book.siteId, (sourceCounts.get(candidate.book.siteId) || 0) + 1);
    if (candidate.author) authorCounts.set(candidate.author, (authorCounts.get(candidate.author) || 0) + 1);
  }
  return selected;
}
