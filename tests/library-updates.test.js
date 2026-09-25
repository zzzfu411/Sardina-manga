import test from 'node:test';
import assert from 'node:assert/strict';
import {MAX_UPDATE_REQUESTS, catalogSnapshot, catalogStateAfterCheck, normalizeCatalogState, mergeCatalogState, acknowledgeCatalog, createLibraryUpdates} from '../web/library-updates.js';

const detail = (...ids) => ({chapters: ids.map(id => ({name: `第${id}话`, url: `https://comic.example/chapter/${id}`}))});
const stateFor = (ids, now = 100) => catalogStateAfterCheck(null, catalogSnapshot(detail(...ids)), now);
const book = id => ({siteId: 'fixture', detailUrl: `https://comic.example/book/${id}`, title: `普通作品${id}`});
const key = book => `${book.siteId}::${book.detailUrl}`;
const turn = () => new Promise(resolve => setImmediate(resolve));

function harness(books) {
  const shelf = new Map(books.map(book => [key(book), {...book}]));
  const requests = [], results = [], statuses = [];
  let at = 1000;
  const updates = createLibraryUpdates({
    api: (path, body, signal) => new Promise((resolve, reject) => requests.push({path, body, signal, resolve, reject})),
    getBook: id => shelf.get(id), now: () => ++at,
    onBookResult: result => {
      results.push(result);
      const current = shelf.get(result.key);
      if (current && result.catalogState) shelf.set(result.key, {...current, catalogState: result.catalogState});
    },
    onStatus: status => statuses.push(status),
  });
  return {updates, shelf, requests, results, statuses};
}

test('catalogue snapshots ignore names, group labels, order and duplicate chapter URLs', () => {
  const before = detail(1, 2, 3);
  const after = {chapters: [...before.chapters].reverse().map(row => ({...row, name: '改名', group: '重新分组'}))};
  after.chapters.push({...after.chapters[0]});
  assert.deepEqual(catalogSnapshot(after), catalogSnapshot(before));
  const snapshot = catalogSnapshot(after);
  assert.deepEqual(Object.keys(snapshot).sort(), ['chapterCount', 'fingerprint']);
  assert.match(snapshot.fingerprint, /^v1:[0-9a-f]{16}$/);
  assert.equal(JSON.stringify(snapshot).includes('comic.example'), false);
  assert.equal(snapshot.chapterCount, 3);
});

test('first check establishes a baseline; growth, replacement and removal have distinct meanings', () => {
  const initial = stateFor([1, 2]);
  assert.equal(initial.change, '');
  assert.equal(initial.changedAt, 0);
  const growth = catalogStateAfterCheck(initial, catalogSnapshot(detail(1, 2, 3)), 200);
  assert.equal(growth.change, 'new');
  assert.equal(growth.changedAt, 200);
  assert.equal(catalogStateAfterCheck(initial, catalogSnapshot(detail(1, 9)), 200).change, 'changed');
  assert.equal(catalogStateAfterCheck(initial, catalogSnapshot(detail(1)), 200).change, 'changed');
});

test('checking an unchanged directory advances its check time without clearing an unseen change', () => {
  const changed = catalogStateAfterCheck(stateFor([1]), catalogSnapshot(detail(1, 2)), 200);
  const again = catalogStateAfterCheck(changed, catalogSnapshot(detail(2, 1)), 300);
  assert.equal(again.change, 'new');
  assert.equal(again.changedAt, 200);
  assert.equal(again.checkedAt, 300);
  assert.equal(catalogStateAfterCheck(again, catalogSnapshot(detail(1, 2)), 300).checkedAt, 301);
});

test('empty, restricted and partially invalid directories cannot produce a baseline', () => {
  for (const value of [null, {}, {chapters: []}, {...detail(1), unavailableReason: '部分章节受限'},
    {chapters: [{url: 'https://comic.example/1'}, {}]}, {chapters: [{url: 'javascript:bad'}]},
    {chapters: [{url: 'https://name:secret@comic.example/1'}]}]) {
    assert.throws(() => catalogSnapshot(value), /目录/);
  }
});

test('optional catalogue metadata validates its version, bounded fields and independent timestamps', () => {
  const initial = stateFor([1, 2]);
  assert.deepEqual(normalizeCatalogState({...initial, urls: ['should-not-persist']}), initial);
  for (const mutation of [{fingerprint: 'unknown'}, {chapterCount: 0}, {chapterCount: 1.5}, {chapterCount: 100001},
    {checkedAt: '100'}, {checkedAt: Infinity}, {changedAt: 101}, {change: 'unread'}]) {
    assert.equal(normalizeCatalogState({...initial, ...mutation}), null);
  }
  assert.equal(normalizeCatalogState(null), null);
});

test('metadata merging favours latest checkedAt and the current cleared notice on equal times', () => {
  const older = stateFor([1], 100), newer = {...stateFor([1, 2], 200), change: 'new', changedAt: 200};
  assert.deepEqual(mergeCatalogState(newer, older), newer);
  assert.deepEqual(mergeCatalogState(older, newer), newer);
  assert.deepEqual(mergeCatalogState({...newer, change: ''}, newer), {...newer, change: ''});
  assert.deepEqual(mergeCatalogState(newer, {broken: true}), newer);
});

test('cached details may establish a first baseline or acknowledge only a matching catalogue', () => {
  const baseline = acknowledgeCatalog(null, detail(1, 2), {now: 100});
  assert.deepEqual(baseline, stateFor([1, 2], 100));
  const changed = catalogStateAfterCheck(baseline, catalogSnapshot(detail(1, 2, 3)), 200);
  assert.deepEqual(acknowledgeCatalog(changed, detail(1, 2), {now: 300}), changed);
  const cleared = acknowledgeCatalog(changed, detail(3, 2, 1), {now: 300});
  assert.equal(cleared.change, '');
  assert.equal(cleared.checkedAt, 200);
  assert.equal(cleared.changedAt, 200);
  assert.deepEqual(acknowledgeCatalog(changed, {chapters: []}), changed);
});

test('fresh details may update a viewed baseline but cannot overwrite a newer check or imported version', () => {
  const baseline = stateFor([1], 100);
  const options = {fresh: true, startedAt: 150, baseState: baseline, now: 200};
  const seen = acknowledgeCatalog(baseline, detail(1, 2), options);
  assert.equal(seen.chapterCount, 2);
  assert.equal(seen.change, '');
  assert.equal(seen.checkedAt, 200);
  const imported = stateFor([7, 8, 9], 180);
  assert.deepEqual(acknowledgeCatalog(imported, detail(1, 2), options), imported);
  const sameTimeOtherVersion = stateFor([9], 100);
  assert.deepEqual(acknowledgeCatalog(sameTimeOtherVersion, detail(1, 2), options), sameTimeOtherVersion);
  assert.deepEqual(acknowledgeCatalog(baseline, detail(1, 2), {fresh: true, startedAt: 150, now: 200}), baseline);
});

test('the checker keeps caller priority, deduplicates books and makes at most two refreshed requests', async () => {
  const books = [book(1), book(2), book(3), book(4)];
  const h = harness(books);
  const completion = h.updates.start([books[2], books[0], books[2], books[3], books[1]]);
  assert.equal(MAX_UPDATE_REQUESTS, 2);
  assert.equal(h.requests.length, 2);
  assert.deepEqual(h.requests.map(r => r.body.detailUrl), [books[2].detailUrl, books[0].detailUrl]);
  for (const r of h.requests) {
    assert.equal(r.path, '/api/details');
    assert.equal(r.body.refresh, true);
    assert.ok(r.signal instanceof AbortSignal);
  }
  h.requests[0].resolve(detail(1)); await turn();
  assert.equal(h.requests.length, 3);
  h.requests[1].resolve(detail(1, 2)); await turn();
  assert.equal(h.requests.length, 4);
  h.requests[2].resolve(detail(1)); h.requests[3].resolve(detail(1));
  const status = await completion;
  assert.deepEqual(status, {running: false, total: 4, completed: 4, succeeded: 4, failed: 0, skipped: 0, active: 0, cancelled: 0});
  assert.ok(h.statuses.every(s => s.active <= 2));
  assert.ok(h.results.every(r => r.outcome === 'baseline' && r.catalogState.change === '' && r.error === ''));
  assert.ok(h.results.every(r => !('book' in r) && !('page' in r) && !('readAt' in r)));
  assert.equal(h.updates.isRunning(), false);
});

test('a failed source leaves its baseline intact while remaining books continue', async () => {
  const first = {...book(1), catalogState: stateFor([1, 2], 100)}, others = [book(2), book(3)];
  const h = harness([first, ...others]); const completion = h.updates.start([first, ...others]);
  h.requests[0].reject(new Error('源站暂时不可用')); await turn();
  assert.equal(h.requests.length, 3);
  h.requests[1].resolve({...detail(1), unavailableReason: '目录受限'});
  h.requests[2].resolve(detail(1));
  const status = await completion;
  assert.equal(status.failed, 2); assert.equal(status.succeeded, 1);
  assert.deepEqual(h.shelf.get(key(first)).catalogState, first.catalogState);
  assert.equal(h.results.find(r => r.key === key(first)).catalogState, null);
});

test('stop resolves immediately, aborts both requests and ignores late success and error without more statuses', async () => {
  const books = [book(1), book(2), book(3)]; const h = harness(books);
  const completion = h.updates.start(books);
  assert.equal(h.updates.stop(), true);
  const status = await completion;
  assert.equal(status.cancelled, 3); assert.equal(status.completed, 0);
  assert.ok(h.requests.every(r => r.signal.aborted));
  const statusCount = h.statuses.length;
  h.requests[0].resolve(detail(1)); h.requests[1].reject(new Error('late error')); await turn();
  assert.equal(h.requests.length, 2);
  assert.equal(h.results.length, 0);
  assert.equal(h.statuses.length, statusCount);
  assert.equal(h.updates.stop(), false);
});

test('starting another run invalidates old responses even when the adapter ignores abort', async () => {
  const books = [book(1), book(2)]; const h = harness(books);
  const old = h.updates.start(books);
  const fresh = h.updates.start([books[0]]);
  assert.equal((await old).cancelled, 2);
  assert.equal(h.requests.length, 2);
  h.requests[0].resolve(detail(1, 2, 3)); h.requests[1].reject(new Error('stale'));
  await turn();
  assert.equal(h.requests.length, 3);
  h.requests[2].resolve(detail(9));
  assert.equal((await fresh).succeeded, 1);
  assert.equal(h.results.length, 1);
  assert.equal(h.results[0].catalogState.fingerprint, catalogSnapshot(detail(9)).fingerprint);
});

test('repeated stop/start never exceeds two unsettled transports and cancelled waiting runs never fetch', async () => {
  const books = [book(1), book(2), book(3), book(4)]; const h = harness(books);
  const old = h.updates.start(books.slice(0, 2));
  const waiting = h.updates.start([books[2]]);
  const latest = h.updates.start([books[3]]);
  assert.equal((await old).cancelled, 2);
  assert.equal((await waiting).cancelled, 1);
  assert.equal(h.requests.length, 2);
  h.requests[0].resolve(detail(1)); await turn();
  assert.equal(h.requests.length, 3);
  assert.equal(h.requests[2].body.detailUrl, books[3].detailUrl);
  assert.equal(h.requests.some(r => r.body.detailUrl === books[2].detailUrl), false);
  h.requests[2].resolve(detail(4));
  assert.equal((await latest).succeeded, 1);
  h.requests[1].reject(new Error('very late')); await turn();
  assert.equal(h.results.length, 1);
});

test('deleted in-flight and queued books are skipped without being recreated', async () => {
  const books = [book(1), book(2), book(3)]; const h = harness(books);
  const completion = h.updates.start(books);
  h.shelf.delete(key(books[0])); h.shelf.delete(key(books[2]));
  h.requests[0].resolve(detail(1)); await turn();
  assert.equal(h.requests.length, 2);
  h.requests[1].resolve(detail(1));
  const status = await completion;
  assert.equal(status.skipped, 2); assert.equal(status.succeeded, 1);
  assert.equal(h.shelf.size, 1);
  assert.deepEqual(h.results.map(r => r.key), [key(books[1])]);
});

test('book results cannot overwrite reading progress or a newly chosen classification', async () => {
  const target = {...book(1), page: 1, readAt: 10, readingState: 'later'}; const h = harness([target]);
  const completion = h.updates.start([target]);
  const latest = {...target, chapterUrl: 'https://comic.example/chapter/20', page: 33, pageOffset: 0.63, readAt: 900, readingState: 'finished', stateChangedAt: 950};
  h.shelf.set(key(target), latest);
  h.requests[0].resolve(detail(1, 2)); await completion;
  const result = h.shelf.get(key(target));
  for (const field of ['chapterUrl', 'page', 'pageOffset', 'readAt', 'readingState', 'stateChangedAt']) assert.equal(result[field], latest[field]);
});

test('a newer catalogue imported while a request is in flight rejects late success and late errors', async () => {
  for (const fail of [false, true]) {
    const target = {...book(1), catalogState: stateFor([1], 100)}; const h = harness([target]);
    const completion = h.updates.start([target]);
    const imported = stateFor([8, 9], 1100);
    h.shelf.set(key(target), {...target, catalogState: imported});
    if (fail) h.requests[0].reject(new Error('stale failure')); else h.requests[0].resolve(detail(1, 2));
    assert.equal((await completion).skipped, 1);
    assert.equal(h.results.length, 0);
    assert.deepEqual(h.shelf.get(key(target)).catalogState, imported);
  }
});

test('acknowledging the current directory during a check does not restore its already-cleared notice', async () => {
  const changed = catalogStateAfterCheck(stateFor([1]), catalogSnapshot(detail(1, 2)), 200);
  const target = {...book(1), catalogState: changed}; const h = harness([target]);
  const completion = h.updates.start([target]);
  h.shelf.set(key(target), {...target, catalogState: acknowledgeCatalog(changed, detail(1, 2))});
  h.requests[0].resolve(detail(2, 1)); await completion;
  assert.equal(h.results[0].outcome, 'unchanged');
  assert.equal(h.shelf.get(key(target)).catalogState.change, '');
});

test('a detail acknowledgement after the response resolves remains cleared when the result commits', async () => {
  const changed = catalogStateAfterCheck(stateFor([1]), catalogSnapshot(detail(1, 2)), 200);
  const target = {...book(1), catalogState: changed}; const h = harness([target]);
  const completion = h.updates.start([target]);
  h.requests[0].resolve(detail(2, 1));
  // The network continuation runs first; acknowledgement happens before its
  // enclosing inspect() promise resumes the worker that delivers the callback.
  queueMicrotask(() => h.shelf.set(key(target), {...target, catalogState: acknowledgeCatalog(changed, detail(1, 2))}));
  const status = await completion;
  assert.equal(status.succeeded, 1);
  assert.equal(h.results[0].outcome, 'unchanged');
  assert.equal(h.results[0].catalogState.change, '');
  assert.equal(h.results[0].catalogState.changedAt, changed.changedAt);
  assert.ok(h.results[0].catalogState.checkedAt > changed.checkedAt);
  assert.equal(h.shelf.get(key(target)).catalogState.change, '');
  assert.deepEqual(Object.keys(h.results[0]).sort(), ['catalogState', 'detailUrl', 'error', 'key', 'outcome', 'siteId']);
});

test('an imported version between response and commit rejects both stale success and stale failure', async () => {
  for (const fail of [false, true]) {
    const target = {...book(1), catalogState: stateFor([1], 100)}; const h = harness([target]);
    const completion = h.updates.start([target]);
    const imported = stateFor([8, 9], 1100);
    if (fail) h.requests[0].reject(new Error('stale failure')); else h.requests[0].resolve(detail(1, 2));
    queueMicrotask(() => h.shelf.set(key(target), {...target, catalogState: imported}));
    const status = await completion;
    assert.equal(status.skipped, 1);
    assert.equal(status.succeeded, 0);
    assert.equal(status.failed, 0);
    assert.equal(h.results.length, 0);
    assert.deepEqual(h.shelf.get(key(target)).catalogState, imported);
  }
});

test('deletion between response and commit suppresses success and error callbacks', async () => {
  for (const fail of [false, true]) {
    const target = book(1); const h = harness([target]);
    const completion = h.updates.start([target]);
    if (fail) h.requests[0].reject(new Error('stale failure')); else h.requests[0].resolve(detail(1, 2));
    queueMicrotask(() => h.shelf.delete(key(target)));
    const status = await completion;
    assert.equal(status.skipped, 1);
    assert.equal(status.succeeded, 0);
    assert.equal(status.failed, 0);
    assert.equal(h.results.length, 0);
    assert.equal(h.shelf.size, 0);
  }
});

test('empty input finishes without fetching and invalid input cannot create a request', async () => {
  const h = harness([]);
  const status = await h.updates.start([null, {}, {siteId: 'fixture', detailUrl: 'javascript:bad'}]);
  assert.equal(status.total, 0); assert.equal(status.running, false);
  assert.equal(h.requests.length, 0);
});
