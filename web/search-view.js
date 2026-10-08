import {bookKey, metadataText, normalizeAuthor, rekeyWorkStates, withAuthorEvidence} from './search-model.js';
import {buildSearchResults} from './search-results-model.js';

const abortError = () => new DOMException('请求已取消', 'AbortError');

// Each consumer owns its cancellation. The shared request is aborted only
// after its last consumer leaves; aborted requests still occupy a slot until
// the transport settles, keeping the actual concurrency bound intact.
export function createDetailLoader(api, {limit = 2, cacheSize = 96, ttl = 300000} = {}) {
  const cache = new Map(), tasks = new Map(), queue = [], running = new Set();
  let destroyed = false;
  function cached(key) {
    const entry = cache.get(key);
    if (!entry) return undefined;
    if (Date.now() - entry.at >= ttl) { cache.delete(key); return undefined; }
    cache.delete(key); cache.set(key, entry);
    return entry.value;
  }
  function settle(task, error, value) {
    for (const consumer of task.consumers) {
      consumer.signal?.removeEventListener('abort', consumer.abort);
      if (error) consumer.reject(error); else consumer.resolve(value);
    }
    task.consumers.clear();
  }
  function pump() {
    while (!destroyed && running.size < limit && queue.length) {
      const task = queue.shift();
      if (!task.consumers.size || task.controller.signal.aborted) continue;
      task.started = true; running.add(task);
      Promise.resolve().then(() => {
        if (task.controller.signal.aborted) throw abortError();
        return api('/api/details', {siteId: task.book.siteId, detailUrl: task.book.detailUrl}, task.controller.signal);
      })
        .then(value => {
          if (task.controller.signal.aborted) return settle(task, abortError());
          if (!value || typeof value !== 'object') throw new Error('源站未返回可用目录');
          cache.set(task.key, {at: Date.now(), value});
          while (cache.size > cacheSize) cache.delete(cache.keys().next().value);
          settle(task, null, value);
        }).catch(error => settle(task, error)).finally(() => {
          running.delete(task);
          if (tasks.get(task.key) === task) tasks.delete(task.key);
          pump();
        });
    }
  }
  function cancelTask(task) {
    task.controller.abort();
    if (tasks.get(task.key) === task) tasks.delete(task.key);
    if (!task.started) { const index = queue.indexOf(task); if (index >= 0) queue.splice(index, 1); }
    settle(task, abortError());
  }
  return {
    peek(book) { return cached(bookKey(book)); },
    load(book, signal) {
      if (destroyed || signal?.aborted) return Promise.reject(abortError());
      const key = bookKey(book), value = cached(key);
      if (value !== undefined) return Promise.resolve(value);
      let task = tasks.get(key);
      if (!task) {
        task = {key, book, controller: new AbortController(), consumers: new Set(), started: false};
        tasks.set(key, task); queue.push(task);
      }
      return new Promise((resolve, reject) => {
        const consumer = {resolve, reject, signal, abort: null};
        consumer.abort = () => {
          signal?.removeEventListener('abort', consumer.abort);
          task.consumers.delete(consumer); reject(abortError());
          if (!task.consumers.size) cancelTask(task);
          pump();
        };
        task.consumers.add(consumer);
        signal?.addEventListener('abort', consumer.abort, {once: true});
        pump();
      });
    },
    cancelAll() { for (const task of [...tasks.values()]) cancelTask(task); queue.length = 0; },
    destroy() { destroyed = true; this.cancelAll(); cache.clear(); },
    stats() { return {active: running.size, pending: queue.length, cached: cache.size}; },
  };
}

const node = (tag, className, text) => {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
};
const button = (text, className, callback) => {
  const element = node('button', className, text);
  element.type = 'button'; element.addEventListener('click', callback);
  return element;
};
const safeText = value => typeof value === 'string' ? value.trim() : '';

export function createSearchView({root, api, imageUrl, onOpenBook, onReadChapter, onMetrics}) {
  const loader = createDetailLoader(api), cards = new Map(), authorEvidence = new Map();
  let keyword = '', model = null, groups = [], filter = '', generation = 0, destroyed = false;
  let primary, related, others, empty;
  const observer = typeof IntersectionObserver === 'function' ? new IntersectionObserver(entries => {
    for (const entry of entries) {
      const card = entry.target.searchCard;
      if (!card) continue;
      card.visible = entry.isIntersecting;
      if (card.visible && card.mounted) ensureDetail(card);
    }
  }, {rootMargin: '240px 0px', threshold: 0}) : null;

  function makeSection(className, label) {
    const details = node('details', className), summary = node('summary'), body = node('div', 'search-section-list');
    summary.append(node('span', 'search-section-label', label), node('span', 'search-section-count'));
    details.append(summary, body);
    const section = {details, summary, body, limit: 24};
    details.addEventListener('toggle', () => { if (model && !destroyed) renderLists(); });
    return section;
  }
  function mountRoot() {
    root.classList.add('search-results-list');
    primary = node('div', 'search-primary');
    related = makeSection('search-fold search-related', '相关作品');
    others = makeSection('search-fold search-others', '其他搜索结果');
    empty = node('div', 'search-empty'); empty.setAttribute('role', 'status');
    root.replaceChildren(primary, empty, related.details, others.details);
  }
  function selectedBook(card) { return card.work.books.find(book => bookKey(book) === card.selectedKey) || card.work.preferred; }
  function enrichedBook(card) {
    const book = selectedBook(card), detail = card.detail;
    if (!detail) return book;
    return {...book, ...Object.fromEntries(['title', 'coverUrl', 'author', 'description', 'status'].flatMap(field => safeText(detail[field]) ? [[field, detail[field]]] : []))};
  }
  function openBook(card) { onOpenBook(enrichedBook(card), card.detail || undefined); }
  function metrics() {
    return {rawCount: model.rawCount, workCount: model.workCount, relatedCount: model.relatedCount, hiddenCount: model.hiddenCount};
  }
  function rebuildModel() {
    model = buildSearchResults(withAuthorEvidence(groups, authorEvidence), keyword, filter);
    const remapped = rekeyWorkStates([...model.works, ...model.related, ...model.hidden], cards.values());
    const retained = new Set(remapped.values());
    for (const card of cards.values()) {
      if (!retained.has(card)) { card.mounted = false; observer?.unobserve(card.article); stopCard(card); }
    }
    cards.clear(); for (const [key, card] of remapped) cards.set(key, card);
  }
  function recordDetailAuthor(book, detail) {
    if (!normalizeAuthor(detail?.author)) return false;
    authorEvidence.set(bookKey(book), detail.author);
    return normalizeAuthor(book.author) !== normalizeAuthor(detail.author);
  }

  function makeCard(work) {
    const article = node('article', 'search-work');
    const card = {article, work, selectedKey: bookKey(work.preferred), userSelected: false, reverse: false,
      detail: null, status: 'idle', error: '', request: null, visible: false, mounted: false,
      coverSrc: '', sourceButtons: new Map(), renderedDetail: null, renderedReverse: null};
    article.searchCard = card;
    card.cover = button('', 'search-cover', () => openBook(card));
    card.fallback = node('span', 'search-cover-placeholder');
    card.cover.append(card.fallback);
    const content = node('div', 'search-work-content'), summary = node('div', 'search-work-summary'), heading = node('div', 'search-work-heading');
    const h2 = node('h2'); card.title = button('', 'search-work-title', () => openBook(card)); h2.append(card.title);
    card.match = node('span', 'search-match'); heading.append(h2, card.match);
    card.meta = node('div', 'search-work-meta');
    card.description = node('p', 'search-description');
    card.sources = node('div', 'search-source-list'); card.sources.setAttribute('aria-label', '选择漫画源');
    card.sourceFold = node('details', 'search-source-fold'); card.sourceLabel = node('summary'); card.sourceFold.append(card.sourceLabel, card.sources);
    card.chapterArea = node('section', 'search-chapter-area');
    summary.append(heading, card.meta, card.description);
    content.append(summary, card.sourceFold, card.chapterArea);
    article.append(card.cover, content);
    return card;
  }
  function stopCard(card) {
    card.request?.abort(); card.request = null;
    if (card.status === 'loading') card.status = 'idle';
  }
  function setSelection(card, key, userSelected = true) {
    if (key === card.selectedKey) { if (userSelected) card.userSelected = true; return; }
    stopCard(card); card.selectedKey = key; card.userSelected = userSelected;
    card.detail = null; card.status = 'idle'; card.error = '';
    card.renderedDetail = null; card.renderedReverse = null;
  }
  function renderSources(card) {
    card.sourceLabel.textContent = `${selectedBook(card).siteName} · 更换来源（${card.work.sourceCount} 个源）`;
    const seen = new Map(), totals = new Map(), live = new Set();
    for (const book of card.work.books) totals.set(book.siteId, (totals.get(book.siteId) || 0) + 1);
    let cursor = card.sources.firstChild;
    for (const book of card.work.books) {
      const key = bookKey(book), nth = (seen.get(book.siteId) || 0) + 1; seen.set(book.siteId, nth); live.add(key);
      let source = card.sourceButtons.get(key);
      if (!source) {
        source = button('', 'search-source', () => {
          setSelection(card, key); renderCard(card); ensureDetail(card);
        });
        card.sourceButtons.set(key, source);
      }
      const total = totals.get(book.siteId);
      const label = `${book.siteName}${total > 1 ? ` · 条目 ${nth}` : ''}`;
      const author = metadataText(book.author);
      source.replaceChildren(node('span', 'search-source-name', label));
      if (card.work.hasDifferentCredits) source.append(node('small', 'search-source-credit', author || '作者未提供'));
      source.title = [total > 1 ? `同一来源的第 ${nth} / ${total} 个条目` : book.siteName,
        `源站书名：${book.title}`, author ? `作者：${author}` : '', book.latestChapter ? `最新章节：${book.latestChapter}` : ''].filter(Boolean).join('\n');
      source.setAttribute('aria-label', [total > 1 ? `${book.siteName}，条目 ${nth} / ${total}，${book.title}` : book.siteName,
        card.work.hasDifferentCredits && author ? `作者：${author}` : ''].filter(Boolean).join('，'));
      source.setAttribute('aria-pressed', String(key === card.selectedKey));
      source.dataset.site = book.siteId;
      if (source === cursor) cursor = cursor.nextSibling;
      else card.sources.insertBefore(source, cursor);
    }
    for (const [key, source] of card.sourceButtons) {
      if (!live.has(key)) { source.remove(); card.sourceButtons.delete(key); }
    }
  }
  function renderMetadata(card) {
    const book = enrichedBook(card), work = card.work;
    card.title.textContent = work.title;
    card.cover.setAttribute('aria-label', `查看《${work.title}》完整目录`);
    card.fallback.textContent = work.title;
    card.match.textContent = work.kind === 'alias' ? '别名匹配' : work.score >= 98 ? `${work.sourceCount} 个源` : '';
    card.match.hidden = !card.match.textContent;
    const items = [];
    if (metadataText(book.author)) items.push(node('span', 'search-author', `作者：${metadataText(book.author)}`));
    if (safeText(book.edition)) items.push(node('span', 'search-edition', metadataText(book.edition)));
    if (safeText(book.language)) items.push(node('span', 'search-language', metadataText(book.language)));
    if (safeText(book.status)) items.push(node('span', 'search-status', book.status));
    if (safeText(book.latestChapter)) items.push(node('span', 'search-latest', `更新至 ${book.latestChapter}`));
    card.meta.replaceChildren(...items); card.meta.hidden = !items.length;
    card.description.textContent = safeText(book.description); card.description.hidden = !card.description.textContent;
    // A same-title display group can include genuinely different works. Do not
    // borrow a cover from a conflicting identity when this entry lacks one.
    const identity = work.identityByBook.get(bookKey(book));
    const coverBook = safeText(book.coverUrl) ? book : work.books.find(item => safeText(item.coverUrl)
      && work.identityByBook.get(bookKey(item)) === identity);
    const coverUrl = coverBook && /^https?:\/\//.test(coverBook.coverUrl) ? imageUrl(coverBook.coverUrl, coverBook.siteId) : '';
    if (coverUrl !== card.coverSrc) {
      card.coverSrc = coverUrl; card.cover.querySelector('img')?.remove(); card.fallback.hidden = false;
      if (coverUrl) {
        const img = node('img'); img.alt = ''; img.loading = 'lazy'; img.decoding = 'async'; img.referrerPolicy = 'no-referrer';
        img.addEventListener('load', () => { if (card.coverSrc === coverUrl) card.fallback.hidden = true; });
        img.addEventListener('error', () => { img.remove(); });
        img.src = coverUrl; card.cover.append(img);
      }
    }
  }
  function renderChapters(card) {
    const area = card.chapterArea, book = selectedBook(card);
    const restoreSortFocus = document.activeElement?.classList.contains('search-order') && area.contains(document.activeElement);
    area.setAttribute('aria-label', `${book.siteName}的章节目录`);
    area.setAttribute('aria-busy', String(card.status === 'loading'));
    if (card.status !== 'ready') {
      card.renderedDetail = null;
      const row = node('div', 'search-chapter-message');
      if (card.status === 'loading') row.append(node('span', 'search-detail-loading', `正在加载${book.siteName}的目录…`));
      else if (card.status === 'error') {
        const message = node('div');
        message.append(node('p', '', `${book.siteName}目录加载失败`), node('small', '', card.error));
        row.append(message, button('重试目录', 'search-text-button', () => { card.status = 'idle'; ensureDetail(card); }));
      } else row.append(button('查看章节', 'search-text-button', () => ensureDetail(card)));
      area.replaceChildren(row); return;
    }
    if (card.renderedDetail === card.detail && card.renderedReverse === card.reverse) return;
    card.renderedDetail = card.detail; card.renderedReverse = card.reverse;
    const chapters = Array.isArray(card.detail.chapters) ? card.detail.chapters : [];
    const tools = node('div', 'search-chapter-tools'), heading = node('div', 'search-chapter-heading');
    heading.append(node('strong', '', '章节目录'), node('span', '', `${chapters.length} 章`));
    const sort = button(card.reverse ? '倒序' : '正序', 'search-order', () => { card.reverse = !card.reverse; renderChapters(card); });
    sort.setAttribute('aria-label', card.reverse ? '当前倒序，切换为正序' : '当前正序，切换为倒序');
    sort.setAttribute('aria-pressed', String(card.reverse));
    tools.append(heading, sort);
    const chapterGrid = node('div', 'search-chapters');
    const visible = card.reverse ? chapters.slice(-12).reverse() : chapters.slice(0, 12);
    for (const chapter of visible) {
      const chapterButton = button(chapter.name || '未命名章节', 'search-chapter', () => onReadChapter(enrichedBook(card), chapter, card.detail));
      chapterButton.title = chapter.name || '未命名章节'; chapterGrid.append(chapterButton);
    }
    if (chapters.length) {
      const all = button(`全部 ${chapters.length} 章`, 'search-all-chapters', () => openBook(card));
      chapterGrid.append(all);
    } else chapterGrid.append(node('p', 'search-no-chapters', safeText(card.detail.unavailableReason) || '这个源暂未提供章节，请切换上方其他源。'));
    area.replaceChildren(tools, chapterGrid);
    if (chapters.length && safeText(card.detail.unavailableReason)) {
      area.prepend(node('p', 'search-no-chapters', safeText(card.detail.unavailableReason)));
    }
    if (restoreSortFocus) sort.focus({preventScroll: true});
  }
  function renderCard(card) {
    const book = selectedBook(card);
    if (!card.detail) {
      const cached = loader.peek(book);
      if (cached) { card.detail = cached; card.status = 'ready'; }
    }
    renderMetadata(card); renderSources(card); renderChapters(card);
  }
  async function ensureDetail(card) {
    if (destroyed || !card.mounted || card.status !== 'idle') return;
    const controller = new AbortController(), requestGeneration = generation, selected = card.selectedKey;
    card.request = controller; card.status = 'loading'; card.error = ''; renderChapters(card);
    try {
      const detail = await loader.load(selectedBook(card), controller.signal);
      if (controller.signal.aborted || generation !== requestGeneration || selected !== card.selectedKey || destroyed) return;
      const book = selectedBook(card);
      card.detail = detail; card.status = 'ready'; card.request = null;
      if (recordDetailAuthor(book, detail)) {
        rebuildModel(); renderLists(); onMetrics?.(metrics());
      } else renderCard(card);
    } catch (error) {
      if (controller.signal.aborted || generation !== requestGeneration || selected !== card.selectedKey || destroyed) return;
      card.status = 'error'; card.error = error?.message || '请重试或选择其他源'; card.request = null; renderChapters(card);
    }
  }
  function getCard(work, used) {
    let card = cards.get(work.key);
    if (!card || used.has(card)) {
      // Metadata arriving from a new source can reveal an author and refine a
      // work key. Preserve a chosen source when that identity is refined.
      card = [...cards.values()].find(item => !used.has(item) && item.work.canonicalTitle === work.canonicalTitle
        && work.books.some(book => bookKey(book) === item.selectedKey));
      if (card) {
        for (const [key, value] of cards) if (value === card) cards.delete(key);
      } else card = makeCard(work);
      cards.set(work.key, card);
    }
    card.work = work;
    if (!work.books.some(book => bookKey(book) === card.selectedKey)) setSelection(card, bookKey(work.preferred), false);
    else if (!card.userSelected) setSelection(card, bookKey(work.preferred), false);
    used.add(card); card.mounted = true; renderCard(card);
    return card;
  }
  function reconcile(parent, works, used) {
    const live = new Set(); let cursor = parent.firstChild;
    for (const work of works) {
      const card = getCard(work, used); live.add(card.article);
      if (card.article === cursor) cursor = cursor.nextSibling;
      else parent.insertBefore(card.article, cursor);
      observer?.observe(card.article);
      if (card.visible || !observer) ensureDetail(card);
    }
    for (const child of [...parent.children]) if (!live.has(child)) child.remove();
  }
  function renderFold(section, works, used) {
    section.details.hidden = !works.length;
    section.summary.querySelector('.search-section-count').textContent = `${works.length} 部`;
    if (!section.details.open) { section.body.replaceChildren(); return; }
    reconcile(section.body, works.slice(0, section.limit), used);
    if (works.length > section.limit) section.body.append(button(`再显示 ${Math.min(24, works.length - section.limit)} 部`, 'search-show-more', () => { section.limit += 24; renderLists(); }));
  }
  function renderLists() {
    if (!model || destroyed) return;
    const focused = document.activeElement;
    const used = new Set();
    reconcile(primary, model.works, used); renderFold(related, model.related, used); renderFold(others, model.hidden, used);
    for (const card of cards.values()) {
      if (!used.has(card)) { card.mounted = false; card.visible = false; observer?.unobserve(card.article); stopCard(card); }
    }
    empty.hidden = !!model.works.length;
    if (!empty.hidden) {
      const selected = groups.filter(group => !filter || group.siteId === filter);
      const loading = selected.some(group => group.loading);
      empty.textContent = loading ? '正在查找准确匹配的作品…' : model.relatedCount
        ? '暂未找到完全匹配的标题，可以展开下方相关作品。'
        : model.hiddenCount ? '暂未找到匹配的标题，可以展开其他结果，或换个名称试试。'
        : selected.some(group => group.error) ? '此漫画源暂不可用，请重试或切换其他源。' : '暂时没有找到这本漫画，试试简称或其他名称。';
    }
    if (focused && root.contains(focused) && document.activeElement !== focused) focused.focus({preventScroll: true});
  }
  mountRoot();
  return {
    update(next) {
      if (destroyed) return {rawCount: 0, workCount: 0, relatedCount: 0, hiddenCount: 0};
      if (keyword !== next.keyword) this.reset();
      keyword = next.keyword || ''; groups = next.groups || []; filter = next.filter || '';
      for (const group of groups) {
        for (const book of group.results || []) {
          if (book && typeof book === 'object') {
            const sourceBook = {...book, siteId: group.siteId || book.siteId};
            recordDetailAuthor(sourceBook, loader.peek(sourceBook));
          }
        }
      }
      rebuildModel(); renderLists(); return metrics();
    },
    reset() {
      generation++; loader.cancelAll(); observer?.disconnect();
      for (const card of cards.values()) { card.mounted = false; card.visible = false; stopCard(card); }
      cards.clear(); authorEvidence.clear(); model = null; keyword = ''; groups = []; filter = ''; mountRoot();
    },
    destroy() {
      this.reset(); destroyed = true; loader.destroy(); observer?.disconnect(); root.replaceChildren(); root.classList.remove('search-results-list');
    },
  };
}
