export const DISCOVERY_KINDS = {popular: '热门榜', latest: '最近更新'};

const text = value => typeof value === 'string' ? value.trim() : '';
const validId = value => typeof value === 'string' && /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$/.test(value);

export function externalUrl(value) {
  try {
    const url = new URL(value);
    return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : '';
  } catch {
    return '';
  }
}

export function normalizeDiscoverySources(payload) {
  const input = Array.isArray(payload) ? payload : payload?.sources;
  if (!Array.isArray(input)) throw new Error('漫画源列表格式不完整，请重试');
  const sources = [], seen = new Set();
  for (const source of input) {
    if (!validId(source?.siteId) || !text(source.siteName) || seen.has(source.siteId)) continue;
    const modes = [], kinds = new Set();
    for (const mode of Array.isArray(source.modes) ? source.modes : []) {
      if (!Object.hasOwn(DISCOVERY_KINDS, mode?.kind) || kinds.has(mode.kind)) continue;
      const periods = [], ids = new Set();
      for (const period of Array.isArray(mode.periods) ? mode.periods : []) {
        if (!validId(period?.id) || !text(period.label) || ids.has(period.id)) continue;
        ids.add(period.id); periods.push({id: period.id, label: text(period.label)});
      }
      // A malformed nonempty period list must not become an invented periodless mode.
      if (Array.isArray(mode.periods) && mode.periods.length && !periods.length) continue;
      kinds.add(mode.kind);
      modes.push({kind: mode.kind, label: text(mode.label) || DISCOVERY_KINDS[mode.kind], periods,
        ...(Number.isSafeInteger(mode.maxPage) && mode.maxPage >= 1 && mode.maxPage <= 1000 ? {maxPage: mode.maxPage} : {})});
    }
    if (!modes.length) continue;
    seen.add(source.siteId); sources.push({siteId: source.siteId, siteName: text(source.siteName), modes,
      ...(typeof source.coverLookup === 'boolean' ? {coverLookup: source.coverLookup} : {})});
  }
  return sources;
}

export function chooseDiscoverySelection(sources, preferred = {}) {
  const kind = sources.some(source => source.modes.some(mode => mode.kind === preferred.kind)) ? preferred.kind :
    ['popular', 'latest'].find(value => sources.some(source => source.modes.some(mode => mode.kind === value)));
  if (!kind) return null;
  const available = sources.filter(source => source.modes.some(mode => mode.kind === kind));
  const source = available.find(item => item.siteId === preferred.siteId) || available[0];
  const mode = source.modes.find(item => item.kind === kind);
  const period = mode.periods.find(item => item.id === preferred.period)?.id || mode.periods[0]?.id || '';
  const page = Number.isSafeInteger(preferred.page) && preferred.page > 0 ? Math.min(preferred.page, mode.maxPage || 1000) : 1;
  return {siteId: source.siteId, kind, period, page};
}

export function normalizeDiscoveryResult(payload, selection, sources) {
  if (!payload || !['siteId', 'kind', 'period', 'page'].every(key => payload[key] === selection[key])) {
    throw new Error('列表与当前选择不一致，请重试');
  }
  if (!Array.isArray(payload.items) || typeof payload.hasMore !== 'boolean') {
    throw new Error('列表或分页信息不完整，请重试');
  }
  const source = sources.find(item => item.siteId === selection.siteId);
  const mode = source.modes.find(item => item.kind === selection.kind);
  if (payload.hasMore && mode.maxPage && selection.page >= mode.maxPage) throw new Error('来源分页超出已支持范围，请重试');
  const items = [], seen = new Set();
  for (const item of payload.items) {
    if (!item || !text(item.title) || !externalUrl(item.detailUrl)) continue;
    if (item.siteId && item.siteId !== selection.siteId) throw new Error('作品来源与当前列表不一致，请重试');
    const detailUrl = externalUrl(item.detailUrl);
    if (seen.has(detailUrl)) continue;
    seen.add(detailUrl);
    items.push({...item, siteId: selection.siteId, siteName: source.siteName, title: text(item.title), detailUrl,
      coverUrl: externalUrl(item.coverUrl), latestChapter: text(item.latestChapter),
      coverLookup: source.coverLookup !== false,
      updatedAtText: text(item.updatedAtText),
      rank: Number.isSafeInteger(item.rank) && item.rank > 0 ? item.rank : null});
  }
  if (payload.items.length && !items.length) throw new Error('源站未返回可打开的作品，请重试');
  return {...payload, ...selection, siteName: source.siteName, items, sourceUrl: externalUrl(payload.sourceUrl),
    fetchedAt: typeof payload.fetchedAt === 'string' ? payload.fetchedAt : '', label: text(payload.label),
    paginationNote: text(payload.paginationNote).slice(0, 500)};
}

/** One visible selection owns one request; late responses cannot update a newer selection. */
export function createDiscoveryModel({api, onChange = () => {}}) {
  let state = {visible: false, phase: 'idle', sources: [], selection: null, data: null, error: '', errorStage: ''};
  let sourcesLoaded = false, request = null, generation = 0;
  const notify = () => onChange({...state});
  function cancel() {generation++; request?.abort(); request = null;}
  function begin(phase) {
    cancel();
    const controller = new AbortController(), turn = generation;
    request = controller; state = {...state, phase, error: '', errorStage: ''}; notify();
    return {controller, turn};
  }
  const current = run => state.visible && !run.controller.signal.aborted && run.turn === generation;
  async function loadList(refresh = false) {
    if (!state.visible || !state.selection) return;
    state = {...state, data: null};
    const selection = {...state.selection}, run = begin('loading');
    try {
      const payload = await api('/api/discovery', {...selection, ...(refresh ? {refresh: true} : {})}, run.controller.signal);
      if (!current(run)) return;
      const data = normalizeDiscoveryResult(payload, selection, state.sources);
      state = {...state, data, phase: 'ready'}; notify();
      return data;
    } catch (error) {
      if (!current(run)) return;
      state = {...state, phase: 'error', error: text(error?.message) || '暂时无法读取列表', errorStage: 'list'}; notify();
    } finally {if (request === run.controller) request = null;}
  }
  async function loadSources() {
    if (!state.visible) return;
    const run = begin('sources-loading');
    try {
      const payload = await api('/api/discovery/sources', null, run.controller.signal);
      if (!current(run)) return;
      const sources = normalizeDiscoverySources(payload);
      sourcesLoaded = true;
      state = {...state, sources, selection: chooseDiscoverySelection(sources, state.selection || {})};
      if (!state.selection) {state = {...state, phase: 'unavailable'}; notify(); return;}
      await loadList();
    } catch (error) {
      if (!current(run)) return;
      state = {...state, phase: 'error', error: text(error?.message) || '暂时无法读取漫画源', errorStage: 'sources'}; notify();
    } finally {if (request === run.controller) request = null;}
  }
  return {
    show({kind} = {}) {
      const changed = Object.hasOwn(DISCOVERY_KINDS, kind) && state.selection?.kind !== kind;
      if (changed) {
        const preferred = {...state.selection, kind, period: '', page: 1};
        state = {...state, selection: sourcesLoaded ? chooseDiscoverySelection(state.sources, preferred) : preferred,
          data: null, phase: state.phase === 'sources-loading' ? state.phase : 'idle'};
      }
      if (state.visible) {if (changed && sourcesLoaded) return loadList(); return;}
      state = {...state, visible: true};
      if (!sourcesLoaded) return loadSources();
      if (state.data || state.phase === 'error' || state.phase === 'unavailable') {notify(); return;}
      return loadList();
    },
    hide() {
      cancel();
      state = {...state, visible: false, phase: ['loading', 'sources-loading'].includes(state.phase) ? 'idle' : state.phase};
      notify();
    },
    select(changes) {
      if (!state.visible || !sourcesLoaded) return;
      const selection = chooseDiscoverySelection(state.sources, {...state.selection, ...changes, page: 1});
      if (!selection || JSON.stringify(selection) === JSON.stringify(state.selection)) return;
      state = {...state, selection}; return loadList();
    },
    refresh() {return sourcesLoaded && state.selection ? loadList(true) : loadSources();},
    retry() {return state.errorStage === 'sources' || !sourcesLoaded ? loadSources() : loadList();},
    previous() {
      if (!state.visible || !state.selection || state.selection.page <= 1 || !['ready', 'error'].includes(state.phase)) return;
      state = {...state, selection: {...state.selection, page: state.selection.page - 1}};
      return loadList();
    },
    next() {
      if (!state.visible || state.phase !== 'ready' || !state.data?.hasMore) return;
      state = {...state, selection: {...state.selection, page: state.selection.page + 1}};
      return loadList();
    },
    getState() {return {...state};},
  };
}

/** Pagination alone may move the viewport, and only for its still-current successful result. */
export function createDiscoveryPagination({model, onPageReady = () => {}}) {
  async function move(direction) {
    const data = await model[direction]();
    const state = model.getState();
    if (data && state.visible && state.phase === 'ready' && state.data === data) onPageReady(data, direction);
  }
  return {previous: () => move('previous'), next: () => move('next')};
}
