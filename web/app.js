import {createSearchView} from './search-view.js';
import {createReader} from './reader.js';
import {createImageLoader} from './image-loader.js';
import {createDownloads} from './downloads.js';
import {createSourceCatalog} from './source-catalog.js';
import {READING_STATES, bookKey, readingState, rememberBook, setReadingState, libraryCounts, filterLibrary, continueBook, mergeShelfBackup, serializeShelfBackup, readShelfBackup, MAX_BACKUP_BYTES, markChapterRead, isChapterRead} from './library-model.js';
import {createLibraryStore, SHELF_PREFIX} from './library-store.js';
import {dueUpdateBooks} from './library-auto-updates.js';
import {loadSourcePreferences, sortSources} from './source-preferences.js';
import {createDiscovery} from './discovery.js';
import {createRecommendations} from './recommendations.js';
import {createCoverWall} from './cover-wall.js';
import {createLibraryUpdates, acknowledgeCatalog, normalizeCatalogState} from './library-updates.js';
import {pageWindow} from './page-window.js';
import {createRouteHistory} from './route-history.js';
import {createCoverPause} from './cover-pause.js';
const $ = s => document.querySelector(s);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };
const button = (text, cls, fn) => { const b = el('button', cls, text); b.type = 'button'; b.onclick = fn; return b; };
const readStore = (key, fallback) => { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } };
const state = {page: 'home', discoverTab: 'recommend', sites: [], groups: [], keyword: '', filter: '', shelfFilter: 'all', shelfPage: 0, detailView: null, sourcePreferences: loadSourcePreferences(), search: null, retries: new Map(), detailSeq: 0, detailRequest: null, book: null, chapters: [], chapter: null};
const navigation = createRouteHistory({history: window.history, location});
let libraryStore;
try {libraryStore = createLibraryStore({storage: localStorage});}
catch {libraryStore = {books: [], issues: ['浏览器无法读取记录，请检查存储权限'], async save() {throw new Error('浏览器无法保存记录，请导出书架备份');}, flush() {}, async sync() {return shelf;}, recovery() {return '{}';}};}
let shelf = libraryStore.books, shelfSaveGeneration = 0;
let autoUpdates = readStore('revyunman.autoUpdates.v1', false) === true;
const storedAttempts = readStore('revyunman.autoUpdateAttempts.v1', {});
const autoAttempts = storedAttempts && typeof storedAttempts === 'object' && !Array.isArray(storedAttempts) ? Object.fromEntries(Object.entries(storedAttempts).filter(([, value]) => Number.isFinite(value) && value >= 0)) : {};
let history = readStore('revyunman.searches.v1', []);
const updateErrors = new Map();
let updateRun = null, updateStats = {baseline: 0, new: 0, changed: 0}, updateContexts = new Map();
let removedBook = null, removalTimer;
if (!Array.isArray(history)) history = [];
history = history.filter(x => typeof x === 'string').slice(0, 8);
const key = bookKey;
function persist(name, data) { try { localStorage.setItem(name, JSON.stringify(data)); } catch { toast('浏览器无法保存记录，请导出书架备份'); } }
function toast(message) { const t = $('#toast'); const modal = [...document.querySelectorAll('dialog[open]')].at(-1); (modal || document.body).append(t); t.textContent = message; t.hidden = false; clearTimeout(toast.timer); toast.timer = setTimeout(() => t.hidden = true, 4000); }
function shelfChanged() {$('#shelf-count').textContent = shelf.length; renderContinueReading(); renderShelfUpdateControls(); recommendations.shelfChanged?.();}
function saveShelf(options = {}) {
  const generation = ++shelfSaveGeneration; shelfChanged();
  const failed = error => {toast(`${error.message || '保存失败'}；当前记录仍可导出备份`); return false;};
  try {
    return libraryStore.save(shelf, options).then(saved => {if (generation === shelfSaveGeneration) {shelf = saved; shelfChanged();} return true;}).catch(failed);
  } catch (error) {return Promise.resolve(failed(error));}
}
function captureProgressContext(book) {
  try {return libraryStore.beginProgressSession?.(book);}
  catch {toast('无法读取书架记录，本次阅读仍可继续；请检查浏览器存储权限'); return null;}
}
function progressAllowed(book, context) {
  try {return !libraryStore.canSaveProgress || libraryStore.canSaveProgress(book, context);}
  catch {toast('无法保存阅读记录，请检查浏览器存储权限'); return false;}
}
const passiveSave = (book, context) => ({intent: 'passive', contexts: new Map([[key(book), context]])});
function remember(book, extra = {}, options = {}) {
  const old = shelf.find(b => key(b) === key(book));
  if (options.intent === 'passive' && !progressAllowed(book, options.contexts?.get(key(book)))) return old || book;
  const stamp = Object.hasOwn(extra, 'favorite') ? Math.max(Date.now(), (Number(old?.favoriteChangedAt) || 0) + 1) : Date.now();
  const saved = rememberBook(old, old ? book : {...book, favorite: false}, extra, stamp);
  shelf = [saved, ...shelf.filter(b => key(b) !== key(book))]; saveShelf(options); return saved;
}
function rememberProgress(book, progress, progressContext) {
  if (!progressAllowed(book, progressContext)) return;
  const id = key(book), old = shelf.find(item => key(item) === id);
  const saved = rememberBook(old, old ? book : {...book, favorite: false}, progress);
  shelf = [saved, ...shelf.filter(item => key(item) !== id)];
  const generation = ++shelfSaveGeneration;
  $('#shelf-count').textContent = shelf.length; renderContinueReading();
  recommendations.recordRead?.(book, progress);
  if (!libraryStore.updateBook) {saveShelf(passiveSave(book, progressContext)); return;}
  const failed = error => toast(`${error.message || '保存失败'}；当前记录仍可导出备份`);
  try {
    libraryStore.updateBook(saved, {progressContext}).then(async merged => {
      if (merged && generation === shelfSaveGeneration) {shelf = [merged, ...shelf.filter(item => key(item) !== id)]; renderContinueReading();}
      else if (!merged && generation === shelfSaveGeneration) {
        const synced = await libraryStore.sync();
        if (generation === shelfSaveGeneration) {shelf = synced; shelfChanged();}
      }
    }).catch(failed);
  } catch (error) {failed(error);}
}
async function api(path, body, signal) {
  const controller = new AbortController(); let timedOut = false;
  const abort = () => controller.abort();
  if (signal?.aborted) abort(); else signal?.addEventListener('abort', abort, {once: true});
  const timeout = setTimeout(() => {timedOut = true; controller.abort();}, 45000);
  try {
    const response = await fetch(path, {method: body ? 'POST' : 'GET', headers: body ? {'Content-Type': 'application/json'} : {}, body: body ? JSON.stringify(body) : undefined, signal: controller.signal});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `请求失败 (${response.status})`);
    return payload.data;
  } catch (error) {
    if (timedOut) throw new Error('连接超时，请重试或切换漫画源');
    throw error;
  } finally {clearTimeout(timeout); signal?.removeEventListener('abort', abort);}
}
function encode(value) { const bytes = new TextEncoder().encode(JSON.stringify(value)); return btoa(Array.from(bytes, b => String.fromCharCode(b)).join('')).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', ''); }
function decode(value) { return JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(value.replaceAll('-', '+').replaceAll('_', '/')), c => c.charCodeAt(0)))); }
function bookToken(book) { return encode([book.siteId, book.detailUrl, {title: book.title, coverUrl: book.coverUrl || ''}]); }
function bookRoute(book) { return '/m/' + bookToken(book); }
function route(path, replace = false) {
  navigation.navigate(path, {replace, background: resultRoute()});
}
function discoverPath(tab = state.discoverTab) {return tab === 'recommend' ? '/discover' : `/discover/${tab}`;}
function resultRoute() { return state.page === 'discover' ? discoverPath() : state.page === 'search' && state.keyword ? '/s/' + encodeURIComponent(state.keyword) : '/'; }
function setPage(page) {
  state.page = page; document.body.dataset.page = page;
  $('#landing').hidden = page !== 'home'; $('#results').hidden = page !== 'search'; $('#discover-page').hidden = page !== 'discover';
  $('#header-search').hidden = page !== 'search'; $('.header').classList.toggle('searching', page === 'search');
  for (const link of $('.primary-nav').querySelectorAll('a')) {
    if (link.dataset.page === (page === 'discover' ? 'discover' : 'home')) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
  }
  coverWall.setVisible(page === 'home');
  if (page !== 'discover') {discovery.hide(); recommendations.hide();}
}
function imageUrl(url, site) { return '/api/image?' + new URLSearchParams({url, siteId: site || ''}); }
const sourceCatalog = createSourceCatalog({root: $('#source-catalog-dialog'), api, onPreferencesChange: preferences => {state.sourcePreferences = preferences;}});
$('#source-catalog-open').onclick = () => sourceCatalog.open();
const coverWall = createCoverWall({root: $('#atlas'), imageUrl, control: $('#wall-motion'), onOpenBook: book => openBook(book)});
const discovery = createDiscovery({root: $('#discovery'), api, imageUrl, onOpenBook: book => openBook(book), embedded: true});
const recommendations = createRecommendations({root: $('#recommendations'), api, imageUrl, onOpenBook: book => openBook(book), getShelf: () => shelf});
const libraryUpdates = createLibraryUpdates({api,
  getBook: id => {const book = shelf.find(book => key(book) === id); return book && progressAllowed(book, updateContexts.get(id)) ? book : null;},
  onBookResult: result => {
    const current = shelf.find(book => key(book) === result.key); if (!current) return;
    const context = updateContexts.get(result.key); if (!progressAllowed(current, context)) return;
    if (result.error) updateErrors.set(result.key, result.error);
    else if (result.catalogState) {
      updateErrors.delete(result.key);
      shelf = shelf.map(book => key(book) === result.key ? {...book, catalogState: result.catalogState} : book);
      if (Object.hasOwn(updateStats, result.outcome)) updateStats[result.outcome]++;
      saveShelf(passiveSave(current, context));
    }
    renderShelfUpdateControls();
    if ($('#shelf-dialog').open && $('#shelf-only-updated').checked) renderShelf();
    else renderShelfUpdateNotes();
  },
  onStatus: status => {updateRun = status; renderShelfUpdateControls();}
});
const searchView = createSearchView({root: $('#result-grid'), api, imageUrl,
  onOpenBook: (book, detail) => openBook(book, {detail}),
  onReadChapter: (book, chapter, detail) => openBook(book, {chapterUrl: chapter.url, detail}),
  onMetrics: metrics => updateSearchStatus(metrics)
});
const imageLoader = createImageLoader();
const downloads = createDownloads({api, imageUrl, imageLoader, toast,
  onOpen: record => openBook(record.book, {chapterUrl: record.chapter.url, resume: true, detail: {...record.book, chapters: record.chapters, savedOffline: true}}),
});
const coverPause = createCoverPause({root: document, exclude: $('#reader-dialog')});
const reader = createReader({root: $('#reader-dialog'), api, imageUrl, imageLoader, downloads, toast,
  onOpen: coverPause.pause,
  onClose: coverPause.resume,
  getProgress: book => shelf.find(b => key(b) === key(book)),
  onProgress: rememberProgress,
  onRefreshCatalog: () => refreshBookCatalog(),
  onFailure: (book, detail) => recommendations.recordFailure?.(book, detail),
  onRecovery: (book, detail) => recommendations.recordRecovery?.(book, detail),
  isChapterRead: (book, chapter) => isChapterRead(shelf.find(item => key(item) === key(book)), chapter),
  onChapterRead: (book, chapter, read) => {
    const old = shelf.find(item => key(item) === key(book)) || remember(book);
    shelf = shelf.map(item => key(item) === key(book) ? markChapterRead(old, chapter, read) : item); saveShelf();
  },
  onNavigate: (book, chapter, {push}) => {
    state.chapter = chapter;
    document.title = `${chapter.name} · ${book.title} · Sardina`;
    if (push) route('/read/' + bookToken(book) + '/' + encode(chapter.url));
  },
  onExit: () => closeReader()
});
function cover(book) {
  const wrap = el('div', 'book-cover');
  const placeholder = el('span', 'cover-placeholder', book.title); wrap.append(placeholder);
  if (/^https?:\/\//.test(book.coverUrl || '')) {
    const img = el('img'); img.alt = book.title; img.loading = 'lazy'; img.referrerPolicy = 'no-referrer';
    img.onload = () => placeholder.hidden = true;
    img.onerror = () => img.remove();
    img.src = imageUrl(book.coverUrl, book.siteId); wrap.append(img);
  }
  return wrap;
}
function card(book, isShelf = false) {
  const article = el('article', 'book-card');
  if (isShelf) article.dataset.bookKey = key(book);
  const openCard = () => openBook(book, {resume: isShelf && !hasCatalogChange(shelf.find(item => key(item) === key(book)) || book)});
  const c = cover(book); const open = button('', 'book-cover', openCard); open.setAttribute('aria-label', `打开《${book.title}》`);
  open.replaceChildren(...c.childNodes); open.append(el('span', 'source-badge', book.siteName || book.siteId));
  article.append(open, button(book.title, 'book-title', openCard), el('p', 'book-meta', isShelf ? (book.chapterName ? `${book.chapterName} · 第${(book.page || 0) + 1}页` : '还未开始阅读') : book.latestChapter || book.author || '点击查看章节'));
  if (isShelf) {
    if (book.favorite === false) article.append(el('p', 'book-history-note', '阅读记录'));
    const del = button('×', 'remove-book', () => removeShelfBook(book));
    del.setAttribute('aria-label', `移出书架：${book.title}`);
    const status = el('select', 'book-reading-state'); status.setAttribute('aria-label', `《${book.title}》的阅读状态`); status.dataset.bookKey = key(book);
    for (const [value, text] of Object.entries(READING_STATES)) {const option = el('option', '', text); option.value = value; status.append(option);}
    status.value = readingState(book);
    status.onchange = () => {
      shelf = shelf.map(item => key(item) === key(book) ? setReadingState(item, status.value) : item); saveShelf();
      if (state.shelfFilter === 'all') {renderShelfCounts(); return;}
      renderShelf();
      const next = [...$('#shelf-grid').querySelectorAll('.book-reading-state')].find(item => item.dataset.bookKey === key(book));
      if (next) next.focus({preventScroll: true}); else $('#shelf-filters').querySelector('[aria-pressed="true"]').focus();
    };
    const updateNote = el('p', 'book-update-note'); updateNote.dataset.bookKey = key(book); fillUpdateNote(updateNote, book);
    article.append(updateNote, status, del);
  }
  return article;
}
function hasCatalogChange(book) { return ['new', 'changed'].includes(book.catalogState?.change); }
function fillUpdateNote(note, book) {
  const meta = book.catalogState, error = updateErrors.get(key(book));
  const status = meta?.change === 'new' ? '有新章节' : meta?.change === 'changed' ? '目录有变化' : meta ? '已记录目录' : '';
  note.textContent = [status, meta ? `${meta.chapterCount} 章` : '', error ? '检查失败' : ''].filter(Boolean).join(' · ');
  note.hidden = !note.textContent;
  note.dataset.change = meta?.change || ''; note.dataset.error = String(!!error);
  note.title = [meta?.checkedAt ? `上次成功检查：${new Date(meta.checkedAt).toLocaleString('zh-CN')}` : '', error || ''].filter(Boolean).join('\n');
}
function renderShelfUpdateNotes() {
  for (const note of $('#shelf-grid').querySelectorAll('.book-update-note')) {
    const book = shelf.find(item => key(item) === note.dataset.bookKey); if (book) fillUpdateNote(note, book);
  }
}
function renderShelfUpdateControls() {
  const check = $('#shelf-check-updates');
  check.textContent = updateRun?.running ? '停止检查' : '检查更新'; check.disabled = !shelf.length && !updateRun?.running;
  const retry = $('#shelf-retry-updates'); retry.hidden = !shelf.some(book => updateErrors.has(key(book))); retry.disabled = !!updateRun?.running;
  $('#shelf-updated-count').textContent = shelf.filter(hasCatalogChange).length;
  if (!updateRun) return;
  const head = updateRun.running ? '正在检查' : updateRun.cancelled ? '已停止' : '检查完成';
  $('#shelf-update-status').textContent = [`${head} ${updateRun.completed} / ${updateRun.total} 本`,
    updateStats.new ? `${updateStats.new} 本有新章节` : '', updateStats.changed ? `${updateStats.changed} 本目录有变化` : '',
    updateStats.baseline ? `${updateStats.baseline} 本首次记录` : '', updateRun.failed ? `${updateRun.failed} 本失败` : '',
    updateRun.skipped ? `${updateRun.skipped} 本已跳过` : ''].filter(Boolean).join(' · ');
}
function checkShelfUpdates(books = shelf) {
  if (libraryUpdates.isRunning()) {libraryUpdates.stop(); return;}
  updateStats = {baseline: 0, new: 0, changed: 0};
  updateContexts = new Map(books.map(book => [key(book), captureProgressContext(book)]));
  const priority = {reading: 0, later: 1, finished: 2};
  libraryUpdates.start([...books].sort((a, b) => priority[readingState(a)] - priority[readingState(b)]));
}
function maybeAutoUpdate() {
  if (!autoUpdates || document.hidden || libraryUpdates.isRunning() || !state.sites.length) return;
  const due = dueUpdateBooks(shelf, {attempts: autoAttempts, availableSites: state.sites.map(site => site.siteId)});
  if (!due.length) return;
  for (const book of due) autoAttempts[`${book.siteId}::${book.detailUrl}`] = Date.now();
  const valid = new Set(shelf.map(book => `${book.siteId}::${book.detailUrl}`));
  for (const key of Object.keys(autoAttempts)) if (!valid.has(key)) delete autoAttempts[key];
  persist('revyunman.autoUpdateAttempts.v1', autoAttempts); checkShelfUpdates(due);
}
function renderContinueReading() {
  const root = $('#continue-reading'), book = continueBook(shelf); root.replaceChildren(); root.hidden = !book;
  if (!book) return;
  const resume = button('', 'continue-book', () => openBook(book, {resume: true}));
  const copy = el('span', 'continue-copy');
  copy.append(el('span', 'continue-label', '继续阅读'), el('strong', '', book.title), el('span', 'continue-position', `${book.chapterName || '上次章节'} · 第${(Number(book.page) || 0) + 1}页`));
  resume.setAttribute('aria-label', `继续阅读《${book.title}》`); resume.append(copy, el('span', 'continue-action', '接着看'));
  root.append(resume);
}
function renderHistory() {
  const root = $('#history'); root.replaceChildren(); root.hidden = !history.length;
  if (history.length) { root.append(el('span', '', '最近：')); history.slice(0, 5).forEach(q => root.append(button(q, '', () => search(q)))); root.append(button('清空', '', () => { history = []; persist('revyunman.searches.v1', history); renderHistory(); })); }
}
function showError(root, message, retry) { const box = el('div', 'error-panel'); box.append(el('p', '', message)); if (retry) box.append(button('重试', 'quiet', retry)); root.replaceChildren(box); }
function updateSearchStatus(metrics) {
  const groups = state.groups, complete = groups.filter(g => !g.loading).length, count = groups.reduce((n, g) => n + g.results.length, 0);
  $('#search-status').textContent = `${metrics.workCount} 部匹配作品 · ${count} 条候选 · ${complete} / ${groups.length} 个源已响应` + (state.filter ? ' · 正在筛选单个源' : '') + (groups.some(g => g.error) ? ` · ${groups.filter(g => g.error).length} 个源失败，可展开筛选重试` : '');
}
function renderResults() {
  const groups = state.groups, count = groups.reduce((n, g) => n + g.results.length, 0);
  const metrics = searchView.update({groups, keyword: state.keyword, filter: state.filter});
  $('#results-title').textContent = `“${state.keyword}” 的搜索结果`;
  updateSearchStatus(metrics);
  $('#stop-search').hidden = !state.search && !state.retries.size;
  $('#source-filter-label').textContent = state.filter ? `当前：${groups.find(g => g.siteId === state.filter)?.siteName || state.filter} · 更换漫画源` : `全部漫画源 · 筛选（${groups.length}）`;
  const tabs = $('#source-tabs');
  const tab = (label, id, error) => { let b = [...tabs.children].find(b => b.dataset.site === id); if (!b) {b = button('', '', () => {state.filter = id; renderResults();}); b.dataset.site = id; tabs.append(b);} b.textContent = label; b.className = `${state.filter === id ? 'active' : ''} ${error ? 'failed' : ''}`; b.setAttribute('aria-pressed', String(state.filter === id)); };
  tab(`全部 ${count} 条`, ''); groups.forEach(g => tab(`${g.siteName} · ${g.loading ? '…' : g.error ? '!' : `${g.results.length} 条`}`, g.siteId, g.error));
  tabs.querySelectorAll('[data-site]').forEach(b => {if (b.dataset.site && !groups.some(g => g.siteId === b.dataset.site)) b.remove();});
  let sourceError = $('#source-error');
  const failed = state.filter && groups.find(g => g.siteId === state.filter && g.error);
  if (failed) {
    if (!sourceError) {sourceError = el('div', 'source-error'); sourceError.id = 'source-error'; tabs.after(sourceError);}
    sourceError.replaceChildren(el('span', '', `${failed.siteName}：${failed.error}`), button('重试这个源', 'quiet', () => retrySource(failed.siteId)));
  } else sourceError?.remove();
  let sourceNote = $('#source-note');
  const notice = state.filter && state.sites.find(s => s.siteId === state.filter)?.notice;
  if (notice && !failed) {
    if (!sourceNote) {sourceNote = el('p', 'muted'); sourceNote.id = 'source-note'; tabs.after(sourceNote);}
    sourceNote.textContent = notice;
  } else sourceNote?.remove();
  if (!groups.length) {
    showError($('#result-grid'), '漫画源列表加载失败，请刷新页面重试', () => location.reload());
  }
}
function stopSearch() {
  state.search?.abort(); state.search = null; cancelRetries();
  state.groups.forEach(g => { if (g.loading) {g.loading = false; g.error = '已停止搜索';} }); renderResults();
}
function cancelRetries() {for (const controller of state.retries.values()) controller.abort(); state.retries.clear();}
async function search(keyword, push = true) {
  keyword = keyword.trim(); if (!keyword) {toast('请输入漫画名称'); return;}
  closeReader(false); closeDetail(false); $('#shelf-dialog').close();
  state.search?.abort(); cancelRetries(); searchView.reset(); const controller = new AbortController(); state.search = controller;
  state.keyword = keyword; state.filter = ''; $('#home-keyword').value = $('#header-keyword').value = keyword;
  document.title = `${keyword} · 搜索 · Sardina`;
  history = [keyword, ...history.filter(q => q !== keyword)].slice(0, 8); persist('revyunman.searches.v1', history); renderHistory();
  if (push) route('/s/' + encodeURIComponent(keyword)); setPage('search');
  state.groups = sortSources(state.sites, state.sourcePreferences).map(s => ({...s, results: [], loading: true})); renderResults();
  // Reorder once per search; incremental source responses must not disturb focus.
  const tabs = $('#source-tabs');
  for (const id of ['', ...state.groups.map(group => group.siteId)]) {
    const tab = [...tabs.children].find(item => item.dataset.site === id); if (tab) tabs.append(tab);
  }
  let cursor = 0;
  async function worker() {
    while (cursor < state.groups.length && !controller.signal.aborted) {
      const group = state.groups[cursor++];
      try { const data = await api('/api/search', {keyword, siteId: group.siteId}, controller.signal); if (controller.signal.aborted) return; Object.assign(group, data[0], {loading: false}); }
      catch (e) { if (controller.signal.aborted) return; group.loading = false; group.error = e.message; }
      renderResults();
    }
  }
  await Promise.all(Array.from({length: Math.min(4, state.groups.length)}, worker));
  if (state.search === controller) { state.search = null; renderResults(); }
}
async function retrySource(id) {
  const keyword = state.keyword, groups = state.groups, g = groups.find(g => g.siteId === id); if (!g || g.loading) return;
  const controller = new AbortController(); state.retries.set(id, controller);
  g.loading = true; delete g.error; renderResults();
  try { const data = await api('/api/search', {keyword, siteId: id}, controller.signal); if (!controller.signal.aborted) Object.assign(g, data[0], {loading: false}); }
  catch (e) {if (!controller.signal.aborted) {g.loading = false; g.error = e.message;}}
  finally {if (state.retries.get(id) === controller) state.retries.delete(id); if (state.groups === groups) renderResults();}
}
function pageTitle() {return state.page === 'discover' ? '发现 · Sardina' : state.page === 'search' && state.keyword ? `${state.keyword} · 搜索 · Sardina` : 'Sardina';}
function closeDetail(push = true) {
  state.catalogRequest?.abort(); state.detailSeq++; state.detailRequest?.abort(); state.detailRequest = null;
  const wasOpen = $('#detail-dialog').open; $('#detail-dialog').close();
  if (push && wasOpen) {navigation.close('page', resultRoute()); document.title = pageTitle();}
}
async function openBook(book, {resume = false, push = true, chapterUrl = null, detail: prefetchedDetail = null} = {}) {
  state.catalogRequest?.abort(); closeReader(false); $('#shelf-dialog').close(); state.detailSeq++; const seq = state.detailSeq;
  state.detailRequest?.abort(); const controller = new AbortController(); state.detailRequest = controller;
  const old = shelf.find(b => key(b) === key(book)); state.book = {...old, ...book}; state.chapters = [];
  const catalogContext = captureProgressContext(book), saveOptions = passiveSave(book, catalogContext);
  if (state.detailView?.bookKey !== key(book)) state.detailView = {bookKey: key(book), query: '', reverse: false, page: 0, scrollTop: 0};
  const catalogStartedAt = Date.now(), catalogBase = old?.catalogState;
  const refreshCatalog = hasCatalogChange(old || book) || updateErrors.has(key(book));
  if (old) state.book = remember(state.book, {}, saveOptions);
  $('#detail-dialog').onscroll = null;
  const root = $('#detail-content'); root.replaceChildren();
  const header = el('div', 'dialog-header'); const heading = el('h2', '', '漫画详情'); heading.id = 'detail-title';
  const close = button('×', 'icon-button', () => closeDetail()); close.setAttribute('aria-label', '关闭漫画详情'); header.append(heading, close); root.append(header, el('div', 'loading', '正在加载漫画目录…'));
  if (!$('#detail-dialog').open) $('#detail-dialog').showModal();
  $('#detail-dialog').scrollTop = state.detailView.scrollTop;
  if (push) route(bookRoute(book));
  document.title = `${book.title} · Sardina`;
  try {
    const localDetail = prefetchedDetail?.savedOffline ? prefetchedDetail : await downloads.localDetail(book, chapterUrl || (resume && old?.chapterUrl));
    if (seq !== state.detailSeq) return;
    const detail = localDetail || !refreshCatalog && prefetchedDetail || await api('/api/details', {siteId: book.siteId, detailUrl: book.detailUrl, ...(refreshCatalog ? {refresh: true} : {})}, controller.signal);
    if (seq !== state.detailSeq) return;
    for (const field of ['title', 'coverUrl', 'description', 'author', 'status', 'tags', 'genres', 'language', 'edition']) if (detail[field]) state.book[field] = detail[field];
    state.book.unavailableReason = detail.unavailableReason || '';
    state.chapters = detail.chapters || [];
    if (!state.chapters.length) recommendations.recordFailure?.(state.book);
    if (!detail.savedOffline) recommendations.rememberMetadata?.(state.book, detail);
    const saved = shelf.find(item => key(item) === key(state.book));
    const catalogState = detail.savedOffline ? null : acknowledgeCatalog(saved?.catalogState, detail, {fresh: refreshCatalog, startedAt: catalogStartedAt, baseState: catalogBase});
    if (catalogState) state.book.catalogState = catalogState;
    if (saved && progressAllowed(state.book, catalogContext)) {
      remember(state.book, {}, saveOptions);
      // Acknowledgement may keep checkedAt unchanged; apply it to the current
      // record directly so a progress merge cannot reintroduce a viewed badge.
      if (catalogState) shelf = shelf.map(item => key(item) === key(state.book) ? {...item, catalogState} : item);
      if (!detail.savedOffline && state.chapters.length && (!detail.unavailableReason || detail.catalogCompleteness === 'complete')) updateErrors.delete(key(state.book));
      saveShelf(saveOptions);
    }
    renderDetail();
    const target = chapterUrl || (resume && shelf.find(item => key(item) === key(state.book))?.chapterUrl);
    if (target) { const index = state.chapters.findIndex(c => c.url === target); if (index >= 0) await readChapter(index, {resume: resume || !push, push}); else toast('原章节已不在当前目录中，请重新选择'); }
    if (localDetail && seq === state.detailSeq && navigator.onLine !== false) void refreshBookCatalog({quiet: true}).catch(() => {});
  } catch (e) {
    if (seq !== state.detailSeq) return;
    recommendations.recordFailure?.(book);
    const error = el('div'); showError(error, `目录加载失败：${e.message}`, () => openBook(book, {resume, push: false, chapterUrl}));
    error.firstChild.append(button('搜索其他源', 'quiet', () => search(book.title))); root.replaceChildren(header, error);
  } finally {if (state.detailRequest === controller) state.detailRequest = null;}
}
async function refreshBookCatalog({quiet = false} = {}) {
  if (!state.book) return;
  state.catalogRequest?.abort();
  const controller = new AbortController(), book = {...state.book}, seq = state.detailSeq, startedAt = Date.now();
  const catalogContext = captureProgressContext(book), saveOptions = passiveSave(book, catalogContext);
  state.catalogRequest = controller;
  try {
    const detail = await api('/api/details', {siteId: book.siteId, detailUrl: book.detailUrl, refresh: true}, controller.signal);
    if (controller.signal.aborted || seq !== state.detailSeq || key(book) !== key(state.book)) return;
    if (!Array.isArray(detail?.chapters) || !detail.chapters.length) throw new Error(detail?.unavailableReason || '暂未获取到新目录，已保留本地目录');
    const previousCount = state.chapters.length;
    for (const field of ['title', 'coverUrl', 'description', 'author', 'status', 'tags', 'genres', 'language', 'edition']) if (detail[field]) state.book[field] = detail[field];
    state.chapters = detail.chapters; state.book.unavailableReason = detail.unavailableReason || '';
    recommendations.rememberMetadata?.(state.book, detail);
    reader.updateCatalog(state.book, state.chapters);
    await downloads.store.putCatalog(state.book, state.chapters);
    if (seq !== state.detailSeq) return;
    const saved = shelf.find(item => key(item) === key(book));
    if (saved && progressAllowed(book, catalogContext)) {state.book.catalogState = acknowledgeCatalog(saved.catalogState, detail, {fresh: true, startedAt, baseState: saved.catalogState}); remember(state.book, {}, saveOptions);}
    if (!reader.isOpen() && $('#detail-dialog').open) renderDetail();
    if (!quiet || state.chapters.length > previousCount) toast(state.chapters.length > previousCount ? `目录已更新，共 ${state.chapters.length} 章` : '目录已更新');
  } finally {if (state.catalogRequest === controller) state.catalogRequest = null;}
}
function renderPageControls(root, window, onPage) {
  root.hidden = window.pages <= 1;
  if (!root.children.length) {
    const previous = button('上一页', 'quiet', () => {}), label = el('span'), next = button('下一页', 'quiet', () => {});
    previous.dataset.pageAction = 'previous'; next.dataset.pageAction = 'next'; label.setAttribute('aria-live', 'polite');
    root.append(previous, label, next);
  }
  const [previous, label, next] = root.children;
  previous.disabled = !window.hasPrevious; next.disabled = !window.hasNext;
  label.textContent = `第 ${window.page + 1} / ${window.pages} 页`;
  previous.onclick = () => onPage(window.page - 1); next.onclick = () => onPage(window.page + 1);
}
function renderDetail() {
  const root = $('#detail-content'), dialog = $('#detail-dialog'), b = state.book;
  const view = state.detailView || (state.detailView = {bookKey: key(b), query: '', reverse: false, page: 0, scrollTop: 0});
  const active = document.activeElement, chapterFocus = active?.closest('.chapter-entry')?.dataset.chapterUrl;
  const markFocused = active?.classList.contains('chapter-read-toggle'), queryFocused = active?.classList.contains('detail-chapter-query');
  const oldScroll = dialog.scrollTop || view.scrollTop;
  const header = root.firstChild; root.replaceChildren(header);
  const body = el('div', 'detail-body'), info = el('div', 'detail-info'), text = el('div');
  text.append(el('h2', '', b.title), el('p', 'muted', `${b.siteName || b.siteId}${b.author ? ' · ' + b.author : ''} · ${state.chapters.length} 章`));
  if (b.description) text.append(el('p', 'description', b.description));
  if (b.unavailableReason) text.append(el('p', 'source-unavailable', b.unavailableReason));
  const actions = el('div', 'detail-actions'); const saved = shelf.find(x => key(x) === key(b)); const resumeIndex = state.chapters.findIndex(c => c.url === saved?.chapterUrl);
  const start = button(resumeIndex >= 0 ? '继续阅读' : '开始阅读', 'primary', () => readChapter(Math.max(0, resumeIndex), {resume: resumeIndex >= 0})); start.disabled = !state.chapters.length;
  const collect = button('', 'quiet', () => {
    const current = shelf.find(item => key(item) === key(b)), favorite = !(current && current.favorite !== false);
    remember(b, {favorite}); updateCollect(); toast(favorite ? '已加入收藏' : '已取消收藏，阅读进度已保留');
  });
  function updateCollect() {
    const current = shelf.find(item => key(item) === key(b)), collected = !!current && current.favorite !== false;
    collect.textContent = collected ? '取消收藏' : '加入收藏'; collect.setAttribute('aria-pressed', String(collected));
  }
  updateCollect();
  const download = button('下载章节', 'quiet', () => downloads.open({book: b, chapters: state.chapters, chapter: state.chapters[Math.max(0, resumeIndex)]})); download.disabled = !state.chapters.length;
  const update = button('更新目录', 'quiet', async () => {update.disabled = true; try {await refreshBookCatalog();} catch (error) {toast(error.message);} finally {update.disabled = false;}});
  actions.append(start, collect, download, update, button('换源查找', 'quiet', () => search(b.title))); text.append(actions); info.append(cover(b), text); body.append(info);
  const tools = el('div', 'chapter-tools'), chapters = el('div', 'chapters'), filter = el('input', 'detail-chapter-query');
  filter.placeholder = '查找章节'; filter.setAttribute('aria-label', '查找章节'); filter.value = view.query;
  const summary = el('p', 'chapter-list-summary'); summary.setAttribute('role', 'status'); summary.setAttribute('aria-live', 'polite');
  const pages = el('nav', 'list-pagination'); pages.setAttribute('aria-label', '章节目录分页');
  const sort = button(view.reverse ? '正序排列' : '倒序排列', 'quiet', () => {view.reverse = !view.reverse; view.page = 0; sort.textContent = view.reverse ? '正序排列' : '倒序排列'; renderChapters();});
  tools.append(el('strong', '', '章节目录'), filter, sort); body.append(tools, summary, chapters, pages);
  function renderChapters() {
    const scrollTop = dialog.scrollTop;
    const needle = filter.value.normalize('NFKC').trim().toLocaleLowerCase();
    let rows = state.chapters.map((c, i) => ({c, i})).filter(({c}) => String(c.name || '').normalize('NFKC').toLocaleLowerCase().includes(needle));
    if (view.reverse) rows.reverse();
    const window = pageWindow(rows, {page: view.page, size: 80}); view.page = window.page;
    const current = shelf.find(item => key(item) === key(b));
    chapters.replaceChildren();
    for (const {c, i} of window.items) {
      const item = el('div', 'chapter-entry'); item.dataset.chapterUrl = c.url;
      const btn = button(c.name, c.url === current?.chapterUrl ? 'current' : '', () => readChapter(i)); btn.title = c.name;
      const mark = button('', 'chapter-read-toggle', () => {
        const old = shelf.find(book => key(book) === key(b)) || remember(b), nextRead = !isChapterRead(old, c);
        shelf = shelf.map(book => key(book) === key(b) ? markChapterRead(old, c, nextRead) : book); saveShelf(); updateMark(nextRead);
      });
      function updateMark(read) {mark.textContent = read ? '已读' : '标为已读'; mark.setAttribute('aria-label', `${c.name}：${read ? '标为未读' : '标为已读'}`); mark.setAttribute('aria-pressed', String(read));}
      updateMark(isChapterRead(current, c)); item.append(btn, mark); chapters.append(item);
    }
    if (!rows.length) chapters.append(el('p', 'muted', state.chapters.length ? '没有匹配的章节' : '当前源没有可读章节，请换源查找。'));
    summary.textContent = rows.length ? `显示 ${window.start + 1}–${window.end} / ${rows.length} 章` : '0 章';
    renderPageControls(pages, window, page => {
      view.page = page; renderChapters();
      chapters.querySelector('button')?.focus({preventScroll: true}); chapters.scrollIntoView({block: 'start'});
    });
    dialog.scrollTop = scrollTop;
  }
  filter.oninput = () => {view.query = filter.value; view.page = 0; renderChapters();}; renderChapters(); root.append(body);
  dialog.scrollTop = oldScroll;
  if (queryFocused) filter.focus({preventScroll: true});
  if (chapterFocus) {
    const row = [...chapters.children].find(item => item.dataset.chapterUrl === chapterFocus);
    (row?.querySelector(markFocused ? '.chapter-read-toggle' : 'button') || filter).focus({preventScroll: true});
  }
  dialog.onscroll = () => {view.scrollTop = dialog.scrollTop;};
}
function renderShelfCounts() {
  const counts = libraryCounts(shelf), filters = $('#shelf-filters');
  if (!filters.children.length) for (const [value, text] of Object.entries({all: '全部', ...READING_STATES})) {
    const filter = button('', '', () => {state.shelfFilter = value; state.shelfPage = 0; renderShelf();}); filter.dataset.state = value; filter.dataset.label = text; filters.append(filter);
  }
  for (const filter of filters.children) {filter.replaceChildren(document.createTextNode(filter.dataset.label), el('span', '', counts[filter.dataset.state])); filter.setAttribute('aria-pressed', String(state.shelfFilter === filter.dataset.state));}
}
function clearShelfFilters() {
  $('#shelf-query').value = ''; $('#shelf-only-updated').checked = false; $('#shelf-only-favorites').checked = false;
  state.shelfFilter = 'all'; state.shelfPage = 0; renderShelf(); $('#shelf-query').focus();
}
function findManga() {$('#shelf-dialog').close(); goHome(); $('#home-keyword').focus();}
function renderRemoval() {
  const root = $('#shelf-undo'); root.replaceChildren(); root.hidden = !removedBook;
  if (!removedBook) return;
  const entry = removedBook;
  const undo = button('撤销', 'quiet', async () => {
    if (removedBook !== entry) return;
    clearTimeout(removalTimer); removedBook = null; renderRemoval();
    shelf = mergeShelfBackup(shelf, [entry.book]);
    const saved = await saveShelf(); renderShelf();
    const restored = [...$('#shelf-grid').children].find(item => item.dataset.bookKey === key(entry.book));
    (restored?.querySelector('.book-title') || $('#shelf-query')).focus({preventScroll: true});
    if (saved) toast(`已恢复《${entry.book.title}》及阅读进度`);
  });
  root.append(el('span', '', `已移出《${entry.book.title}》`), undo);
  const expire = () => {removalTimer = setTimeout(() => {if (removedBook === entry) {removedBook = null; renderRemoval();}}, 10000);};
  root.onfocusin = root.onpointerenter = () => clearTimeout(removalTimer);
  root.onfocusout = root.onpointerleave = () => {clearTimeout(removalTimer); expire();};
  clearTimeout(removalTimer); expire();
}
function removeShelfBook(book) {
  const current = shelf.find(item => key(item) === key(book)); if (!current) return;
  removedBook = {book: current};
  shelf = shelf.filter(item => key(item) !== key(book)); updateErrors.delete(key(book));
  saveShelf(); renderRemoval(); renderShelf(); $('#shelf-undo button').focus({preventScroll: true});
}
function renderShelf() {
  const grid = $('#shelf-grid'), focused = document.activeElement, dialog = $('#shelf-dialog'), scrollTop = dialog.scrollTop;
  const focusKey = focused?.closest('.book-card')?.dataset.bookKey;
  const focusClass = ['book-reading-state', 'book-cover', 'book-title', 'remove-book'].find(name => focused?.classList.contains(name));
  const query = $('#shelf-query').value, onlyUpdated = $('#shelf-only-updated').checked, onlyFavorites = $('#shelf-only-favorites').checked;
  const books = filterLibrary(shelf, {query, state: state.shelfFilter, sort: $('#shelf-sort').value}).filter(book => (!onlyUpdated || hasCatalogChange(book)) && (!onlyFavorites || book.favorite !== false));
  const window = pageWindow(books, {page: state.shelfPage, size: 48}); state.shelfPage = window.page;
  renderShelfCounts(); grid.replaceChildren();
  $('#shelf-list-summary').textContent = books.length > 48 ? `显示 ${window.start + 1}–${window.end} / ${books.length} 本 · 书架共 ${shelf.length} 本` : `显示 ${books.length} / ${shelf.length} 本`;
  window.items.forEach(b => grid.append(card(b, true)));
  if (!books.length) {
    const empty = el('div', 'empty');
    if (!shelf.length) empty.append(el('strong', '', '书架还是空的'), el('p', '', '收藏漫画，或开始阅读后在这里继续。'), button('去找漫画', 'primary', findManga));
    else empty.append(el('strong', '', onlyUpdated ? '当前没有匹配的待查看更新' : onlyFavorites ? '当前筛选下没有收藏的漫画' : query ? '书架中没有匹配的漫画' : `还没有标为“${READING_STATES[state.shelfFilter] || '全部'}”的漫画`), el('p', '', '试试其他名称，或清除筛选查看全部记录。'), button('查看全部', 'quiet', clearShelfFilters));
    grid.append(empty);
  }
  renderPageControls($('#shelf-pagination'), window, page => {
    state.shelfPage = page; renderShelf(); grid.querySelector('.book-title')?.focus({preventScroll: true}); grid.scrollIntoView({block: 'start'});
  });
  renderShelfUpdateControls(); dialog.scrollTop = scrollTop;
  if (focusKey && focusClass) {
    const kept = [...grid.children].find(item => item.dataset.bookKey === focusKey);
    if (kept) kept.querySelector(`.${focusClass}`)?.focus({preventScroll: true});
    else if (!removedBook) $('#shelf-query').focus();
  }
}
function saveProgress() { reader.flush(); }
async function readChapter(index, {resume = false, push = true} = {}) {
  if (!state.chapters[index] || !state.book) return;
  $('#detail-dialog').close();
  const progressContext = captureProgressContext(state.book);
  await reader.open({book: {...state.book}, chapters: [...state.chapters], index, resume, push, progressContext});
}
function closeReader(push = true) {
  const wasOpen = reader.isOpen();
  reader.close(); state.chapter = null;
  if (push && wasOpen && state.book) {
    if (navigation.close('detail', bookRoute(state.book))) return;
    document.title = `${state.book.title} · Sardina`;
    renderDetail(); if (!$('#detail-dialog').open) $('#detail-dialog').showModal();
  }
}
async function restoreRoute() {
  const parts = location.pathname.split('/').filter(Boolean);
  closeReader(false); closeDetail(false); $('#shelf-dialog').close();
  try {
    if (parts[0] === 's') return await search(decodeURIComponent(parts.slice(1).join('/')), false);
    if (parts[0] === 'discover') return showDiscover(['popular', 'latest'].includes(parts[1]) ? parts[1] : 'recommend', {push: false, scroll: false});
    if ((parts[0] === 'm' || parts[0] === 'read') && parts[1]) {
      const [siteId, detailUrl, meta] = decode(parts[1]); if (!state.sites.some(s => s.siteId === siteId)) throw new Error('未接入该漫画源');
      const found = shelf.find(b => key(b) === key({siteId, detailUrl}));
      const book = found ? {...found, detailUrl} : {siteId, detailUrl, title: typeof meta?.title === 'string' ? meta.title : '漫画', coverUrl: typeof meta?.coverUrl === 'string' ? meta.coverUrl : '', siteName: state.sites.find(s => s.siteId === siteId).siteName};
      const background = window.history.state?.background;
      if (typeof background === 'string' && /^\/discover(?:\/(popular|latest))?$/.test(background)) {
        showDiscover(background.split('/')[2] || 'recommend', {push: false, scroll: false});
      } else if (typeof background === 'string' && background.startsWith('/s/')) {
        const query = decodeURIComponent(background.slice(3));
        if (query !== state.keyword || !state.groups.length) void search(query, false); else setPage('search');
      } else setPage('home');
      return await openBook(book, {push: false, chapterUrl: parts[0] === 'read' ? decode(parts[2]) : null});
    }
    state.search?.abort(); state.search = null; cancelRetries(); searchView.reset(); state.keyword = ''; document.title = 'Sardina'; setPage('home');
  } catch (e) {toast(`无法打开链接：${e.message}`); route('/', true); setPage('home'); document.title = 'Sardina';}
}
function goHome(event) {event?.preventDefault(); state.search?.abort(); state.search = null; cancelRetries(); closeReader(false); closeDetail(false); state.keyword = ''; searchView.reset(); document.title = 'Sardina'; route('/'); setPage('home'); window.scrollTo({top: 0, behavior: 'instant'});}
function showDiscover(tab = 'recommend', {push = true, scroll = true} = {}) {
  state.search?.abort(); state.search = null; cancelRetries(); closeReader(false); closeDetail(false);
  state.discoverTab = tab; state.keyword = ''; setPage('discover'); document.title = '发现 · Sardina';
  if (push) route(discoverPath());
  for (const link of $('.discover-tabs').querySelectorAll('a')) {
    if (link.dataset.discover === tab) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
  }
  if (tab === 'recommend') {discovery.hide(); recommendations.show();}
  else {recommendations.hide(); discovery.show({kind: tab});}
  if (scroll) window.scrollTo({top: 0, behavior: 'instant'});
}
$('#home-search').onsubmit = e => {e.preventDefault(); search($('#home-keyword').value);};
$('#header-search').onsubmit = e => {e.preventDefault(); search($('#header-keyword').value);};
$('.brand').onclick = event => {if (!event.button && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) goHome(event);};
for (const link of document.querySelectorAll('.primary-nav a, .discover-tabs a')) {
  link.onclick = event => {
    if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (link.dataset.page === 'home') goHome(); else showDiscover(link.dataset.discover || 'recommend');
  };
}
$('#stop-search').onclick = stopSearch;
$('.shelf-open').onclick = () => {renderShelf(); $('#shelf-dialog').showModal(); $('#shelf-query').focus(); maybeAutoUpdate();};
$('#close-shelf').onclick = () => $('#shelf-dialog').close();
$('#downloads-open').onclick = () => downloads.open();
for (const [selector, event] of [['#shelf-sort', 'onchange'], ['#shelf-query', 'oninput'], ['#shelf-only-updated', 'onchange'], ['#shelf-only-favorites', 'onchange']]) {
  $(selector)[event] = () => {state.shelfPage = 0; renderShelf();};
}
$('#shelf-auto-updates').checked = autoUpdates;
$('#shelf-auto-updates').onchange = event => {autoUpdates = event.target.checked; persist('revyunman.autoUpdates.v1', autoUpdates); if (autoUpdates) maybeAutoUpdate(); else libraryUpdates.stop();};
$('#shelf-check-updates').onclick = () => checkShelfUpdates();
$('#shelf-retry-updates').onclick = () => checkShelfUpdates(shelf.filter(book => updateErrors.has(key(book))));
function flushShelf() {try {libraryStore.flush();} catch {toast('最新记录尚未保存，请导出书架备份');}}
window.addEventListener('pagehide', () => {libraryUpdates.stop(); flushShelf();});
document.addEventListener('visibilitychange', () => {if (document.hidden) {libraryUpdates.stop(); flushShelf();} else maybeAutoUpdate();});
window.addEventListener('storage', async event => {
  if (!event.key?.startsWith(SHELF_PREFIX)) return;
  const generation = shelfSaveGeneration;
  let synced;
  try {synced = await libraryStore.sync();} catch {toast('无法读取其他标签页的更新，当前记录仍可导出'); return;}
  if (generation !== shelfSaveGeneration) return;
  shelf = synced; shelfChanged(); if ($('#shelf-dialog').open) renderShelf();
  if ($('#detail-dialog').open && state.book && state.chapters.length && !reader.isOpen()) renderDetail();
  reader.refreshReadingState?.();
});
$('#shelf-dialog').addEventListener('keydown', event => {if (event.key === 'Escape' && !event.isComposing) {event.preventDefault(); $('#shelf-dialog').close();}});
$('#detail-dialog').addEventListener('cancel', e => {e.preventDefault(); closeDetail();});
for (const id of ['detail-dialog', 'shelf-dialog']) $('#' + id).addEventListener('click', e => {if (e.target !== e.currentTarget) return; const r = e.currentTarget.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) id === 'detail-dialog' ? closeDetail() : e.currentTarget.close();});
window.addEventListener('popstate', restoreRoute);
function downloadJson(contents, label) {const blob = new Blob([contents], {type: 'application/json'}); const url = URL.createObjectURL(blob); const a = el('a'); a.href = url; a.download = `${label}-${new Date().toISOString().slice(0, 10)}.json`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);}
$('#export-shelf').onclick = () => {saveProgress(); try {downloadJson(serializeShelfBackup(shelf), 'Sardina书架');} catch (error) {toast(error.message);}};
$('#export-shelf-recovery').onclick = () => {try {downloadJson(libraryStore.recovery(), 'Sardina原始恢复记录');} catch (error) {toast(error.message);}};
$('#import-shelf').onclick = () => $('#shelf-file').click();
$('#shelf-file').onchange = async e => {
  const file = e.target.files[0]; if (!file) return;
  try {if (file.size > MAX_BACKUP_BYTES) throw new Error('备份文件超过 50 MB 上限');
    const books = readShelfBackup(await file.text());
    shelf = mergeShelfBackup(shelf, books); const saved = await saveShelf(); renderShelf(); if (saved) toast(`已合并 ${books.length} 本漫画，保留较新的阅读进度与分类`);
  } catch (error) {toast('导入失败：' + error.message);} finally {e.target.value = '';}
};
async function init() {
  await saveShelf({intent: 'passive'}); renderHistory();
  if (libraryStore.issues.length) toast(libraryStore.issues.join('；'));
  const [sites, home] = await Promise.allSettled([api('/api/sites'), api('/api/home-sections')]);
  if (sites.status === 'fulfilled') state.sites = sites.value;
  else toast('漫画源连接失败，请刷新重试');
  if (home.status === 'fulfilled') coverWall.setBooks(home.value.featured || []);
  await restoreRoute();
  maybeAutoUpdate();
}
init();
