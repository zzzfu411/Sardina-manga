import test from 'node:test';
import assert from 'node:assert/strict';
import {api} from '../web/api.js';

test('API errors keep a readable message even when a proxy returns text or broken JSON', async t => {
  for (const body of ['error code: 502\n', '<html>Bad Gateway</html>', '{broken']) {
    t.mock.method(globalThis, 'fetch', async () => new Response(body, {status: 502}));
    await assert.rejects(api('/api/chapter-images', {purpose: 'download'}), /暂时不可用（HTTP 502）/);
  }
  t.mock.method(globalThis, 'fetch', async () => new Response('not JSON'));
  await assert.rejects(api('/api/details', {}), /数据不完整/);
  t.mock.method(globalThis, 'fetch', async () => Response.json({error: 'Komiic 今日图片配额已用尽'}, {status: 502}));
  await assert.rejects(api('/api/chapter-images', {}), /配额已用尽/);
});

test('chapter failures issue one request and respect cancellation', async t => {
  let calls = 0;
  t.mock.method(globalThis, 'fetch', async () => ++calls === 1 ? new Response('error code: 502', {status: 502}) : Response.json({data: {images: ['page']}}));
  await assert.rejects(api('/api/chapter-images', {}), /HTTP 502/);
  assert.equal(calls, 1);
  calls = 0;
  t.mock.method(globalThis, 'fetch', async () => {calls++; return Response.json({error: 'HTTP Error 403: Forbidden'}, {status: 502});});
  await assert.rejects(api('/api/chapter-images', {}), /漫画源暂时拒绝访问/);
  assert.equal(calls, 1);
  calls = 0;
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(api('/api/chapter-images', {}, controller.signal), {name: 'AbortError'});
  assert.equal(calls, 0);
});
