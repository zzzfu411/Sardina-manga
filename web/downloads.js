import {offlineReady} from './offline.js';
import {createDownloadStore, downloadKey} from './download-store.js';
import {createDownloadManager, downloadSelection} from './download-model.js';

const node = (tag, className = '', text = '') => {const element = document.createElement(tag); element.className = className; element.textContent = text; return element;};
const button = (text, action) => {const element = node('button', 'quiet', text); element.type = 'button'; element.onclick = action; return element;};
const size = bytes => `${(bytes / 1024 / 1024).toFixed(1)} MB`;

export function createDownloads({api, imageUrl, imageLoader, onOpen, toast, onShow = () => {}, onRequestClose = null}) {
  const store = createDownloadStore();
  let context = null, refreshId = 0, refreshTimer = null;
  const dialog = node('dialog', 'download-dialog'); dialog.id = 'downloads-dialog'; dialog.setAttribute('aria-labelledby', 'downloads-title');
  const head = node('header', 'download-head'), title = node('h2', '', '章节下载'); title.id = 'downloads-title';
  const requestClose = () => onRequestClose ? onRequestClose() : dialog.close();
  const close = button('关闭', requestClose); close.setAttribute('aria-label', '关闭下载管理'); head.append(title, close);
  const note = node('p', 'muted download-note');
  function updateOfflineNote() {note.textContent = offlineReady() ? '已准备离线阅读，可断网重新打开已下载章节。图片保存在此浏览器，不包含在书架备份中。' : '离线启动尚未就绪，请保持联网直至准备完成。章节保存在此浏览器，图片不包含在书架备份中；关闭页面会暂停下载。';}
  updateOfflineNote(); window.addEventListener('sardina-offline-ready', updateOfflineNote);
  const picker = node('form', 'download-picker'), bookName = node('strong');
  const startLabel = node('label', '', '起始章节'), start = node('select'); start.id = 'download-start'; startLabel.append(start);
  const countLabel = node('label', '', '章节数'), count = node('select'); count.id = 'download-count';
  for (const value of [1, 3, 5]) {const option = node('option', '', `${value} 章`); option.value = value; count.append(option);} countLabel.append(count);
  const submit = button('下载', () => {}); submit.type = 'submit'; submit.className = 'primary'; picker.append(bookName, startLabel, countLabel, submit);
  const summary = node('p', 'muted download-summary'); summary.setAttribute('role', 'status');
  const storageNote = node('p', 'muted download-note');
  const list = node('div', 'download-list'); dialog.append(head, note, picker, summary, storageNote, list); document.body.append(dialog);
  const scheduleRefresh = () => {if (dialog.open && !refreshTimer) refreshTimer = setTimeout(() => {refreshTimer = null; refresh();}, 120);};
  const manager = createDownloadManager({store, api, imageUrl, loadImage: imageLoader.load, onChange: scheduleRefresh});
  store.subscribe(scheduleRefresh);
  async function showStorage() {
    const storage = globalThis.navigator?.storage;
    const [estimate, persistent] = await Promise.all([storage?.estimate?.().catch(() => null), storage?.persisted?.().catch(() => false)]);
    storageNote.textContent = [estimate?.quota ? `浏览器空间 ${size(estimate.usage || 0)} / ${size(estimate.quota)}` : '', persistent ? '已获持久保存许可' : '浏览器可能在空间紧张时清理缓存'].filter(Boolean).join(' · ');
  }
  function render(records) {
    manager.reconcile(records);
    const focus = document.activeElement, focusId = focus?.dataset.downloadId, focusAction = focus?.dataset.action;
    const merged = new Map(records.map(record => [record.id, {id: record.id, book: record.book, chapter: record.chapter, chapters: record.chapters, record, status: record.complete ? 'complete' : 'paused'}]));
    for (const job of manager.jobs()) merged.set(job.id, {...merged.get(job.id), ...job});
    const values = [...merged.values()].sort((a, b) => (b.record?.updatedAt || Date.now()) - (a.record?.updatedAt || Date.now()));
    const bytes = records.reduce((sum, row) => sum + row.bytes, 0);
    summary.textContent = `${values.length} 章 · 已用 ${size(bytes)} / 1 GB`;
    list.replaceChildren();
    if (!values.length) list.append(node('p', 'muted', '还没有下载。在漫画详情或阅读器中选择章节。'));
    for (const job of values) {
      const row = node('article', 'download-row'), copy = node('div', 'download-copy');
      copy.append(node('strong', '', job.book.title), node('span', '', job.chapter.name));
      const persisted = records.find(row => row.id === job.id);
      const record = persisted && persisted.updatedAt >= (job.record?.updatedAt || 0) ? persisted : job.record;
      const total = record?.total || record?.urls?.length || 0, done = record?.count || 0;
      if (record?.complete) job.status = 'complete';
      const labels = {saving: '正在保存任务', loading: '正在获取图片列表', waiting: '等待其他标签页', queued: '等待下载', downloading: '下载中', complete: '已下载', paused: '已暂停', error: '下载失败'};
      const status = node('span', 'muted', `${labels[job.status]}${total ? ` · ${done} / ${total} 页 · ${size(record.bytes)}` : ''}`);
      copy.append(status);
      if (job.error) copy.append(node('p', 'download-error', job.error));
      const progress = node('progress'); progress.max = total || 1; progress.value = done; progress.setAttribute('aria-label', `${job.chapter.name}下载进度`); copy.append(progress);
      const actions = node('div', 'download-actions');
      function add(text, action, handler) {
        const control = button(text, () => Promise.resolve().then(handler).catch(error => toast(error.message)));
        control.dataset.downloadId = job.id; control.dataset.action = action; actions.append(control); return control;
      }
      const openRecord = async () => {const saved = await store.getChapter(job.book, job.chapter); if (saved) {dialog.close(); onOpen(saved);}};
      if (job.status === 'complete') add('阅读', 'read', openRecord);
      else if (job.running || ['saving', 'queued'].includes(job.status)) {
        const control = add('暂停', 'pause', () => manager.pause(job.id)); control.disabled = job.controller?.signal.aborted === true;
      } else add(job.status === 'error' ? '重试' : '继续下载', 'resume', () => manager.add(job));
      if (!job.running && (record?.retainedVersions || record?.backups?.length)) add('恢复旧缓存', 'restore', async () => {await store.restorePrevious(job.id); await openRecord();});
      add('删除', 'delete', async () => {await manager.remove(job.id); await refresh();});
      row.append(copy, actions); list.append(row);
    }
    if (focusId && focusAction) [...list.querySelectorAll('button')].find(control => control.dataset.downloadId === focusId && control.dataset.action === focusAction)?.focus({preventScroll: true});
  }
  async function refresh() {
    const id = ++refreshId;
    try {const records = await store.list(); if (id === refreshId && dialog.open) render(records);}
    catch (error) {if (id === refreshId) {summary.textContent = error.message || '下载空间暂不可用'; list.replaceChildren();}}
  }
  picker.onsubmit = event => {
    event.preventDefault(); if (!context) return;
    try {
      for (const chapter of downloadSelection(context.chapters, Number(start.value), Number(count.value))) manager.add({...context, chapter});
      globalThis.navigator?.storage?.persist?.().then(showStorage).catch(() => {});
      refresh();
    } catch (error) {toast(error.message);}
  };
  function open(selection = null, {restore = false} = {}) {
    if (!restore) context = selection; picker.hidden = !context; start.replaceChildren();
    summary.textContent = '正在读取下载…';
    if (context) {
      bookName.textContent = context.book.title;
      for (const [index, chapter] of context.chapters.entries()) {const option = node('option', '', chapter.name); option.value = index; start.append(option);}
      start.value = String(Math.max(0, context.chapters.findIndex(chapter => chapter.url === context.chapter?.url))); count.value = '1';
    }
    if (!dialog.open) {if (!restore) onShow(); dialog.showModal();} close.focus(); refresh(); showStorage();
  }
  dialog.addEventListener('cancel', event => {event.preventDefault(); requestClose();});
  dialog.addEventListener('keydown', event => {if (event.key === 'Escape' && !event.isComposing) {event.preventDefault(); event.stopPropagation(); requestClose();}});
  dialog.addEventListener('close', () => {refreshId++;});
  window.addEventListener('pagehide', () => manager.pauseAll());
  return {open, close: () => dialog.close(), store,
    async localDetail(book, chapterUrl) {
      if (!chapterUrl) return null;
      const record = await store.getChapter(book, {url: chapterUrl}).catch(() => null);
      return record?.complete ? {...record.book, chapters: record.chapters, savedOffline: true} : null;
    },
    getChapter: (book, chapter) => store.getChapter(book, chapter).catch(() => null),
    getPage: (record, index) => store.getPage(record, index).catch(() => null),
  };
}
