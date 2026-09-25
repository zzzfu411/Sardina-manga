import test from 'node:test';
import assert from 'node:assert/strict';
import {createDetailLoader} from '../web/search-view.js';

const book = id => ({siteId: 'source', detailUrl: `https://example.org/${id}`});
const tick = () => new Promise(resolve => setImmediate(resolve));
function transport({honorAbort = false} = {}) {
  const requests = [];
  const api = (path, body, signal) => new Promise((resolve, reject) => {
    const request = {path, body, signal, resolve, reject}; requests.push(request);
    if (honorAbort) signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), {once: true});
  });
  return {api, requests};
}

test('detail requests are bounded at two and duplicate consumers share transport', async () => {
  const {api, requests} = transport(), loader = createDetailLoader(api);
  const a = loader.load(book(1)), again = loader.load(book(1)), b = loader.load(book(2)), c = loader.load(book(3));
  await tick();
  assert.equal(requests.length, 2);
  assert.equal(loader.stats().pending, 1);
  requests[0].resolve({chapters: [{name: '第一话'}]});
  const [first, duplicate] = await Promise.all([a, again]);
  assert.equal(first, duplicate);
  await tick();
  assert.equal(requests.length, 3);
  assert.equal(loader.stats().active, 2);
  requests[1].resolve({chapters: []}); requests[2].resolve({chapters: []});
  await Promise.all([b, c]); loader.destroy();
});

test('aborting one consumer does not cancel another consumer of the same work', async () => {
  const {api, requests} = transport(), loader = createDetailLoader(api), controller = new AbortController();
  const canceled = loader.load(book(1), controller.signal), surviving = loader.load(book(1));
  const cancellation = assert.rejects(canceled, {name: 'AbortError'});
  await tick(); controller.abort(); await cancellation;
  assert.equal(requests[0].signal.aborted, false);
  requests[0].resolve({title: '作品', chapters: []});
  assert.equal((await surviving).title, '作品'); loader.destroy();
});

test('canceling a queued request never starts its transport', async () => {
  const {api, requests} = transport(), loader = createDetailLoader(api), controller = new AbortController();
  const a = loader.load(book(1)), b = loader.load(book(2)), queued = loader.load(book(3), controller.signal);
  const cancellation = assert.rejects(queued, {name: 'AbortError'});
  await tick(); controller.abort(); await cancellation;
  requests.forEach(request => request.resolve({chapters: []})); await Promise.all([a, b]); await tick();
  assert.equal(requests.length, 2); loader.destroy();
});

test('cancelAll rejects stale consumers and does not cache a late stale response', async () => {
  const {api, requests} = transport(), loader = createDetailLoader(api);
  const pending = loader.load(book(1)), cancellation = assert.rejects(pending, {name: 'AbortError'});
  await tick(); loader.cancelAll(); await cancellation;
  assert.equal(requests[0].signal.aborted, true);
  requests[0].resolve({title: '迟到的旧结果', chapters: []}); await tick();
  assert.equal(loader.peek(book(1)), undefined);
  const fresh = loader.load(book(1)); await tick();
  assert.equal(requests.length, 2);
  requests[1].resolve({title: '新结果', chapters: []}); assert.equal((await fresh).title, '新结果'); loader.destroy();
});

test('an aborted transport that settles late continues occupying its concurrency slot', async () => {
  const {api, requests} = transport(), loader = createDetailLoader(api, {limit: 1});
  const a = loader.load(book(1)), canceled = assert.rejects(a, {name: 'AbortError'});
  await tick(); loader.cancelAll(); await canceled;
  const b = loader.load(book(2)); await tick();
  assert.equal(requests.length, 1);
  requests[0].resolve({chapters: []}); await tick();
  assert.equal(requests.length, 2);
  requests[1].resolve({chapters: []}); await b; loader.destroy();
});

test('successful details are cached while failed details can be retried', async () => {
  let calls = 0;
  const loader = createDetailLoader(async () => { calls++; if (calls === 1) throw new Error('暂时失败'); return {chapters: []}; });
  await assert.rejects(loader.load(book(1)), /暂时失败/); await tick();
  await loader.load(book(1)); await tick();
  await loader.load(book(1)); assert.equal(calls, 2); loader.destroy();
});

test('bounded LRU cache drops the least recently used detail', async () => {
  const loader = createDetailLoader(async (path, body) => ({title: body.detailUrl, chapters: []}), {cacheSize: 2});
  await loader.load(book(1)); await loader.load(book(2)); loader.peek(book(1)); await loader.load(book(3));
  assert.ok(loader.peek(book(1))); assert.equal(loader.peek(book(2)), undefined); assert.ok(loader.peek(book(3)));
  loader.destroy();
});

test('a pre-aborted signal and destroyed loader never start requests', async () => {
  let calls = 0;
  const loader = createDetailLoader(async () => { calls++; return {}; }), controller = new AbortController(); controller.abort();
  await assert.rejects(loader.load(book(1), controller.signal), {name: 'AbortError'});
  loader.destroy(); await assert.rejects(loader.load(book(2)), {name: 'AbortError'});
  assert.equal(calls, 0);
});
