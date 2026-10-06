// Deliberately bounded mappings: unfamiliar titles stay separate rather than
// being merged using a fuzzy match. Relevance and work identity are independent.
const TRADITIONAL = Object.freeze({
  獅:'狮',龍:'龙',門:'门',國:'国',學:'学',園:'园',傳:'传',體:'体',戰:'战',劍:'剑',
  愛:'爱',夢:'梦',異:'异',時:'时',間:'间',無:'无',雙:'双',靈:'灵',滅:'灭',師:'师',
  風:'风',雲:'云',書:'书',畫:'画',話:'话',記:'记',錄:'录',貓:'猫',與:'与',為:'为',
  們:'们',這:'这',個:'个',來:'来',後:'后',開:'开',關:'关',東:'东',見:'见',長:'长',
  終:'终',極:'极',進:'进',擊:'击',術:'术',迴:'回',轉:'转',生:'生',戀:'恋',銀:'银',
  聖:'圣',騎:'骑',士:'士',惡:'恶',絕:'绝',覺:'觉',醒:'醒',獨:'独',變:'变',寵:'宠',
  顏:'颜',顯:'显',實:'实',虛:'虚',頭:'头',顧:'顾',葉:'叶',島:'岛',臺:'台',灣:'湾',
  陽:'阳',陰:'阴',華:'华',麗:'丽',樂:'乐',盜:'盗',賊:'贼',壽:'寿',驚:'惊',電:'电',
  絲:'丝',紅:'红',藍:'蓝',綠:'绿',黃:'黄',將:'将',軍:'军',亂:'乱',歷:'历',歸:'归',
  歲:'岁',點:'点',內:'内',陸:'陆',優:'优',滿:'满',遙:'遥',響:'响',現:'现',
  劉:'刘',陳:'陈',張:'张',馮:'冯',趙:'赵',吳:'吴',楊:'杨',鄭:'郑',賴:'赖',羅:'罗',
  裏:'里',裡:'里',說:'说',謎:'谜',條:'条',環:'环',經:'经',續:'续',繪:'绘',製:'制',
  諜:'谍',鋼:'钢',煉:'炼',鍊:'炼',過:'过',裝:'装',備:'备',強:'强',
});

export const SOURCE_PRIORITY = Object.freeze([
  'hipmh', 'manhuazhijia', 'baozimh', 'mangacopy', 'manhuagui', 'tuku',
  'rumanhua', 'dumanwu', 'mangabz', 'dm5', 'komiic', 'manben', 'comicbox',
]);

const ALIASES = new Map([
  ['三月的狮子', '三月的狮子'], ['3月的狮子', '三月的狮子'],
  ['海贼王', '海贼王'], ['航海王', '海贼王'], ['onepiece', '海贼王'],
  ['死神', '死神'], ['境界', '死神'], ['bleach', '死神'],
  ['间谍过家家', '间谍过家家'], ['间谍家家酒', '间谍过家家'], ['spyfamily', '间谍过家家'],
  ['进击的巨人', '进击的巨人'], ['进击巨人', '进击的巨人'],
  ['鬼灭之刃', '鬼灭之刃'], ['鬼灭', '鬼灭之刃'],
  ['咒术回战', '咒术回战'], ['咒术', '咒术回战'],
  ['火影忍者', '火影忍者'], ['火影', '火影忍者'],
  ['一拳超人', '一拳超人'], ['一击男', '一拳超人'],
  ['钢之炼金术师', '钢之炼金术师'], ['钢炼', '钢之炼金术师'],
]);
const VERSION_SUFFIX = /^(?:外传|番外|同人|彩色|全彩|重制|重置|原作|单行本|新装|完全版|特别篇|短篇|续篇|第\d+部)/u;
const text = value => typeof value === 'string' ? value.trim() : '';
const compareText = (a, b) => a < b ? -1 : a > b ? 1 : 0;
const sourceRank = siteId => { const rank = SOURCE_PRIORITY.indexOf(siteId); return rank < 0 ? SOURCE_PRIORITY.length : rank; };

// Some APIs return double-escaped text, including numeric entities without a
// semicolon. Decode only text; callers render it with textContent, never HTML.
export function metadataText(value) {
  const named = {amp: '&', quot: '"', apos: "'", lt: '<', gt: '>', nbsp: ' '};
  let result = text(value);
  for (let pass = 0; pass < 2; pass++) {
    result = result.replace(/&(?:#(\d{1,7});?|#x([a-f\d]{1,6});?|(amp|quot|apos|lt|gt|nbsp);)/gi, (raw, decimal, hex, name) => {
      if (name) return named[name.toLowerCase()];
      const code = parseInt(decimal || hex, decimal ? 10 : 16);
      return code > 0 && code <= 0x10ffff && !(code >= 0xd800 && code <= 0xdfff) ? String.fromCodePoint(code) : raw;
    });
  }
  return result.trim();
}

export function normalizeTitle(value) {
  return metadataText(value).normalize('NFKC').toLowerCase()
    .replace(/[\p{P}\p{Z}\s]/gu, '')
    .replace(/[\u3400-\u9fff]/gu, char => TRADITIONAL[char] || char);
}

export function canonicalTitle(value) {
  const normalized = normalizeTitle(value);
  if (ALIASES.has(normalized)) return ALIASES.get(normalized);
  // Preserve the entire edition marker: an alias must never erase an edition.
  for (const [alias, canonical] of ALIASES) {
    if (normalized.startsWith(alias) && VERSION_SUFFIX.test(normalized.slice(alias.length))) {
      return canonical + normalized.slice(alias.length);
    }
  }
  return normalized;
}

export function bookKey(book) {
  return `${text(book?.siteId)}::${text(book?.detailUrl)}`;
}

function authorText(book) {
  const extra = book.extra && typeof book.extra === 'object' ? book.extra : {};
  return metadataText(book.author) || metadataText(extra.author) || metadataText(extra['作者']);
}

export function normalizeAuthor(value) {
  const clean = metadataText(value).normalize('NFKC').replace(/^(?:作者|漫画|漫畫|绘画|繪畫)\s*[:：]\s*/u, '');
  if (/^n\/a$/iu.test(clean)) return '';
  const parts = clean.split(/[,，、/&＆+;；|()（）]|\s+and\s+/iu).map(normalizeTitle)
    .filter(part => part && !/^(?:未知|不详|不詳|佚名|暂无|暫無|unknown|team|studio)$/iu.test(part));
  return [...new Set(parts)].sort(compareText).join('+');
}

function authorGroups(known) {
  const entries = [...known].map(([key, books]) => ({key, books, names: new Set(key.split('+'))})).sort((a, b) => compareText(a.key, b.key));
  const subset = (a, b) => [...a].every(name => b.has(name));
  const anchors = entries.filter(entry => !entries.some(other => entry !== other && subset(entry.names, other.names)));
  const strongMatch = (a, b) => [...a.names].filter(name => b.names.has(name)
    && !/(?:studios?|工作室|出版社|出版|文化|漫画|动漫)$/u.test(name)).length >= 2;
  const clusters = [];
  // Rich credit lists may differ by an extra collaborator/publisher. Require
  // two shared contributors and agreement with every anchor, not a chain of
  // pairwise overlaps that could bridge different same-title works.
  for (const entry of anchors) {
    const matches = clusters.filter(cluster => cluster.anchors.every(other => strongMatch(entry, other)));
    if (matches.length === 1) { matches[0].anchors.push(entry); matches[0].books.push(...entry.books); }
    else clusters.push({key: entry.key, anchors: [entry], books: [...entry.books]});
  }
  for (const entry of entries.filter(entry => !anchors.includes(entry))) {
    const matches = clusters.filter(cluster => cluster.anchors.some(anchor => subset(entry.names, anchor.names)));
    // A partial credit shared by two different works stays unresolved. It
    // never becomes evidence for merging those works with each other.
    if (matches.length === 1) matches[0].books.push(...entry.books);
    else clusters.push({key: entry.key, anchors: [], books: [...entry.books]});
  }
  return clusters;
}

// Detail metadata is authoritative only when it actually names an author.
// Missing/unknown authors do not erase existing search evidence. Never infer
// authors from a synopsis or mutate responses owned by the root application.
export function withAuthorEvidence(groups, evidence) {
  return (Array.isArray(groups) ? groups : []).map(group => {
    if (!group || !Array.isArray(group.results)) return group;
    return {...group, results: group.results.map(book => {
      if (!book || typeof book !== 'object') return book;
      const author = evidence.get(bookKey({...book, siteId: text(group.siteId) || text(book.siteId)}));
      return normalizeAuthor(author) ? {...book, author: text(author)} : book;
    })};
  });
}

// A newly discovered author can split a card while the reader has a source
// selected. Route that card's state by its selected book before matching keys;
// otherwise the old work key steals the selection for the other author.
export function rekeyWorkStates(works, states) {
  const owners = new Map(works.flatMap(work => work.books.map(book => [bookKey(book), work.key])));
  const byKey = new Map();
  const preference = state => (state.userSelected ? 2 : 0) + (state.mounted ? 1 : 0);
  for (const state of states) {
    const key = owners.get(state.selectedKey) || state.work.key;
    const previous = byKey.get(key);
    if (!previous || preference(state) > preference(previous)) byKey.set(key, state);
  }
  return byKey;
}

function bigrams(value) {
  const chars = [...value];
  return new Set(chars.slice(0, -1).map((char, i) => char + chars[i + 1]));
}

export function titleRelevance(title, keyword) {
  const name = normalizeTitle(title), query = normalizeTitle(keyword);
  if (!query) return {score: 100, kind: 'all'};
  if (name === query) return {score: 100, kind: 'exact'};
  const canonical = canonicalTitle(name), needle = canonicalTitle(query);
  if (canonical === needle) return {score: 98, kind: 'alias'};
  if (needle.length >= 2 && canonical.includes(needle)) {
    return {score: Math.max(72, 88 - (canonical.length - needle.length)), kind: 'contains'};
  }
  if (canonical.length >= 2 && needle.includes(canonical)) return {score: 65, kind: 'related'};
  const a = bigrams(canonical), b = bigrams(needle);
  const common = [...b].filter(pair => a.has(pair)).length;
  const overlap = common / Math.max(b.size, 1);
  if (common >= 2 && overlap >= .35) return {score: Math.round(35 + overlap * 20), kind: 'related'};
  return {score: common ? 15 : 0, kind: 'other'};
}

/** Source-verified alternate titles influence relevance, never work merging. */
export function bookRelevance(book, keyword) {
  const titles = [book.title, ...(Array.isArray(book.alternateTitles) ? book.alternateTitles.slice(0, 30) : []), book.matchedTitle].filter(value => typeof value === 'string' && value.length <= 4000);
  return titles.map(title => titleRelevance(title, keyword)).reduce((best, match) => match.score > best.score ? match : best, {score: 0, kind: 'other'});
}

function preferredBook(a, b) {
  return sourceRank(a.siteId) - sourceRank(b.siteId)
    || b.matchScore - a.matchScore
    || compareText(bookKey(a), bookKey(b));
}

function makeWork(titleKey, authorKey, books, keyword) {
  books.sort(preferredBook);
  const best = [...books].sort((a, b) => b.matchScore - a.matchScore || preferredBook(a, b))[0];
  const canonicalQuery = canonicalTitle(keyword);
  const title = ALIASES.get(titleKey) || (titleKey === canonicalQuery && ALIASES.has(canonicalQuery) ? canonicalQuery : best.title);
  const variant = [normalizeTitle(best.edition), normalizeTitle(best.language)];
  return {
    key: JSON.stringify([titleKey, authorKey, ...(variant.some(Boolean) ? variant : [])]), canonicalTitle: titleKey,
    title, authorKey, books, preferred: books[0],
    score: best.matchScore, kind: best.matchKind,
    sourceCount: new Set(books.map(book => book.siteId)).size,
  };
}

export function buildSearchModel(groups = [], keyword = '', filter = '') {
  const selected = Array.isArray(groups) ? groups.filter(group => group && (!filter || group.siteId === filter)) : [];
  const candidates = new Map();
  let rawCount = 0;
  for (const group of selected) {
    const rows = Array.isArray(group.results) ? group.results : [];
    rawCount += rows.length;
    for (const row of rows) {
      if (!row || typeof row !== 'object') continue;
      const title = metadataText(row.title), detailUrl = text(row.detailUrl), siteId = text(group.siteId) || text(row.siteId);
      if (!title || !detailUrl || !siteId) continue;
      const relevance = bookRelevance({...row, title}, keyword);
      const book = {...row, title, detailUrl, siteId, siteName: text(group.siteName) || text(row.siteName) || siteId,
        author: authorText(row), matchScore: relevance.score, matchKind: relevance.kind};
      const key = bookKey(book), previous = candidates.get(key);
      if (!previous) candidates.set(key, book);
      else {
        // A duplicated URL often appears once for its cover and once for its
        // title. Keep the richer fields without counting it as another source.
        for (const field of ['coverUrl', 'author', 'description', 'status', 'latestChapter']) {
          if (!previous[field] && book[field]) previous[field] = book[field];
        }
        if (book.matchScore > previous.matchScore) Object.assign(previous, {title: book.title, matchScore: book.matchScore, matchKind: book.matchKind});
      }
    }
  }
  const byTitle = new Map();
  for (const book of candidates.values()) {
    const titleKey = canonicalTitle(book.title);
    const variantKey = JSON.stringify([titleKey, normalizeTitle(book.edition), normalizeTitle(book.language)]);
    if (!byTitle.has(variantKey)) byTitle.set(variantKey, {titleKey, books: []});
    byTitle.get(variantKey).books.push(book);
  }
  const all = [];
  for (const {titleKey, books} of byTitle.values()) {
    const known = new Map(), unknown = [];
    for (const book of books) {
      const author = normalizeAuthor(book.author);
      if (!author) unknown.push(book);
      else { if (!known.has(author)) known.set(author, []); known.get(author).push(book); }
    }
    const identities = authorGroups(known);
    // Unknown authors can join a single unambiguous identity, but must not
    // bridge two conflicting authors into one work.
    if (identities.length <= 1) {
      const author = identities[0]?.key || '';
      all.push(makeWork(titleKey, author, books, keyword));
    } else {
      for (const identity of identities) all.push(makeWork(titleKey, identity.key, identity.books, keyword));
      if (unknown.length) all.push(makeWork(titleKey, '', unknown, keyword));
    }
  }
  all.sort((a, b) => b.score - a.score || sourceRank(a.preferred.siteId) - sourceRank(b.preferred.siteId) || compareText(a.key, b.key));
  const threshold = all.some(work => work.score >= 98) ? 98 : 70;
  const works = all.filter(work => work.score >= threshold);
  const related = all.filter(work => work.score < threshold && work.score >= 35);
  const hidden = all.filter(work => work.score < 35);
  return {works, related, hidden, rawCount, workCount: works.length, relatedCount: related.length,
    hiddenCount: hidden.length, candidateCount: candidates.size, totalWorkCount: all.length};
}
