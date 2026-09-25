import {DISCOVERY_KINDS, createDiscoveryModel, createDiscoveryPagination, externalUrl} from './discovery-model.js';
import {createDiscoveryCoverLoader} from './discovery-covers.js';

const node = (tag, className, text) => {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
};
const button = (text, className, action) => {
  const element = node('button', className, text); element.type = 'button';
  element.addEventListener('click', action); return element;
};

function timestamp(value, year = false) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : new Intl.DateTimeFormat('zh-CN', {
    ...(year ? {year: 'numeric'} : {}), month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(date);
}

export function createDiscovery({root, api, imageUrl, onOpenBook, embedded = false}) {
  root.classList.add('discovery'); root.hidden = true;
  const inner = node('div', 'discovery-inner');
  const header = node('header', 'discovery-heading');
  const heading = node('h2', '', '发现漫画'); heading.id = 'discovery-title';
  if (embedded) root.classList.add('discovery-embedded'); else root.setAttribute('aria-labelledby', heading.id);
  header.append(heading, node('p', '', '看看热门作品与最近更新'));

  const controls = node('div', 'discovery-controls');
  const kinds = node('div', 'discovery-kinds'); kinds.setAttribute('role', 'group'); kinds.setAttribute('aria-label', '发现类型');
  const modeButtons = new Map();
  for (const [kind, label] of Object.entries(DISCOVERY_KINDS)) {
    const choice = button(label, 'discovery-kind', () => model.select({kind, period: ''}));
    choice.dataset.kind = kind; kinds.append(choice); modeButtons.set(kind, choice);
  }
  const filters = node('div', 'discovery-filters');
  const sourceLabel = node('label', 'discovery-select');
  const sourceLabelText = node('span', '', '榜单来源'); sourceLabel.append(sourceLabelText);
  const sourceSelect = node('select'); sourceSelect.id = 'discovery-source'; sourceLabel.append(sourceSelect);
  sourceSelect.addEventListener('change', () => model.select({siteId: sourceSelect.value, period: ''}));
  const periodLabel = node('label', 'discovery-select'); periodLabel.append(node('span', '', '周期'));
  const periodSelect = node('select'); periodSelect.id = 'discovery-period'; periodLabel.append(periodSelect);
  periodSelect.addEventListener('change', () => model.select({period: periodSelect.value}));
  const refresh = button('刷新', 'discovery-refresh', () => model.refresh()); refresh.id = 'discovery-refresh';
  filters.append(sourceLabel, periodLabel, refresh);
  if (!embedded) controls.append(kinds);
  controls.append(filters);

  const context = node('div', 'discovery-context');
  const listLabel = node('p', 'discovery-list-label'); listLabel.id = 'discovery-list-label';
  listLabel.tabIndex = -1; listLabel.setAttribute('role', 'heading'); listLabel.setAttribute('aria-level', '3');
  const provenance = node('div', 'discovery-provenance');
  const origin = node('a', '', '查看源站'); origin.target = '_blank'; origin.rel = 'noopener noreferrer';
  const fetched = node('time'); provenance.append(origin, fetched); context.append(listLabel, provenance);
  const scopeNote = node('p', 'discovery-scope-note'); scopeNote.hidden = true;

  const status = node('div', 'discovery-status'); status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
  const statusText = node('p');
  const retry = button('重试', 'discovery-retry', () => model.retry()); status.append(statusText, retry);
  const grid = node('ul', 'discovery-grid'); grid.id = 'discovery-grid'; grid.setAttribute('aria-label', '发现作品');
  const pagination = node('nav', 'discovery-pagination'); pagination.setAttribute('aria-label', '发现列表翻页');
  const previous = button('上一页', '', () => paginate('previous')); previous.id = 'discovery-previous';
  const pageNumber = node('span'); pageNumber.id = 'discovery-page';
  const next = button('下一页', '', () => paginate('next')); next.id = 'discovery-next';
  pagination.append(previous, pageNumber, next);
  if (!embedded) inner.append(header);
  inner.append(controls, context, scopeNote, status, grid, pagination); root.replaceChildren(inner);

  let renderedData = null;
  const covers = createDiscoveryCoverLoader({api});
  const pendingCovers = new WeakMap();
  const coverObserver = new IntersectionObserver(entries => {
    for (const entry of entries) if (entry.isIntersecting) {
      coverObserver.unobserve(entry.target); pendingCovers.get(entry.target)?.();
    }
  }, {rootMargin: '160px 0px'});
  function makeCard(book, kind) {
    const card = node('li', 'discovery-card');
    const frame = node('div', 'discovery-cover-frame');
    const cover = button('', 'discovery-cover', () => onOpenBook(book));
    cover.setAttribute('aria-label', `打开《${book.title}》`);
    const placeholder = node('span', 'discovery-cover-placeholder', '封面加载中'); cover.append(placeholder);
    const coverRetry = button('重试封面', 'discovery-cover-retry', () => requestCover(true));
    coverRetry.setAttribute('aria-label', `重试《${book.title}》的封面`); coverRetry.hidden = true;
    let image = null, revision = 0, retryCount = 0;
    function failed(error) {
      image?.remove(); image = null; placeholder.hidden = false; placeholder.textContent = '封面暂不可用';
      placeholder.title = error?.message || '源站封面未能加载'; coverRetry.hidden = false;
    }
    function showImage(url) {
      const token = ++revision; image?.remove();
      placeholder.hidden = false; placeholder.textContent = '封面加载中'; placeholder.removeAttribute('title'); coverRetry.hidden = true;
      book.coverUrl = url;
      image = node('img'); image.alt = ''; image.loading = 'lazy'; image.decoding = 'async'; image.referrerPolicy = 'no-referrer';
      image.addEventListener('load', () => {if (token === revision) placeholder.hidden = true;});
      image.addEventListener('error', () => {if (token === revision) failed();});
      const proxy = new URL(imageUrl(url, book.siteId), document.baseURI);
      if (retryCount) proxy.searchParams.set('coverRetry', String(retryCount));
      image.src = proxy.href; cover.append(image);
    }
    function requestCover(refresh = false) {
      if (refresh) retryCount++;
      placeholder.textContent = '封面加载中'; coverRetry.hidden = true;
      if (book.coverLookup === false) {
        if (externalUrl(book.coverUrl)) showImage(book.coverUrl);
        else {failed(new Error('来源未提供封面')); coverRetry.hidden = true;}
        return;
      }
      covers.request(book, {refresh, onLoad: showImage, onError: failed});
    }
    if (externalUrl(book.coverUrl)) showImage(book.coverUrl);
    else if (book.coverLookup === false) {placeholder.textContent = '暂无封面';}
    else {pendingCovers.set(card, requestCover); coverObserver.observe(card);}
    if (kind === 'popular' && book.rank) {
      const rank = node('span', 'discovery-rank', String(book.rank)); rank.setAttribute('aria-label', `第 ${book.rank} 名`); cover.append(rank);
    }
    const title = node('h3'); title.append(button(book.title, 'discovery-book-title', () => onOpenBook(book)));
    frame.append(cover, coverRetry); card.append(frame, title);
    if (book.latestChapter) card.append(node('p', 'discovery-chapter', book.latestChapter));
    if (kind === 'latest' && book.updatedAtText) {
      const at = /^\d{4}-\d\d-\d\dT/.test(book.updatedAtText) ? timestamp(book.updatedAtText, true) : '';
      card.append(node('p', 'discovery-updated', at || book.updatedAtText));
    }
    else if (!book.latestChapter && book.author) card.append(node('p', 'discovery-author', book.author));
    return card;
  }
  function options(select, values, value) {
    const signature = JSON.stringify(values);
    if (select.dataset.options !== signature) {
      select.replaceChildren(...values.map(item => {
        const option = node('option', '', item.label); option.value = item.id; return option;
      }));
      select.dataset.options = signature;
    }
    select.value = value || '';
  }
  function render(state) {
    root.hidden = !state.visible;
    if (!state.visible) {covers.pause(); return;}
    const busy = ['sources-loading', 'loading'].includes(state.phase);
    const selection = state.selection, data = state.data;
    sourceLabelText.textContent = selection?.kind === 'latest' ? '更新来源' : '榜单来源';
    const source = state.sources.find(item => item.siteId === selection?.siteId);
    const mode = source?.modes.find(item => item.kind === selection?.kind);
    for (const [kind, choice] of modeButtons) {
      choice.disabled = !state.sources.some(item => item.modes.some(value => value.kind === kind));
      choice.setAttribute('aria-pressed', String(selection?.kind === kind));
    }
    options(sourceSelect, state.sources.filter(item => item.modes.some(value => value.kind === selection?.kind))
      .map(item => ({id: item.siteId, label: item.siteName})), selection?.siteId);
    sourceSelect.disabled = !source;
    options(periodSelect, mode?.periods || [], selection?.period);
    periodLabel.hidden = !mode?.periods.length;
    refresh.disabled = busy;

    context.hidden = !selection;
    listLabel.textContent = data?.label || [source?.siteName, mode?.label, mode?.periods.find(item => item.id === selection?.period)?.label].filter(Boolean).join(' · ');
    origin.hidden = !data?.sourceUrl;
    if (data?.sourceUrl) origin.href = data.sourceUrl; else origin.removeAttribute('href');
    const at = timestamp(data?.fetchedAt || '');
    fetched.hidden = !at; fetched.textContent = at ? `获取于 ${at}` : '';
    if (at) {fetched.dateTime = data.fetchedAt; fetched.title = new Date(data.fetchedAt).toLocaleString('zh-CN');}
    else {fetched.removeAttribute('datetime'); fetched.removeAttribute('title');}
    scopeNote.textContent = data?.paginationNote || ''; scopeNote.hidden = !scopeNote.textContent;

    status.hidden = state.phase === 'ready' && Boolean(data?.items.length);
    status.classList.toggle('discovery-status-error', state.phase === 'error');
    retry.hidden = state.phase !== 'error';
    if (state.phase === 'sources-loading') statusText.textContent = '正在读取发现来源…';
    else if (state.phase === 'loading') statusText.textContent = `正在读取${source?.siteName || ''}${DISCOVERY_KINDS[selection?.kind] || '列表'}…`;
    else if (state.phase === 'error') statusText.textContent = `加载失败：${state.error}`;
    else if (state.phase === 'unavailable') statusText.textContent = '暂时没有可用的发现来源，可以先搜索想看的漫画。';
    else if (state.phase === 'ready') statusText.textContent = '当前列表还没有作品，试试其他来源或周期。';
    else statusText.textContent = '选择来源，发现新故事。';

    grid.setAttribute('aria-busy', String(busy));
    if (renderedData !== data) {
      coverObserver.disconnect(); covers.clear();
      const items = data?.items || [];
      grid.replaceChildren(...items.map(book => makeCard(book, selection.kind)));
      renderedData = data;
    }
    covers.resume();
    pagination.hidden = !selection || state.phase === 'sources-loading' || state.phase === 'unavailable';
    previous.disabled = busy || !selection || selection.page <= 1;
    next.disabled = busy || state.phase !== 'ready' || !data?.hasMore;
    pageNumber.textContent = selection ? `第 ${selection.page} 页` : '';
  }
  const model = createDiscoveryModel({api, onChange: render});
  let pageFocus = null;
  function paginate(direction) {
    pageFocus = root.ownerDocument.activeElement;
    return pager[direction]();
  }
  const pager = createDiscoveryPagination({model, onPageReady: (_data, direction) => {
    const document = root.ownerDocument, trigger = direction === 'next' ? next : previous;
    // A modal or a deliberate focus change takes precedence over a pending page request.
    if (!root.isConnected || root.hidden || document.querySelector('dialog[open]')) return;
    if (document.activeElement !== trigger && document.activeElement !== pageFocus && document.activeElement !== document.body) return;
    listLabel.focus({preventScroll: true});
    context.scrollIntoView({block: 'start', behavior: 'auto'});
  }});
  return {show: options => model.show(options), hide: () => model.hide()};
}
