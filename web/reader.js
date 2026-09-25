import {MAX_IMAGE_REQUESTS, clampPage, clampOffset, restorePosition, normalizeRatio, imageCandidates, progressSnapshot, createChapterScope, normalizeReaderPreferences, readerPreferencesForBook, pageNavigation, pageTurnDelta, readerPageWidth, clampReaderZoom, chapterNavigation, buildReaderPages} from './reader-model.js';
import {prefetchPageCount} from './reader-model.js';
import {createImageLoader} from './image-loader.js';
import {sourceEntryKey} from './book-identity.js';

const PREFS_KEY = 'revyunman.reader.preferences.v2';
const BOOK_PREFS_KEY = 'revyunman.reader.books.v1';
const SIZES_KEY = 'revyunman.reader.dimensions.v1';
const make = (tag, className = '', text) => {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
};
const action = (id, text, callback, label) => {
  const button = make('button', 'ry-reader-button', text);
  button.id = id; button.type = 'button'; button.onclick = callback;
  if (label) {button.setAttribute('aria-label', label); button.title = label;}
  return button;
};
const stored = (name, fallback) => {
  try {return JSON.parse(localStorage.getItem(name)) ?? fallback;} catch {return fallback;}
};
const editable = target => target instanceof Element && !!target.closest('input, select, textarea, [contenteditable="true"]');

/** The application owns routing and the shelf. This module owns one reader session. */
export function createReader({root, api, imageUrl, imageLoader = createImageLoader(), downloads = null, getProgress, onProgress, onNavigate, onExit, isChapterRead = () => false, onChapterRead, toast = () => {}}) {
  if (!root) throw new Error('缺少阅读器容器');
  let preferences = normalizeReaderPreferences(stored(PREFS_KEY, null), {
    light: stored('revyunman.reader.light', false),
    width: stored('revyunman.reader.width', 800),
  });
  let {theme, width, focused, mode, direction, fit, zoom, prefetch} = preferences;
  let dimensions = stored(SIZES_KEY, {});
  if (!dimensions || typeof dimensions !== 'object' || Array.isArray(dimensions)) dimensions = {};
  const scope = createChapterScope();
  let session = null, catalogOpen = false, settingsOpen = false, catalogReverse = false, drag = null;

  root.classList.add('reader-shell');
  root.setAttribute('aria-label', '漫画阅读器');
  root.replaceChildren();
  const header = make('header', 'ry-reader-header');
  const back = action('reader-back', '返回', requestExit, '返回漫画详情');
  const heading = make('div', 'ry-reader-heading');
  const title = make('strong'); title.id = 'reader-title';
  const chapterLabel = make('span'); chapterLabel.id = 'reader-chapter';
  const loadStatus = make('span', 'ry-reader-load-status'); loadStatus.id = 'reader-load-status';
  loadStatus.setAttribute('role', 'status');
  heading.append(title, chapterLabel, loadStatus);
  const settingsButton = action('reader-settings-open', '设置', () => setSettings(true), '阅读设置');
  settingsButton.setAttribute('aria-expanded', 'false'); settingsButton.setAttribute('aria-controls', 'reader-settings');
  const focusButton = action('reader-focus', '沉浸', () => setFocused(!focused), '进入沉浸阅读');
  const zoomButton = action('reader-zoom-toggle', '放大', () => setZoom(zoom === 1 ? 2 : 1));
  header.append(back, heading, zoomButton, settingsButton, focusButton);

  const scroll = make('div', 'ry-reader-scroll'); scroll.id = 'reader-scroll'; scroll.tabIndex = 0;
  scroll.setAttribute('aria-label', '漫画页面，可滚动阅读');
  const canvas = make('div', 'ry-reader-canvas'); canvas.id = 'reader-pages';
  const ending = make('div', 'ry-reader-end'); ending.hidden = true;
  const endLabel = make('p'); endLabel.id = 'chapter-end-label';
  const nextEnd = action('end-next', '继续下一章', () => moveChapter(1));
  nextEnd.classList.add('ry-reader-primary'); ending.append(endLabel, nextEnd); scroll.append(canvas, ending);

  const toolbar = make('div', 'ry-reader-toolbar'); toolbar.setAttribute('aria-label', '阅读操作');
  const prev = action('prev-chapter', '上一章', () => moveChapter(-1));
  const catalogButton = action('reader-catalog-open', '目录', () => setCatalog(true), '查找或切换章节');
  catalogButton.setAttribute('aria-expanded', 'false'); catalogButton.setAttribute('aria-controls', 'reader-catalog');
  const next = action('next-chapter', '下一章', () => moveChapter(1));
  const readButton = action('reader-mark-read', '标为已读', toggleChapterRead);
  readButton.hidden = typeof onChapterRead !== 'function';
  const chapterControls = make('div', 'ry-reader-chapter-controls'); chapterControls.append(prev, catalogButton, next, readButton);
  const downloadButton = action('reader-download', '下载', () => {if (session) downloads?.open({book: session.book, chapters: session.chapters, chapter: session.chapter});}, '下载章节，稍后离线阅读');
  downloadButton.hidden = !downloads; chapterControls.append(downloadButton);
  const pageControls = make('div', 'ry-reader-page-controls');
  const prevPage = action('reader-prev-page', '上一页', () => movePage(-1));
  const nextPage = action('reader-next-page', '下一页', () => movePage(1));
  prevPage.hidden = nextPage.hidden = true;
  const jumpForm = make('form', 'ry-reader-page-jump'); jumpForm.setAttribute('aria-label', '跳转页码');
  const pageInput = make('input'); pageInput.id = 'reader-page-input'; pageInput.type = 'number'; pageInput.min = '1'; pageInput.value = '1';
  pageInput.inputMode = 'numeric'; pageInput.setAttribute('aria-label', '当前页码');
  const progressLabel = make('span'); progressLabel.id = 'page-progress'; progressLabel.textContent = '/ — 页';
  const jump = action('reader-jump', '跳页', () => {}); jump.type = 'submit';
  jumpForm.append(pageInput, progressLabel, jump); jumpForm.onsubmit = event => {event.preventDefault(); jumpToPage(Number(pageInput.value) - 1);};
  pageControls.append(prevPage, jumpForm, nextPage); toolbar.append(chapterControls, pageControls);
  const exitFocus = action('exit-focus', '显示工具栏', () => setFocused(false)); exitFocus.classList.add('ry-reader-focus-exit');

  const settings = make('section', 'ry-reader-settings'); settings.id = 'reader-settings'; settings.hidden = true;
  settings.setAttribute('role', 'dialog'); settings.setAttribute('aria-modal', 'true'); settings.setAttribute('aria-labelledby', 'reader-settings-title');
  const settingsHead = make('div', 'ry-reader-settings-head');
  const settingsTitle = make('h2', '', '阅读设置'); settingsTitle.id = 'reader-settings-title';
  const settingsClose = action('reader-settings-close', '完成', () => setSettings(false), '关闭阅读设置');
  settingsHead.append(settingsTitle, settingsClose);
  function choices(name, label, options, onChange) {
    const field = make('fieldset', 'ry-reader-setting'); field.append(make('legend', '', label));
    const inputs = options.map(([value, text]) => {
      const choice = make('label', 'ry-setting-choice');
      const input = make('input'); input.id = `${name}-${value}`; input.type = 'radio'; input.name = name; input.value = value;
      input.onchange = () => {if (input.checked) onChange(value);};
      choice.append(input, make('span', '', text)); field.append(choice);
      return input;
    });
    return {field, inputs};
  }
  const modes = choices('reader-mode', '阅读方式', [['continuous', '连续'], ['paged', '逐页']], setMode);
  const fits = choices('reader-fit', '页面尺寸', [['width', '适宽'], ['page', '整页']], setFit);
  const directions = choices('reader-direction', '翻页方向', [['ltr', '从左向右'], ['rtl', '从右向左']], value => {direction = value; applyPreferences(); savePreferences();});
  const themes = choices('reader-theme', '阅读背景', [['dark', '深色'], ['light', '浅色']], value => {theme = value; applyPreferences(); savePreferences();});
  const preloads = choices('reader-prefetch', '提前加载', [['auto', '标准'], ['more', '多预读'], ['off', '省流']], value => {prefetch = value; savePreferences(); if (session) queueImages(session);});
  const preloadHint = make('p', 'ry-reader-setting-hint', '标准提前加载 3–6 页，多预读 6–10 页；省流只加载可见页。Komiic 在标准模式下按需读取。');
  const modeHint = make('p', 'ry-reader-setting-hint', '逐页适合页漫，长图可切回连续阅读。');
  const widthLabel = make('label', 'ry-reader-width'); widthLabel.append(make('span', '', '页面宽度'));
  const widthValue = make('output'); widthValue.id = 'reader-width-value'; widthValue.setAttribute('for', 'reader-width'); widthLabel.append(widthValue);
  const widthInput = make('input'); widthInput.id = 'reader-width'; widthInput.type = 'range'; widthInput.min = '480'; widthInput.max = '1200'; widthInput.step = '20'; widthInput.value = String(width); widthInput.setAttribute('aria-label', '阅读宽度');
  widthLabel.append(widthInput);
  const zoomLabel = make('label', 'ry-reader-width ry-reader-zoom'); zoomLabel.append(make('span', '', '放大比例'));
  const zoomValue = make('output'); zoomValue.id = 'reader-zoom-value'; zoomValue.setAttribute('for', 'reader-zoom'); zoomLabel.append(zoomValue);
  const zoomInput = make('input'); zoomInput.id = 'reader-zoom'; zoomInput.type = 'range'; zoomInput.min = '100'; zoomInput.max = '300'; zoomInput.step = '25'; zoomInput.setAttribute('aria-label', '放大比例'); zoomInput.oninput = () => setZoom(Number(zoomInput.value) / 100); zoomLabel.append(zoomInput);
  const zoomHint = make('p', 'ry-reader-setting-hint ry-reader-zoom-hint', '双击图片放大或还原，放大后可拖动查看。手机也可双指缩放。');
  const preferenceNote = make('p', 'ry-reader-preference-note', '设置仅用于当前这本漫画。');
  const defaults = make('div', 'ry-reader-default-actions');
  defaults.append(action('reader-use-defaults', '恢复默认', useDefaults), action('reader-save-defaults', '设为默认', saveDefaults));
  settings.append(settingsHead, preferenceNote, modes.field, modeHint, directions.field, preloads.field, preloadHint, fits.field, zoomLabel, zoomHint, themes.field, widthLabel, defaults);

  const catalog = make('section', 'ry-reader-catalog'); catalog.id = 'reader-catalog'; catalog.hidden = true;
  catalog.setAttribute('role', 'dialog'); catalog.setAttribute('aria-modal', 'true'); catalog.setAttribute('aria-labelledby', 'reader-catalog-title');
  const catalogHead = make('div', 'ry-reader-catalog-head');
  const catalogTitle = make('h2', '', '章节目录'); catalogTitle.id = 'reader-catalog-title';
  const catalogClose = action('reader-catalog-close', '关闭', () => setCatalog(false), '关闭章节目录');
  catalogHead.append(catalogTitle, catalogClose);
  const catalogTools = make('div', 'ry-reader-catalog-tools');
  const catalogSearch = make('input'); catalogSearch.id = 'reader-chapter-search'; catalogSearch.type = 'search'; catalogSearch.placeholder = '输入章节名称或数字'; catalogSearch.setAttribute('aria-label', '查找章节');
  const catalogSort = action('reader-catalog-sort', '倒序', () => {catalogReverse = !catalogReverse; catalogSort.textContent = catalogReverse ? '正序' : '倒序'; renderCatalog();});
  catalogTools.append(catalogSearch, catalogSort);
  const catalogCount = make('p', 'ry-reader-catalog-count'); catalogCount.id = 'reader-catalog-count'; catalogCount.setAttribute('role', 'status');
  const catalogList = make('div', 'ry-reader-catalog-list'); catalogList.id = 'reader-chapter-list';
  catalog.append(catalogHead, catalogTools, catalogCount, catalogList);
  const shade = action('reader-catalog-shade', '', () => {if (settingsOpen) setSettings(false); else setCatalog(false);}, '关闭面板'); shade.className = 'ry-reader-catalog-shade'; shade.hidden = true; shade.tabIndex = -1;
  root.append(header, scroll, toolbar, exitFocus, shade, catalog, settings);

  function savePreferences() {
    if (!session) return;
    const raw = stored(BOOK_PREFS_KEY, {}), records = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {};
    try {localStorage.setItem(BOOK_PREFS_KEY, JSON.stringify({...records, [sourceEntryKey(session.book)]: currentPreferences()}));} catch {toast('当前浏览器无法保存阅读偏好');}
  }
  function currentPreferences() {return {theme, width, focused, mode, direction, fit, zoom, prefetch};}
  function loadPreferences(book) {
    preferences = normalizeReaderPreferences(stored(PREFS_KEY, preferences));
    ({theme, width, focused, mode, direction, fit, zoom, prefetch} = readerPreferencesForBook(preferences, stored(BOOK_PREFS_KEY, {}), sourceEntryKey(book)));
  }
  function saveDefaults() {
    preferences = currentPreferences();
    try {localStorage.setItem(PREFS_KEY, JSON.stringify(preferences)); toast('已设为其他漫画的默认阅读设置');} catch {toast('当前浏览器无法保存阅读偏好');}
  }
  function useDefaults() {
    const current = session, position = current?.pages.length ? current.restoring ? current.restoreTarget : locate(current) : null;
    if (!current) return;
    flush();
    const raw = stored(BOOK_PREFS_KEY, {}), records = raw && typeof raw === 'object' && !Array.isArray(raw) ? {...raw} : {};
    delete records[sourceEntryKey(current.book)];
    try {localStorage.setItem(BOOK_PREFS_KEY, JSON.stringify(records));} catch {toast('当前浏览器无法保存阅读偏好');}
    loadPreferences(current.book); applyPreferences();
    if (position) {setPosition(current, position); updateProgress(current); queueImages(current);}
  }
  function applyPreferences() {
    root.dataset.theme = theme; root.dataset.mode = mode; root.dataset.direction = direction; root.dataset.fit = fit; root.dataset.zoomed = String(zoom > 1); root.classList.toggle('ry-reader-focused', focused);
    root.style.setProperty('--ry-reader-width', `${width}px`);
    for (const input of modes.inputs) input.checked = input.value === mode;
    for (const input of directions.inputs) input.checked = input.value === direction;
    directions.field.hidden = mode !== 'paged';
    for (const input of themes.inputs) input.checked = input.value === theme;
    for (const input of preloads.inputs) input.checked = input.value === prefetch;
    for (const input of fits.inputs) input.checked = input.value === fit;
    widthInput.value = String(width); widthValue.textContent = `${width} px`;
    zoomInput.value = String(Math.round(zoom * 100)); zoomValue.textContent = `${Math.round(zoom * 100)}%`;
    zoomButton.textContent = zoom === 1 ? '放大' : `${Math.round(zoom * 100)}%`;
    zoomButton.setAttribute('aria-label', zoom === 1 ? '放大漫画至200%' : '还原漫画至100%'); zoomButton.title = zoomButton.getAttribute('aria-label');
    prevPage.hidden = nextPage.hidden = mode !== 'paged';
    scroll.setAttribute('aria-label', mode === 'paged' ? '漫画页面，可用左右方向键翻页' : '漫画页面，可滚动阅读');
    focusButton.setAttribute('aria-pressed', String(focused)); exitFocus.hidden = !focused;
  }
  function setFit(value) {
    const current = session, position = current?.pages.length ? current.restoring ? current.restoreTarget : locate(current) : null;
    flush(); fit = value === 'page' ? 'page' : 'width'; applyPreferences(); savePreferences();
    if (position) {setPosition(current, position); updateProgress(current); queueImages(current); scheduleSave(current);}
  }
  function setZoom(value, event = null) {
    const nextZoom = clampReaderZoom(value), current = session;
    if (nextZoom === zoom) return;
    const position = current?.pages.length ? current.restoring ? current.restoreTarget : locate(current) : null;
    const target = event?.target instanceof Element ? event.target.closest('.ry-reader-page') : current?.pages[position?.page]?.figure;
    const bounds = target?.getBoundingClientRect(), viewport = scroll.getBoundingClientRect();
    const x = event ? event.clientX - viewport.left : scroll.clientWidth / 2;
    const y = event ? event.clientY - viewport.top : scroll.clientHeight / 2;
    const anchor = bounds ? {x: clampOffset((viewport.left + x - bounds.left) / Math.max(1, bounds.width)), y: clampOffset((viewport.top + y - bounds.top) / Math.max(1, bounds.height))} : null;
    flush(); zoom = nextZoom; applyPreferences(); savePreferences();
    if (position && active(current)) {
      setPosition(current, position);
      if (anchor) {
        scroll.scrollLeft = canvas.offsetLeft + target.offsetLeft + target.offsetWidth * anchor.x - x;
        scroll.scrollTop = canvas.offsetTop + target.offsetTop + target.offsetHeight * anchor.y - y;
      }
      updateProgress(current); queueImages(current); scheduleSave(current);
    }
  }
  function setMode(value) {
    const nextMode = value === 'paged' ? 'paged' : 'continuous';
    if (nextMode === mode) return;
    const current = session;
    const position = current?.pages.length ? current.restoring ? current.restoreTarget : locate(current) : null;
    flush();
    if (position && mode === 'continuous') current.pageOffsets.set(position.page, position.pageOffset);
    mode = nextMode; applyPreferences(); savePreferences();
    if (position && active(current)) {
      current.restoreTarget = {...position}; current.restoring = current.pages[position.page].state !== 'loaded';
      setPosition(current, position); updateProgress(current);
      // Keep requests in the new visible/forward window; cancel distant work.
      queueImages(current); scheduleSave(current);
    }
  }
  function setFocused(value) {
    const position = session?.pages.length ? session.restoring ? session.restoreTarget : locate(session) : null;
    focused = value; applyPreferences(); savePreferences();
    if (position) setPosition(session, position);
    if (root.open) (focused ? exitFocus : focusButton).focus({preventScroll: true});
    if (session) requestFrame(session);
  }
  function active(current) {return session === current && scope.isCurrent(current?.scope) && root.open;}
  function requestExit() {flush(); onExit();}
  function dimensionsKey(current) {return `${sourceEntryKey(current.book)}::${current.chapter.url}`;}
  function saveDimensions(current) {
    if (!current?.pages.length) return;
    dimensions[dimensionsKey(current)] = {ratios: current.pages.map(page => page.ratio), at: Date.now()};
    const entries = Object.entries(dimensions).sort((a, b) => Number(b[1]?.at || 0) - Number(a[1]?.at || 0)).slice(0, 30);
    dimensions = Object.fromEntries(entries);
    try {localStorage.setItem(SIZES_KEY, JSON.stringify(dimensions));} catch { /* Position is saved independently of this optional layout cache. */ }
  }
  function clean(current) {
    if (!current) return;
    clearTimeout(current.saveTimer); clearTimeout(current.dimensionsTimer);
    cancelAnimationFrame(current.frame); current.resize?.disconnect();
    for (const page of current.pages) {
      page.ticket++;
      releaseImage(page, true);
    }
    current.inflight = 0;
    drag = null;
    saveDimensions(current);
  }
  function close() {
    flush(); const previous = session; scope.stop(); clean(previous); session = null;
    setCatalog(false, false); setSettings(false, false); canvas.replaceChildren(); ending.hidden = true;
    if (root.open) root.close();
  }
  function flush() {
    const current = session;
    if (!active(current) || !current.pages.length) return;
    const position = locate(current);
    const progress = progressSnapshot({chapter: current.chapter, total: current.pages.length, position, restoring: current.restoring, loaded: current.pages[position.page]?.state === 'loaded'});
    if (!progress) return;
    current.position = position;
    if (mode === 'continuous') current.pageOffsets.set(position.page, position.pageOffset);
    // Each callback carries the book snapshot from this chapter, never a shared global book.
    onProgress({...current.book}, progress);
  }
  function scheduleSave(current) {
    clearTimeout(current.saveTimer);
    current.saveTimer = setTimeout(() => {if (active(current)) flush();}, 350);
  }
  function pageAt(current, y) {
    let low = 0, high = current.pages.length - 1;
    while (low < high) {
      const middle = Math.ceil((low + high) / 2);
      if (current.pages[middle].figure.offsetTop <= y) low = middle;
      else high = middle - 1;
    }
    return low;
  }
  function locate(current) {
    if (mode === 'paged') return {...current.position};
    const page = pageAt(current, scroll.scrollTop);
    const figure = current.pages[page]?.figure;
    return {page, pageOffset: figure ? clampOffset((scroll.scrollTop - figure.offsetTop) / Math.max(1, figure.offsetHeight)) : 0};
  }
  function setPosition(current, position) {
    if (!active(current) || !current.pages.length) return;
    const page = clampPage(position.page, current.pages.length), figure = current.pages[page].figure;
    const oldRange = Math.max(0, scroll.scrollWidth - scroll.clientWidth);
    const pan = current.position.page === page && oldRange > 0 ? scroll.scrollLeft / oldRange : .5;
    current.position = {page, pageOffset: clampOffset(position.pageOffset)};
    layoutPages(current);
    scroll.scrollTop = mode === 'paged' && figure.offsetHeight <= scroll.clientHeight ? 0 : figure.offsetTop + figure.offsetHeight * current.position.pageOffset;
    scroll.scrollLeft = Math.max(0, scroll.scrollWidth - scroll.clientWidth) * pan;
  }
  function widthForPage(page, viewportWidth = scroll.clientWidth, viewportHeight = scroll.clientHeight) {
    // Undecoded long-strip placeholders must leave enough room for retry text.
    const ratio = page.state === 'loaded' ? page.ratio : fit === 'page' ? 1.42 : page.ratio;
    return readerPageWidth({ratio, viewportWidth, viewportHeight, preferredWidth: width, fit, zoom});
  }
  function layoutPages(current) {
    const paged = mode === 'paged', index = current.position.page;
    // Read geometry once before writing hundreds of page styles. Reading it
    // inside this loop forces a layout after each preceding page mutation.
    const viewportWidth = scroll.clientWidth, viewportHeight = scroll.clientHeight;
    const layoutKey = `${mode}:${fit}:${zoom}:${width}:${viewportWidth}:${viewportHeight}`;
    if (current.layoutKey !== layoutKey) {
      for (const page of current.pages) {
        page.figure.hidden = paged && page.index !== index;
        if (!paged) {page.figure.style.width = `${widthForPage(page, viewportWidth, viewportHeight)}px`; page.figure.style.aspectRatio = `1 / ${page.ratio}`;}
      }
    } else if (paged && current.visiblePage !== index) {
      if (current.pages[current.visiblePage]) current.pages[current.visiblePage].figure.hidden = true;
      current.pages[index].figure.hidden = false;
    }
    current.layoutKey = layoutKey; current.visiblePage = index;
    if (paged) {
      const page = current.pages[index];
      // A cached long-strip ratio must not squeeze its error and retry controls
      // into an unreadably thin column before the image has actually decoded.
      const ratio = page.state === 'loaded' ? page.ratio : 1.42;
      page.figure.style.aspectRatio = `1 / ${ratio}`;
      const pageWidth = widthForPage(page, viewportWidth, viewportHeight);
      page.figure.style.width = `${pageWidth}px`;
      canvas.style.width = `${Math.max(viewportWidth, pageWidth)}px`;
      canvas.style.height = `${Math.max(viewportHeight, pageWidth * ratio)}px`;
    } else {
      canvas.style.width = `${Math.min(viewportWidth, width) * zoom}px`;
      canvas.style.removeProperty('height');
    }
  }
  function updateProgress(current) {
    if (!active(current)) return;
    const position = current.restoring ? current.restoreTarget : locate(current);
    current.position = position;
    if (document.activeElement !== pageInput) pageInput.value = String(position.page + 1);
    pageInput.max = String(current.pages.length); progressLabel.textContent = `/ ${current.pages.length} 页`;
    const loaded = current.pages.filter(page => page.state === 'loaded').length;
    const failed = current.pages.filter(page => page.state === 'error').length;
    const lastPage = mode === 'paged' && position.page === current.pages.length - 1 ? ' · 本章最后一页' : '';
    loadStatus.textContent = current.restoring ? `正在打开第 ${position.page + 1} 页${current.pages[position.page]?.state === 'error' ? '，可在页面中重试' : '…'}` : `已加载 ${loaded} / ${current.pages.length} 页${failed ? `，${failed} 页可重试` : ''}${lastPage}`;
    if (current.download?.complete) loadStatus.textContent = '本地阅读 · ' + loadStatus.textContent;
    const navigation = pageNavigation(position.page, current.pages.length);
    prevPage.disabled = navigation.previous === null; nextPage.disabled = navigation.next === null;
    zoomButton.disabled = !current.pages.length;
    pageInput.disabled = jump.disabled = false;
  }
  function requestFrame(current) {
    if (!active(current) || current.frame) return;
    current.frame = requestAnimationFrame(() => {
      current.frame = 0;
      if (!active(current) || !current.pages.length) return;
      updateProgress(current); queueImages(current); scheduleSave(current);
    });
  }
  function stopImage(current, page) {
    if (page.state !== 'loading') return;
    page.ticket++; releaseImage(page, true);
    page.state = 'idle'; page.status.textContent = '等待阅读'; page.figure.dataset.state = 'idle';
    current.inflight = Math.max(0, current.inflight - 1);
  }
  function queueImages(current) {
    if (!active(current) || !current.pages.length) return;
    const first = current.restoring ? current.restoreTarget.page : mode === 'paged' ? current.position.page : pageAt(current, scroll.scrollTop);
    const last = current.restoring || mode === 'paged' ? first : pageAt(current, scroll.scrollTop + scroll.clientHeight);
    // Give the visible page a head start. Background requests start after it decodes.
    const visibleReady = current.pages[first]?.state === 'loaded';
    const ahead = prefetchPageCount({mode, prefetch, siteId: current.book.siteId, saveData: globalThis.navigator?.connection?.saveData});
    const candidates = imageCandidates({total: current.pages.length, page: first, first, last, mode, ahead});
    const desired = new Set(candidates);
    for (const page of current.pages) if (page.state === 'loading' && !desired.has(page.index)) stopImage(current, page);
    for (let index = first; index <= last; index++) if (current.pages[index]?.requestUrl) imageLoader.promote(current.pages[index].requestUrl);
    for (const index of candidates) {
      if (current.inflight >= MAX_IMAGE_REQUESTS) break;
      if (!visibleReady && (index < first || index > last)) continue;
      const page = current.pages[index];
      if (page.state === 'idle') loadImage(current, page, index >= first && index <= last ? 0 : 1);
    }
  }
  function releaseImage(page, removeSource = false) {
    page.image.onload = page.image.onerror = null;
    page.controller?.abort(); page.controller = null;
    if (removeSource) page.image.removeAttribute('src');
    if (page.objectUrl) {URL.revokeObjectURL(page.objectUrl); page.objectUrl = null;}
  }
  function loadImage(current, page, priority = 0) {
    if (!active(current) || page.state !== 'idle') return;
    const ticket = ++page.ticket;
    page.state = 'loading'; current.inflight++;
    const controller = new AbortController(); page.controller = controller;
    page.figure.dataset.state = 'loading'; page.status.textContent = '正在加载'; page.retry.hidden = true; page.placeholder.hidden = false;
    const valid = () => active(current) && page.ticket === ticket && page.state === 'loading';
    function settle(ok, message = '') {
      if (!valid()) return;
      const position = current.restoring ? current.restoreTarget : locate(current);
      current.inflight = Math.max(0, current.inflight - 1);
      page.state = ok ? 'loaded' : 'error'; page.figure.dataset.state = page.state;
      // Once decoded the <img> retains its pixels; the Blob URL and request
      // can be released without retaining the downloaded buffer for the chapter.
      releaseImage(page, !ok);
      if (ok) {
        page.ratio = normalizeRatio(page.image.naturalHeight / page.image.naturalWidth, page.ratio);
        page.figure.style.aspectRatio = `1 / ${page.ratio}`;
        if (mode === 'continuous') page.figure.style.width = `${widthForPage(page)}px`;
        page.placeholder.hidden = true;
        setPosition(current, position);
        if (current.restoring && page.index === current.restoreTarget.page) {
          current.restoring = false;
          setPosition(current, current.restoreTarget);
        }
        clearTimeout(current.dimensionsTimer);
        current.dimensionsTimer = setTimeout(() => {if (active(current)) saveDimensions(current);}, 700);
      } else {
        page.status.textContent = message || '图片内容无法解码，请重试或切换漫画源'; page.retry.hidden = false;
      }
      updateProgress(current); requestFrame(current);
    }
    page.image.onload = () => settle(true);
    page.image.onerror = () => settle(false);
    const url = imageUrl(page.url, current.book.siteId) + (page.attempt ? `&retry=${Date.now()}-${page.attempt}` : '');
    page.requestUrl = url;
    const load = async () => {
      const local = !page.attempt && current.download ? await downloads.getPage(current.download, page.index) : null;
      if (!valid()) throw new DOMException('图片请求已取消', 'AbortError');
      return local || imageLoader.load(url, {signal: controller.signal, priority});
    };
    load().then(blob => {
      if (!valid()) return;
      page.objectUrl = URL.createObjectURL(blob);
      page.image.src = page.objectUrl;
    }).catch(error => {
      // Cancellation invalidates the ticket before aborting. A late rejection
      // must never decrement a new request or replace its status/progress.
      if (!valid() || controller.signal.aborted || error?.name === 'AbortError') return;
      settle(false, error.message || '图片请求失败，请重试');
    });
  }
  function retryImage(current, page) {
    if (!active(current) || page.state !== 'error') return;
    page.attempt++; page.state = 'idle';
    // The retry target is already visible; the same bounded queue owns its request.
    queueImages(current);
  }
  function jumpToPage(page, pageOffset = 0) {
    const current = session;
    if (!active(current) || !current.pages.length || !Number.isFinite(page)) return;
    flush();
    const target = {page: clampPage(page, current.pages.length), pageOffset: clampOffset(pageOffset)};
    current.restoreTarget = target; current.restoring = current.pages[target.page].state !== 'loaded';
    setPosition(current, target); updateProgress(current); queueImages(current); scheduleSave(current);
    scroll.focus({preventScroll: true});
  }
  function movePage(delta) {
    const current = session;
    if (!active(current) || !current.pages.length) return;
    const navigation = pageNavigation(current.position.page, current.pages.length);
    const page = delta < 0 ? navigation.previous : navigation.next;
    if (page !== null) jumpToPage(page, current.pageOffsets.get(page) || 0);
  }
  function moveChapter(delta) {
    const current = session;
    if (!active(current)) return;
    const navigation = chapterNavigation(current.chapters, current.index);
    const index = delta < 0 ? navigation.previous : navigation.next;
    if (index === null) return;
    open({book: current.book, chapters: current.chapters, index, resume: false, push: true});
  }
  function updateChapterRead() {
    if (!session) return;
    const read = !!isChapterRead(session.book, session.chapter);
    readButton.textContent = read ? '已读 · 撤销' : '标为已读';
    readButton.setAttribute('aria-label', read ? '将本章标为未读' : '将本章标为已读');
    readButton.setAttribute('aria-pressed', String(read));
  }
  function toggleChapterRead() {
    const current = session;
    if (!active(current) || typeof onChapterRead !== 'function') return;
    try {
      // Submit the action the user saw even if another tab changed the shelf.
      const read = readButton.getAttribute('aria-pressed') !== 'true';
      onChapterRead({...current.book}, {...current.chapter}, read);
      updateChapterRead(); renderCatalog();
      toast(read ? '本章已标为已读' : '本章已标为未读');
    } catch {toast('章节状态未能保存，请重试');}
  }
  function setCatalog(value, restoreFocus = true) {
    catalogOpen = value && !!session;
    if (catalogOpen) settingsOpen = false;
    syncPanels();
    if (catalogOpen) {
      renderCatalog(); catalogSearch.focus({preventScroll: true});
      catalogList.querySelector('[aria-current="true"]')?.scrollIntoView({block: 'nearest'});
    } else if (restoreFocus && root.open) (focused ? exitFocus : catalogButton).focus({preventScroll: true});
  }
  function setSettings(value, restoreFocus = true) {
    settingsOpen = value && !!session;
    if (settingsOpen) catalogOpen = false;
    syncPanels();
    if (settingsOpen) modes.inputs.find(input => input.checked)?.focus({preventScroll: true});
    else if (restoreFocus && root.open) (focused ? exitFocus : settingsButton).focus({preventScroll: true});
  }
  function syncPanels() {
    catalog.hidden = !catalogOpen; settings.hidden = !settingsOpen; shade.hidden = !catalogOpen && !settingsOpen;
    catalogButton.setAttribute('aria-expanded', String(catalogOpen)); settingsButton.setAttribute('aria-expanded', String(settingsOpen));
    for (const element of [header, scroll, toolbar, exitFocus]) element.inert = catalogOpen || settingsOpen;
  }
  function renderCatalog(preserveView = false) {
    const oldTop = catalogList.scrollTop;
    const focusedIndex = preserveView && catalogList.contains(document.activeElement) ? document.activeElement.dataset.chapterIndex : null;
    catalogList.replaceChildren();
    const current = session;
    if (!current) return;
    const query = catalogSearch.value.trim().toLocaleLowerCase();
    let rows = current.chapters.map((chapter, index) => ({chapter, index})).filter(({chapter}) => String(chapter.name || '').toLocaleLowerCase().includes(query));
    if (catalogReverse) rows.reverse();
    const navigation = chapterNavigation(current.chapters, current.index);
    catalogCount.textContent = query ? `${rows.length} 个匹配章节 / 共 ${current.chapters.length} 章` : `共 ${current.chapters.length} 章${navigation.sequenceId ? ` · 当前序列 ${navigation.count} 章` : ''}`;
    for (const {chapter, index} of rows) {
      const button = action('', '', () => {
        if (!active(current)) return;
        setCatalog(false, false);
        if (index === current.index) scroll.focus({preventScroll: true});
        else open({book: current.book, chapters: current.chapters, index, push: true});
      });
      button.append(make('span', 'ry-catalog-chapter-name', chapter.name || `第 ${index + 1} 章`));
      const meta = [chapter.language, isChapterRead(current.book, chapter) ? '已读' : ''].filter(Boolean).join(' · ');
      if (meta) button.append(make('span', 'ry-catalog-chapter-meta', meta));
      button.removeAttribute('id'); button.dataset.chapterIndex = String(index); button.title = chapter.name || '';
      if (index === current.index) button.setAttribute('aria-current', 'true');
      catalogList.append(button);
    }
    if (!rows.length) catalogList.append(make('p', 'ry-reader-empty', '没有匹配章节，试试更短的名称。'));
    if (preserveView) {
      catalogList.scrollTop = oldTop;
      if (focusedIndex !== null) catalogList.querySelector(`[data-chapter-index="${focusedIndex}"]`)?.focus({preventScroll: true});
    }
  }
  function refreshReadingState() {updateChapterRead(); if (catalogOpen) renderCatalog(true);}
  function chapterError(current, error) {
    if (!active(current)) return;
    canvas.replaceChildren(); ending.hidden = true; loadStatus.textContent = '本章加载失败，可重试';
    const panel = make('div', 'ry-chapter-message ry-chapter-error'); panel.setAttribute('role', 'alert');
    panel.append(make('h2', '', '这一章暂时无法打开'), make('p', '', error.message || '漫画源暂时没有返回图片。'));
    const retry = action('reader-retry-chapter', '重试本章', () => {
      if (active(current)) open({book: current.book, chapters: current.chapters, index: current.index, resume: true, push: false});
    });
    retry.classList.add('ry-reader-primary');
    panel.append(retry, action('reader-error-back', '返回目录', requestExit)); canvas.append(panel);
  }
  async function open({book, chapters, index, resume = false, push = true}) {
    const chapter = chapters?.[index];
    if (!chapter?.url) return;
    flush(); const previous = session; const request = scope.start(); clean(previous);
    loadPreferences(book);
    const current = {scope: request, book: {...book}, chapters: [...chapters], chapter: {...chapter}, index, pages: [], inflight: 0, frame: 0, position: {page: 0, pageOffset: 0}, pageOffsets: new Map(), restoring: false};
    session = current; setCatalog(false, false); setSettings(false, false); catalogSearch.value = '';
    title.textContent = book.title || '漫画'; chapterLabel.textContent = [book.siteName || book.siteId || '', chapter.name || `第 ${index + 1} 章`, chapter.language].filter(Boolean).join(' · ');
    const navigation = chapterNavigation(chapters, index);
    prev.disabled = navigation.previous === null; next.disabled = navigation.next === null;
    nextEnd.hidden = navigation.next === null;
    endLabel.textContent = nextEnd.hidden ? navigation.sequenceId ? '已到当前语言或版本的最后一章，可从目录切换其他序列' : '已经读到当前源的最后一章' : '已到本章末尾，可继续下一章';
    updateChapterRead();
    loadStatus.textContent = '正在获取本章图片…'; progressLabel.textContent = '/ — 页'; pageInput.value = '1'; pageInput.disabled = jump.disabled = true;
    prevPage.disabled = nextPage.disabled = true;
    zoomButton.disabled = true;
    const loading = make('div', 'ry-chapter-message', '正在打开漫画…'); loading.setAttribute('role', 'status'); canvas.replaceChildren(loading); ending.hidden = true;
    applyPreferences();
    if (!root.open) root.showModal();
    scroll.scrollTop = scroll.scrollLeft = 0; scroll.focus({preventScroll: true});
    onNavigate({...current.book}, {...current.chapter}, {push});
    try {
      current.download = await downloads?.getChapter(book, chapter);
      if (!current.download?.urls.length) current.download = null;
      if (!active(current)) return;
      const data = current.download ? {images: current.download.urls} : await api('/api/chapter-images', {siteId: book.siteId, chapterUrl: chapter.url}, request.controller.signal);
      if (!active(current)) return;
      const urls = (Array.isArray(data?.images) ? data.images : []).filter(url => typeof url === 'string' && /^https?:\/\//i.test(url));
      if (!urls.length) throw new Error('这个漫画源没有返回可用图片，请重试或返回详情换源。');
      const saved = resume ? getProgress({...book}) : undefined;
      current.restoreTarget = restorePosition(saved, chapter.url, urls.length);
      current.position = {...current.restoreTarget}; current.restoring = true;
      current.pageOffsets.set(current.position.page, current.position.pageOffset);
      const cached = dimensions[dimensionsKey(current)]?.ratios;
      const fragment = document.createDocumentFragment();
      loadStatus.textContent = `正在准备 ${urls.length} 页漫画…`;
      const pages = await buildReaderPages(urls, {isCurrent: () => active(current), createPage: (url, pageIndex) => {
        const figure = make('figure', 'ry-reader-page'); figure.dataset.page = String(pageIndex); figure.dataset.state = 'idle';
        const ratio = normalizeRatio(Array.isArray(cached) ? cached[pageIndex] : undefined); figure.style.aspectRatio = `1 / ${ratio}`;
        const image = make('img'); image.alt = `${chapter.name || '漫画'} 第 ${pageIndex + 1} 页`; image.decoding = 'async'; image.referrerPolicy = 'no-referrer'; image.draggable = false;
        const placeholder = make('div', 'ry-page-placeholder');
        const number = make('span', 'ry-page-number', `第 ${pageIndex + 1} 页`); const status = make('span', 'ry-page-status', '等待阅读');
        const retry = action('', '重试此页', () => retryImage(current, page)); retry.removeAttribute('id'); retry.classList.add('ry-page-retry'); retry.setAttribute('aria-label', `重新加载第 ${pageIndex + 1} 页`); retry.hidden = true;
        placeholder.append(number, status, retry); figure.append(image, placeholder); fragment.append(figure);
        const page = {index: pageIndex, url, ratio, figure, image, placeholder, status, retry, state: 'idle', ticket: 0, attempt: 0, controller: null, objectUrl: null};
        return page;
      }});
      if (!pages || !active(current)) return;
      current.pages = pages;
      canvas.replaceChildren(fragment); ending.hidden = false;
      setPosition(current, current.restoreTarget); updateProgress(current); queueImages(current);
      let previousSize = `${scroll.clientWidth}:${scroll.clientHeight}`;
      current.resize = new ResizeObserver(() => {
        if (!active(current)) return;
        const size = `${scroll.clientWidth}:${scroll.clientHeight}`;
        if (size !== previousSize) {
          previousSize = size;
          setPosition(current, current.restoring ? current.restoreTarget : current.position);
        }
        requestFrame(current);
      });
      current.resize.observe(scroll);
      renderCatalog();
    } catch (error) {
      if (active(current) && error.name !== 'AbortError') chapterError(current, error);
    }
  }

  scroll.addEventListener('scroll', () => {if (session) requestFrame(session);}, {passive: true});
  function manualScroll() {
    const current = session;
    if (mode !== 'continuous') return;
    if (active(current) && current.restoring && current.pages.length) {
      current.restoring = false; current.position = locate(current); requestFrame(current);
    }
  }
  scroll.addEventListener('wheel', manualScroll, {passive: true});
  scroll.addEventListener('touchmove', manualScroll, {passive: true});
  scroll.addEventListener('pointerdown', event => {if (event.target === scroll) manualScroll();});
  scroll.addEventListener('dblclick', event => {
    if (!(event.target instanceof Element) || !event.target.closest('.ry-reader-page[data-state="loaded"]')) return;
    event.preventDefault(); setZoom(zoom === 1 ? 2 : 1, event);
  });
  scroll.addEventListener('pointerdown', event => {
    if (event.pointerType !== 'mouse' || event.button !== 0 || !(event.target instanceof Element) || !event.target.closest('.ry-reader-page[data-state="loaded"]')) return;
    if (scroll.scrollWidth <= scroll.clientWidth && scroll.scrollHeight <= scroll.clientHeight) return;
    drag = {id: event.pointerId, x: event.clientX, y: event.clientY, left: scroll.scrollLeft, top: scroll.scrollTop};
  });
  scroll.addEventListener('pointermove', event => {
    if (!drag || drag.id !== event.pointerId || event.pointerType !== 'mouse' || !(event.buttons & 1)) return;
    const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) < 4) return;
    scroll.setPointerCapture(event.pointerId); event.preventDefault();
    scroll.scrollLeft = drag.left - dx; scroll.scrollTop = drag.top - dy;
  });
  function finishDrag(event) {
    if (drag?.id !== event.pointerId) return;
    if (scroll.hasPointerCapture(event.pointerId)) scroll.releasePointerCapture(event.pointerId);
    drag = null;
  }
  scroll.addEventListener('pointerup', finishDrag); scroll.addEventListener('pointercancel', finishDrag);
  catalogSearch.oninput = () => renderCatalog();
  widthInput.oninput = () => {
    const position = session?.pages.length ? session.restoring ? session.restoreTarget : locate(session) : null;
    width = Number(widthInput.value); applyPreferences(); savePreferences();
    if (position) {setPosition(session, position); requestFrame(session);}
  };
  root.addEventListener('cancel', event => {
    event.preventDefault();
    if (settingsOpen) setSettings(false);
    else if (catalogOpen) setCatalog(false);
    else if (focused) setFocused(false);
    else requestExit();
  });
  root.addEventListener('keydown', event => {
    if (!root.open || event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
    if (catalogOpen || settingsOpen) {
      if (event.key === 'Escape') {
        // Search inputs can consume Escape to clear themselves before the
        // native dialog emits cancel. Own this panel action and suppress that
        // default so the same key cannot also close the reader underneath.
        event.preventDefault(); event.stopPropagation();
        if (settingsOpen) setSettings(false);
        else setCatalog(false);
      } else if (event.key === 'Tab') {
        const panel = settingsOpen ? settings : catalog;
        const controls = [...panel.querySelectorAll('button:not([disabled]), input:not([disabled])')].filter(control => control.getClientRects().length);
        const first = controls[0], last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) {event.preventDefault(); last?.focus();}
        else if (!event.shiftKey && document.activeElement === last) {event.preventDefault(); first?.focus();}
      }
      return;
    }
    if (editable(event.target)) return;
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault();
      if (mode === 'paged') movePage(pageTurnDelta(event.key, direction));
      else moveChapter(event.key === 'ArrowLeft' ? -1 : 1);
    }
    else if (event.key.toLowerCase() === 'f') {event.preventDefault(); setFocused(!focused);}
    else if (event.key.toLowerCase() === 'c') {event.preventDefault(); setCatalog(true);}
    else if (['+', '=', '-', '0'].includes(event.key)) {event.preventDefault(); setZoom(event.key === '0' ? 1 : zoom + (event.key === '-' ? -.25 : .25));}
    else if (event.key === 'PageDown' || event.key === 'PageUp' || (event.key === ' ' && event.target === scroll)) {
      if (event.target instanceof Element && event.target.closest('button')) return;
      event.preventDefault();
      if (mode === 'paged') movePage(event.key === 'PageUp' || (event.key === ' ' && event.shiftKey) ? -1 : 1);
      else {manualScroll(); scroll.scrollBy({top: scroll.clientHeight * (event.key === 'PageUp' ? -0.85 : 0.85), behavior: 'auto'});}
    } else if ((event.key === 'Home' || event.key === 'End') && event.target === scroll) {event.preventDefault(); jumpToPage(event.key === 'Home' ? 0 : (session?.pages.length || 1) - 1);}
  });
  document.addEventListener('visibilitychange', () => {if (document.visibilityState === 'hidden') flush();});
  window.addEventListener('pagehide', flush);
  applyPreferences();
  return {open, close, flush, refreshReadingState, isOpen: () => !!root.open};
}
