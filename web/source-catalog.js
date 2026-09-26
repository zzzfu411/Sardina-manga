import {loadSourcePreferences, saveSourcePreferences, sortSources, toggleFavorite} from './source-preferences.js';

export const SOURCE_STATUS = {
  integrated: '已接入', duplicate: '重复入口', blocked: '暂不可用', pending: '待适配',
};

export function filterSourceEntries(entries, query = '', status = '') {
  const needle = query.trim().normalize('NFKC').toLowerCase();
  return entries.filter(entry => (!status || entry.status === status) &&
    (!needle || [entry.name, entry.origin, entry.mappedName, entry.collection].some(value =>
      typeof value === 'string' && value.normalize('NFKC').toLowerCase().includes(needle))));
}

const node = (tag, className, text) => {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
};

export function createSourceCatalog({root, api, onPreferencesChange = () => {}}) {
  const query = root.querySelector('[data-catalog-query]');
  const filter = root.querySelector('[data-catalog-filter]');
  const summary = root.querySelector('[data-catalog-summary]');
  const active = root.querySelector('[data-catalog-active]');
  const list = root.querySelector('[data-catalog-list]');
  const count = root.querySelector('[data-catalog-count]');
  let data = null, request = null, generation = 0;
  let preferences = loadSourcePreferences();
  const favoriteNotice = node('p', 'catalog-favorite-notice');
  favoriteNotice.id = 'catalog-favorite-notice';
  favoriteNotice.setAttribute('role', 'status');
  favoriteNotice.setAttribute('aria-live', 'polite');
  active.before(favoriteNotice);

  function renderActive(focusId) {
    if (!data) return;
    favoriteNotice.textContent = '星标常用源，下次搜索时优先。每次仍搜索全部图源。';
    favoriteNotice.classList.remove('catalog-favorite-error');
    const favorites = new Set(preferences.favoriteIds);
    active.replaceChildren();
    for (const site of sortSources(data.activeSources, preferences)) {
      const button = node('button', 'catalog-source-favorite');
      button.type = 'button';
      button.dataset.siteId = site.siteId;
      const star = node('span', 'catalog-source-star', favorites.has(site.siteId) ? '★' : '☆');
      star.setAttribute('aria-hidden', 'true');
      const info = node('span', 'catalog-source-info');
      info.append(node('span', 'catalog-source-name', site.siteName));
      if (Array.isArray(site.discoveryModes)) {
        const labels = site.discoveryModes.map(kind => ({popular: '排行榜', latest: '最近更新'})[kind]).filter(Boolean);
        info.append(node('span', 'catalog-source-capabilities', labels.join(' · ') || '发现列表待接入'));
      }
      const labels = {search: '搜索', details: '目录', chapter: '章节', image: '正文图片', coverImage: '封面'};
      const observed = Object.entries(site.health || {}).filter(([key]) => labels[key]);
      const latest = observed.sort((a, b) => String(b[1].checkedAt).localeCompare(String(a[1].checkedAt)))[0];
      const health = node('span', 'catalog-source-health');
      const states = {ok: '成功', empty: '无匹配', limited: '部分可用', error: '失败'};
      health.textContent = latest ? `最近${labels[latest[0]]}${states[latest[1].status] || '已检查'} · ${(latest[1].elapsedMs / 1000).toFixed(1)} 秒` : '暂无实际请求记录';
      const recentFailures = observed.filter(([, row]) => row.lastFailureAt && Date.now() - Date.parse(row.lastFailureAt) >= 0 && Date.now() - Date.parse(row.lastFailureAt) < 15 * 60000);
      if (recentFailures.length) health.textContent += ` · 近期${recentFailures.map(([key]) => labels[key]).join('/')}有失败`;
      health.title = observed.map(([key, row]) => `${labels[key]}：${states[row.status]} · ${row.checkedAt}${row.error ? ' · ' + row.error : ''}${row.lastFailureAt ? `；最近失败 ${row.lastFailureAt} ${row.lastFailure || ''}` : ''}`).join('\n') || '状态会根据实际搜索和阅读更新';
      if (latest?.[1]?.checkedAt) health.textContent += ' · ' + new Date(latest[1].checkedAt).toLocaleString('zh-CN', {month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit'});
      info.append(health);
      button.append(star, info);
      button.setAttribute('aria-pressed', String(favorites.has(site.siteId)));
      button.setAttribute('aria-label', `${favorites.has(site.siteId) ? '取消常用源' : '设为常用源'}：${site.siteName}`);
      button.addEventListener('click', () => {
        const next = toggleFavorite(preferences, site.siteId);
        if (!saveSourcePreferences(next)) {
          favoriteNotice.textContent = '常用源未保存。请允许浏览器保存网站数据后重试。';
          favoriteNotice.classList.add('catalog-favorite-error');
          return;
        }
        preferences = next;
        renderActive(site.siteId);
        favoriteNotice.textContent = `${site.siteName}已${preferences.favoriteIds.includes(site.siteId) ? '设为' : '取消'}常用源。下次搜索时生效，仍搜索全部图源。`;
        onPreferencesChange({favoriteIds: [...preferences.favoriteIds]});
      });
      active.append(button);
      if (site.siteId === focusId) button.focus({preventScroll: true});
    }
  }

  function render() {
    if (!data) return;
    const entries = filterSourceEntries(data.entries, query.value, filter.value);
    count.textContent = `${entries.length} / ${data.entryCount} 条资料`;
    list.replaceChildren();
    for (const entry of entries) {
      const row = node('li', 'catalog-row');
      const heading = node('div', 'catalog-row-heading');
      heading.append(node('strong', '', entry.name), node('span', `catalog-badge catalog-${entry.status}`, SOURCE_STATUS[entry.status] || '待适配'));
      row.append(heading, node('p', 'catalog-origin', entry.origin || '尚未提取到可验证的入口'));
      const provenance = [entry.collection, entry.mappedName ? `归入 ${entry.mappedName}` : ''].filter(Boolean).join(' · ');
      row.append(node('p', 'catalog-provenance', provenance), node('p', 'catalog-reason', entry.reason));
      list.append(row);
    }
    if (!entries.length) list.append(node('li', 'catalog-empty', '没有匹配的源资料，试试其他名称或状态。'));
  }

  function close() {
    generation++; request?.abort(); request = null;
    if (root.open) root.close();
  }
  async function open() {
    if (!root.open) root.showModal();
    preferences = loadSourcePreferences();
    if (data) {renderActive(); render(); query.focus();}
    request?.abort();
    const controller = new AbortController(); request = controller;
    const turn = ++generation;
    if (!data) {summary.textContent = '正在读取源目录…'; count.textContent = ''; list.replaceChildren();}
    try {
      const result = await api('/api/source-catalog', null, controller.signal);
      if (controller.signal.aborted || turn !== generation || !root.open) return;
      data = result;
      summary.textContent = `${data.activeSourceCount} 个搜索源 · ${data.ruleEntryCount} 条新规则 · 核对日期 ${data.updatedAt}`;
      const focusedSite = active.contains(document.activeElement) ? document.activeElement.dataset.siteId : null;
      renderActive(focusedSite);
      render();
    } catch (error) {
      if (controller.signal.aborted || turn !== generation) return;
      summary.textContent = `源目录加载失败：${error.message}`;
      const retry = node('button', 'quiet', '重新加载'); retry.type = 'button'; retry.onclick = open;
      const row = node('li', 'catalog-empty'); row.append(retry); list.append(row);
    } finally {if (request === controller) request = null;}
  }
  query.addEventListener('input', render);
  filter.addEventListener('change', render);
  root.querySelector('[data-catalog-close]').addEventListener('click', close);
  root.addEventListener('cancel', event => {event.preventDefault(); close();});
  root.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !event.isComposing) {
      // Search inputs otherwise consume the first Escape to clear their text.
      event.preventDefault(); event.stopPropagation(); close();
    }
  });
  root.addEventListener('click', event => {
    if (event.target !== root) return;
    const bounds = root.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) close();
  });
  return {open, close};
}
