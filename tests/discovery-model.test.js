import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeDiscoverySources, chooseDiscoverySelection, normalizeDiscoveryResult, createDiscoveryModel, createDiscoveryPagination, externalUrl} from '../web/discovery-model.js';

const sources = [
  {siteId: 'gui', siteName: '漫画柜', modes: [
    {kind: 'popular', label: '人气榜', periods: [{id: 'day', label: '日榜'}, {id: 'month', label: '月榜'}]},
    {kind: 'latest', label: '最近更新', periods: []},
  ]},
  {siteId: 'manben', siteName: '漫本', modes: [{kind: 'popular', label: '热门漫画', periods: []}]},
  {siteId: 'latest-only', siteName: '更新源', modes: [{kind: 'latest', label: '最近更新', periods: []}]},
];
const item = (id, extra = {}) => ({title: `作品 ${id}`, detailUrl: `https://example.test/comic/${id}`, coverUrl: `https://images.example.test/${id}.jpg`, ...extra});
const list = (body, extra = {}) => ({...body, siteName: '漫画柜', items: [item('one')], hasMore: false,
  sourceUrl: 'https://example.test/rank/', fetchedAt: '2026-09-21T08:30:00Z', label: '真实源榜单', ...extra});
const tick = () => new Promise(resolve => setImmediate(resolve));

function harness() {
  const calls = [], changes = [];
  const model = createDiscoveryModel({api: (path, body, signal) => new Promise((resolve, reject) => {
    calls.push({path, body, signal, resolve, reject});
  }), onChange: state => changes.push(state)});
  return {model, calls, changes};
}
async function ready(extra = {}) {
  const h = harness(), promise = h.model.show();
  h.calls[0].resolve(sources); await tick();
  h.calls[1].resolve(list(h.calls[1].body, extra)); await promise;
  return h;
}

test('a direct latest route loads the requested type without fetching a default ranking first', async () => {
  const h = harness(), promise = h.model.show({kind: 'latest'});
  h.calls[0].resolve(sources); await tick();
  assert.deepEqual(h.calls[1].body, {siteId: 'gui', kind: 'latest', period: '', page: 1});
  h.calls[1].resolve(list(h.calls[1].body)); await promise;
  assert.equal(h.model.getState().data.kind, 'latest');
});

test('switching page tabs before capabilities arrive honours the latest requested kind', async () => {
  const h = harness(), promise = h.model.show({kind: 'popular'});
  h.model.show({kind: 'latest'});
  h.calls[0].resolve(sources); await tick();
  assert.equal(h.calls[1].body.kind, 'latest');
  h.calls[1].resolve(list(h.calls[1].body, {hasMore: true})); await promise;
  const next = h.model.next(); h.calls[2].resolve(list(h.calls[2].body)); await next;
  h.model.hide(); await h.model.show({kind: 'latest'});
  assert.equal(h.calls.length, 3);
  assert.equal(h.model.getState().selection.page, 2);
});

test('showing a different page tab replaces a pending list and ignores its late response', async () => {
  const h = await ready();
  const previous = h.model.refresh();
  const latest = h.model.show({kind: 'latest'});
  assert.equal(h.calls[2].signal.aborted, true);
  h.calls[3].resolve(list(h.calls[3].body)); await latest;
  h.calls[2].resolve(list(h.calls[2].body)); await previous;
  assert.equal(h.model.getState().data.kind, 'latest');
});

test('source capabilities determine available kind, source and period without invented dimensions', () => {
  const normalized = normalizeDiscoverySources({sources});
  assert.deepEqual(chooseDiscoverySelection(normalized), {siteId: 'gui', kind: 'popular', period: 'day', page: 1});
  assert.deepEqual(chooseDiscoverySelection(normalized, {siteId: 'manben', kind: 'latest', period: 'month', page: -2}),
    {siteId: 'gui', kind: 'latest', period: '', page: 1});
  assert.deepEqual(chooseDiscoverySelection(normalized, {siteId: 'manben', kind: 'popular', period: 'day'}),
    {siteId: 'manben', kind: 'popular', period: '', page: 1});
  assert.deepEqual(chooseDiscoverySelection([normalized[2]]), {siteId: 'latest-only', kind: 'latest', period: '', page: 1});
  assert.equal(chooseDiscoverySelection([]), null);
});

test('malformed capabilities cannot become arbitrary URLs or invented periodless modes', () => {
  assert.throws(() => normalizeDiscoverySources({oops: []}), /格式/);
  const rows = normalizeDiscoverySources([
    {...sources[0], siteId: 'https://bad.test'}, sources[0], sources[0],
    {siteId: 'bad', siteName: '无效', modes: [{kind: 'popular', periods: [{id: '../week', label: '周榜'}]}]},
    {siteId: 'other', siteName: '无效', modes: [{kind: 'unknown', periods: []}]},
  ]);
  assert.deepEqual(rows, [sources[0]]);
});

test('source order, explicit rank, chapter and actual update text are retained; missing rank stays missing', () => {
  const selected = chooseDiscoverySelection(sources);
  const payload = list(selected, {items: [item('second', {rank: 7}), item('first', {latestChapter: '第 10 话', updatedAtText: '昨天'}), item('second', {rank: 9})]});
  const result = normalizeDiscoveryResult(payload, selected, sources);
  assert.deepEqual(result.items.map(book => [book.title, book.rank]), [['作品 second', 7], ['作品 first', null]]);
  assert.equal(result.items[1].updatedAtText, '昨天');
  assert.equal(result.items[1].latestChapter, '第 10 话');
  assert.equal(result.items[0].updatedAtText, '');
  assert.equal(result.fetchedAt, '2026-09-21T08:30:00Z');
  assert.equal(result.items[0].siteId, 'gui');
});

test('wrong-source/page results and unknown pagination fail visibly rather than masquerading as a valid list', () => {
  const selected = chooseDiscoverySelection(sources);
  for (const difference of [{siteId: 'manben'}, {kind: 'latest'}, {period: ''}, {page: 2}]) {
    assert.throws(() => normalizeDiscoveryResult(list(selected, difference), selected, sources), /不一致/);
  }
  assert.throws(() => normalizeDiscoveryResult(list(selected, {hasMore: null}), selected, sources), /分页/);
  assert.throws(() => normalizeDiscoveryResult(list(selected, {items: [item('other', {siteId: 'manben'})]}), selected, sources), /来源/);
  assert.throws(() => normalizeDiscoveryResult(list(selected, {items: [{title: '打不开', detailUrl: 'javascript:alert(1)'}]}), selected, sources), /可打开/);
  assert.equal(externalUrl('javascript:alert(1)'), '');
  assert.equal(externalUrl('https://user:pass@example.test/'), '');
});

test('show discovers capabilities once and returning home reuses the current page without prefetching details', async () => {
  const h = await ready();
  assert.deepEqual(h.calls.map(call => call.path), ['/api/discovery/sources', '/api/discovery']);
  const snapshot = h.model.getState().data;
  h.model.hide(); await h.model.show(); await h.model.show();
  assert.equal(h.calls.length, 2);
  assert.equal(h.model.getState().data, snapshot);
  assert.equal(h.model.getState().visible, true);
});

test('rapid selection changes cancel old requests and late results cannot replace the current selection', async () => {
  const h = await ready();
  const old = h.model.select({siteId: 'manben', period: ''}), obsolete = h.calls.at(-1);
  const latest = h.model.select({siteId: 'gui', period: 'month'}), current = h.calls.at(-1);
  assert.equal(obsolete.signal.aborted, true);
  current.resolve(list(current.body, {items: [item('new')]})); await latest;
  obsolete.resolve(list(obsolete.body, {items: [item('old')]})); await old;
  assert.equal(h.model.getState().selection.period, 'month');
  assert.equal(h.model.getState().data.items[0].title, '作品 new');
});

test('hiding during capability loading aborts it and a late reply starts no list request', async () => {
  const h = harness(), opening = h.model.show();
  h.model.hide(); assert.equal(h.calls[0].signal.aborted, true);
  h.calls[0].resolve(sources); await opening;
  assert.equal(h.calls.length, 1);
  assert.equal(h.model.getState().data, null);
  assert.equal(h.model.getState().visible, false);
});

test('hiding during a list request prevents late writes; showing again resumes the chosen page', async () => {
  const h = await ready({hasMore: true});
  const loading = h.model.next(), hiddenRequest = h.calls.at(-1);
  h.model.hide(); assert.equal(hiddenRequest.signal.aborted, true);
  const showing = h.model.show(), visibleRequest = h.calls.at(-1);
  hiddenRequest.resolve(list(hiddenRequest.body, {items: [item('stale')]})); await loading;
  assert.equal(h.model.getState().phase, 'loading');
  assert.equal(visibleRequest.body.page, 2);
  visibleRequest.resolve(list(visibleRequest.body, {items: [item('fresh')]})); await showing;
  assert.equal(h.model.getState().data.items[0].title, '作品 fresh');
});

test('pagination uses hasMore, returns to page one on capability changes, and refresh is explicit', async () => {
  const h = await ready({hasMore: true});
  const next = h.model.next(), page2 = h.calls.at(-1);
  assert.equal(page2.body.page, 2); assert.equal(page2.body.refresh, undefined);
  page2.resolve(list(page2.body)); await next;
  const count = h.calls.length; await h.model.next(); assert.equal(h.calls.length, count);
  const changed = h.model.select({kind: 'latest', period: ''}), latest = h.calls.at(-1);
  assert.equal(latest.body.page, 1); assert.equal(latest.body.period, '');
  latest.resolve(list(latest.body)); await changed;
  const refreshing = h.model.refresh(), refresh = h.calls.at(-1);
  assert.equal(refresh.body.refresh, true);
  refresh.resolve(list(refresh.body)); await refreshing;
});

test('failed next page remains retryable and can return to the previous page', async () => {
  const h = await ready({hasMore: true});
  const next = h.model.next(); h.calls.at(-1).reject(new Error('来源连接失败')); await next;
  assert.equal(h.model.getState().phase, 'error');
  assert.equal(h.model.getState().error, '来源连接失败');
  const retrying = h.model.retry(), retry = h.calls.at(-1);
  assert.equal(retry.body.page, 2); retry.reject(new Error('仍未恢复')); await retrying;
  const previous = h.model.previous(), page1 = h.calls.at(-1);
  assert.equal(page1.body.page, 1); page1.resolve(list(page1.body)); await previous;
  assert.equal(h.model.getState().phase, 'ready');
});

test('source failure retries capabilities; a real empty list is successful and preserves attribution', async () => {
  const h = harness(), opening = h.model.show();
  h.calls[0].reject(new Error('断网')); await opening;
  assert.equal(h.model.getState().errorStage, 'sources');
  const retrying = h.model.retry(); h.calls[1].resolve(sources); await tick();
  h.calls[2].resolve(list(h.calls[2].body, {items: []})); await retrying;
  assert.equal(h.model.getState().phase, 'ready');
  assert.equal(h.model.getState().data.items.length, 0);
  assert.equal(h.model.getState().data.sourceUrl, 'https://example.test/rank/');
});

test('empty capability catalog can be refreshed and never fabricates a source', async () => {
  const h = harness(), opening = h.model.show(); h.calls[0].resolve([]); await opening;
  assert.equal(h.model.getState().phase, 'unavailable'); assert.equal(h.model.getState().selection, null);
  const refreshing = h.model.refresh(); h.calls[1].resolve(sources); await tick();
  h.calls[2].resolve(list(h.calls[2].body)); await refreshing;
  assert.equal(h.model.getState().phase, 'ready');
});

test('only a successful user page change requests viewport/focus movement', async () => {
  const h = await ready({hasMore: true}), moves = [];
  const pager = createDiscoveryPagination({model: h.model, onPageReady: (data, direction) => moves.push([data.page, direction])});
  h.model.hide(); await h.model.show();
  assert.deepEqual(moves, []);
  const paging = pager.next(), request = h.calls.at(-1);
  assert.deepEqual(moves, []);
  request.resolve(list(request.body)); await paging;
  assert.deepEqual(moves, [[2, 'next']]);
  await pager.next(); // No next page exists.
  assert.deepEqual(moves, [[2, 'next']]);
  const previous = pager.previous(), back = h.calls.at(-1);
  back.resolve(list(back.body)); await previous;
  assert.deepEqual(moves, [[2, 'next'], [1, 'previous']]);
});

test('cancelled or failed pagination never moves the viewport after hide or a filter switch', async () => {
  for (const action of ['hide', 'select', 'fail']) {
    const h = await ready({hasMore: true}), moves = [];
    const pager = createDiscoveryPagination({model: h.model, onPageReady: () => moves.push('moved')});
    const paging = pager.next(), old = h.calls.at(-1);
    if (action === 'hide') h.model.hide();
    if (action === 'select') h.model.select({siteId: 'manben'});
    if (action === 'fail') old.reject(new Error('请求失败'));
    else old.resolve(list(old.body));
    await paging;
    assert.deepEqual(moves, [], action);
  }
});

test('a newer selection committed before the pagination callback suppresses the old viewport effect', async () => {
  const h = await ready({hasMore: true}), moves = [];
  const pager = createDiscoveryPagination({model: h.model, onPageReady: () => moves.push('moved')});
  const paging = pager.next(), request = h.calls.at(-1);
  request.resolve(list(request.body));
  queueMicrotask(() => h.model.select({kind: 'latest'}));
  await paging;
  assert.deepEqual(moves, []);
  assert.equal(h.model.getState().selection.kind, 'latest');
});

test('expanded source capabilities preserve actual page bounds and cover lookup support', () => {
  const normalized = normalizeDiscoverySources([{siteId:'new-source',siteName:'新源',coverLookup:false,
    modes:[{kind:'popular',label:'人气排序',periods:[],maxPage:10},{kind:'latest',label:'首页更新',periods:[],maxPage:1}]}]);
  assert.equal(normalized[0].coverLookup,false);
  assert.equal(normalized[0].modes[0].maxPage,10);
  const selection=chooseDiscoverySelection(normalized,{kind:'latest',page:9});
  assert.equal(selection.page,1);
  const result=normalizeDiscoveryResult(list(selection,{items:[item('one',{coverLookup:true})],paginationNote:'当前首页更新展示'}),selection,normalized);
  assert.equal(result.items[0].coverLookup,false);
  assert.equal(result.paginationNote,'当前首页更新展示');
  assert.throws(()=>normalizeDiscoveryResult(list(selection,{hasMore:true}),selection,normalized),/分页/);
});
