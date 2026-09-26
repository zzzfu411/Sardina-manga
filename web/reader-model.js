/** Pure reading-position rules shared by the browser and regression tests. */
export const MAX_IMAGE_REQUESTS = 4;
export const PAGE_BUILD_BATCH = 80;

/** Build privately in short tasks; cancellation never exposes a partial chapter. */
export async function buildReaderPages(urls, {createPage, isCurrent, yieldControl = () => new Promise(resolve => setTimeout(resolve, 0))}) {
  const pages = [];
  for (let index = 0; index < urls.length; index++) {
    if (index % PAGE_BUILD_BATCH === 0) {
      if (index) await yieldControl();
      if (!isCurrent()) return null;
    }
    pages.push(createPage(urls[index], index));
  }
  return isCurrent() ? pages : null;
}

/** Missing v2 fields keep their original defaults; pre-v2 preferences migrate once. */
export function normalizeReaderPreferences(saved, legacy = {}) {
  const value = saved && typeof saved === 'object' && !Array.isArray(saved) ? saved : {
    theme: legacy.light === true ? 'light' : 'dark', width: legacy.width,
  };
  const width = Number(value.width);
  return {
    theme: value.theme === 'light' ? 'light' : 'dark',
    width: Number.isFinite(width) && width > 0 ? Math.max(480, Math.min(1200, width)) : 800,
    focused: value.focused === true,
    mode: value.mode === 'paged' ? 'paged' : 'continuous',
    direction: value.direction === 'rtl' ? 'rtl' : 'ltr',
    fit: ['width', 'page'].includes(value.fit) ? value.fit : value.mode === 'paged' ? 'page' : 'width',
    zoom: clampReaderZoom(value.zoom),
    prefetch: ['off', 'more'].includes(value.prefetch) ? value.prefetch : 'auto',
  };
}

export function clampReaderZoom(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? Math.round(Math.max(1, Math.min(3, number)) * 100) / 100 : 1;
}

/** Book settings override defaults without becoming the next book's defaults. */
export function readerPreferencesForBook(defaults, records, key) {
  const base = normalizeReaderPreferences(defaults);
  const saved = records && typeof records === 'object' && !Array.isArray(records) && Object.hasOwn(records, key) ? records[key] : null;
  return normalizeReaderPreferences(saved && typeof saved === 'object' && !Array.isArray(saved) ? {...base, ...saved} : base);
}

/** Only explicit source sequence IDs constrain automatic chapter navigation. */
export function chapterNavigation(chapters, index) {
  if (!Array.isArray(chapters) || !Number.isInteger(index) || !chapters[index]) return {previous: null, next: null, sequenceId: '', count: 0};
  const chapter = chapters[index], sequenceId = typeof chapter.sequenceId === 'string' ? chapter.sequenceId.trim() : '';
  if (chapter.localOnly) return {previous: null, next: null, sequenceId, count: 1};
  const same = row => !row?.localOnly && (!sequenceId || typeof row?.sequenceId === 'string' && row.sequenceId.trim() === sequenceId);
  let previous = null, next = null;
  for (let at = index - 1; at >= 0; at--) if (same(chapters[at])) {previous = at; break;}
  for (let at = index + 1; at < chapters.length; at++) if (same(chapters[at])) {next = at; break;}
  return {previous, next, sequenceId, count: chapters.filter(same).length};
}

/** One explicit reading session can fail and recover once. Retrying the same
 * chapter or moving within this session does not inflate recommendation events.
 */
export function createReadingFeedback({sessionId, onFailure = () => {}, onRecovery = () => {}} = {}) {
  let failure = null, recovered = false;
  return {
    fail(detail) {
      if (failure) return false;
      failure = {...detail, sessionId}; onFailure(failure); return true;
    },
    recover(detail) {
      if (!failure || recovered || failure.chapterUrl !== detail.chapterUrl || failure.pageIndex !== null && failure.pageIndex !== detail.pageIndex) return false;
      recovered = true; onRecovery({...detail, sessionId, failureKind: failure.kind}); return true;
    },
  };
}

export function clampPage(page, total) {
  const count = Math.max(0, Math.floor(Number(total) || 0));
  return Math.max(0, Math.min(count - 1, Math.floor(Number(page) || 0)));
}

export function clampOffset(value) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.min(1, Math.max(0, number)) : 0;
}

export function restorePosition(saved, chapterUrl, total) {
  if (!saved || saved.chapterUrl !== chapterUrl) return {page: 0, pageOffset: 0};
  return {page: clampPage(saved.page, total), pageOffset: clampOffset(saved.pageOffset)};
}

export function normalizeRatio(value, fallback = 1.42) {
  const ratio = Number(value);
  return Number.isFinite(ratio) && ratio >= 0.15 && ratio <= 50 ? ratio : fallback;
}

export function prefetchPageCount({mode = 'continuous', prefetch = 'auto', siteId = '', saveData = false} = {}) {
  if (prefetch === 'off' || prefetch === 'auto' && (siteId === 'komiic' || saveData)) return 0;
  return mode === 'paged' ? prefetch === 'more' ? 6 : 3 : prefetch === 'more' ? 10 : 6;
}

/** Current/visible pages first, forward buffer next; never fetch the earlier chapter prefix. */
export function imageCandidates({total, page, first = page, last = page, mode = 'continuous', ahead = prefetchPageCount({mode})}) {
  if (!(total > 0)) return [];
  const anchor = clampPage(page, total);
  const from = mode === 'paged' ? anchor : clampPage(first, total), to = mode === 'paged' ? anchor : Math.max(from, clampPage(last, total));
  const candidates = new Set([anchor]);
  for (let index = from; index <= to; index++) candidates.add(index);
  const count = Math.min(10, Math.max(0, Math.floor(Number(ahead) || 0)));
  for (let index = to + 1; index <= Math.min(total - 1, to + count); index++) candidates.add(index);
  if (count && anchor > 0) candidates.add(anchor - 1);
  return [...candidates];
}

/** Page boundaries never imply a chapter change. */
export function pageNavigation(page, total) {
  const count = Math.max(0, Math.floor(Number(total) || 0));
  const current = clampPage(page, count);
  return {previous: count && current > 0 ? current - 1 : null, next: current < count - 1 ? current + 1 : null};
}

export function pageTurnDelta(key, direction = 'ltr') {
  if (key !== 'ArrowLeft' && key !== 'ArrowRight') return 0;
  return (key === 'ArrowRight' ? 1 : -1) * (direction === 'rtl' ? -1 : 1);
}

/** Fit a whole comic page inside the available canvas, respecting width preference. */
export function fitPageWidth({ratio, viewportWidth, viewportHeight, preferredWidth}) {
  const positive = (value, fallback) => Number.isFinite(Number(value)) && Number(value) > 0 ? Number(value) : fallback;
  return Math.max(1, Math.min(positive(viewportWidth, 1), positive(preferredWidth, 800), positive(viewportHeight, 1) / normalizeRatio(ratio)));
}

export function readerPageWidth({ratio, viewportWidth, viewportHeight, preferredWidth, fit = 'width', zoom = 1}) {
  const viewport = Math.max(1, Number(viewportWidth) || 1), preferred = Math.max(1, Number(preferredWidth) || 800);
  const base = fit === 'page' ? fitPageWidth({ratio, viewportWidth: viewport, viewportHeight, preferredWidth: preferred}) : Math.min(viewport, preferred);
  return base * clampReaderZoom(zoom);
}

/** A failed or not-yet-restored page must not replace a valid shelf position. */
export function progressSnapshot({chapter, total, position, restoring, loaded}) {
  if (!chapter?.url || !(total > 0) || restoring || !loaded) return null;
  return {
    chapterUrl: chapter.url,
    chapterName: chapter.name || '',
    page: clampPage(position?.page, total),
    pageOffset: clampOffset(position?.pageOffset),
    totalPages: total,
  };
}

/** Aborting is only half the guard: adapters can still settle after an abort. */
export function createChapterScope() {
  let active = null, generation = 0;
  return {
    start() {
      active?.controller.abort();
      active = {generation: ++generation, controller: new AbortController()};
      return active;
    },
    isCurrent(scope) {
      return !!scope && active === scope && !scope.controller.signal.aborted;
    },
    stop() {
      active?.controller.abort();
      active = null;
    },
  };
}
