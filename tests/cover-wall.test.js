import test from 'node:test';
import assert from 'node:assert/strict';
import {coverWallBooks, coverWallRows} from '../web/cover-wall.js';

const book = number => ({title: `普通作品 ${number}`, siteId: 'fixture', siteName: '测试源',
  detailUrl: `https://example.test/comic/${number}`, coverUrl: `https://images.example/cover-${number}.jpg`});

test('the cover pool accepts only public HTTP image URLs and remains bounded', () => {
  const invalid = [null, {}, {coverUrl: 'data:image/png;base64,bad'}, {coverUrl: 'javascript:bad'},
    {coverUrl: 'https://name:secret@images.example/cover.jpg'}, {coverUrl: '/relative.jpg'}];
  assert.deepEqual(coverWallBooks(invalid), []);
  const covers = coverWallBooks([...invalid, ...Array.from({length: 200}, (_, index) => book(index))]);
  assert.equal(covers.length, 32);
  assert.ok(covers.every(value => Object.keys(value).sort().join(',') === 'coverUrl,detailUrl,siteId,siteName,title'));
  assert.deepEqual(covers[0], book(0));
});

test('only a valid book identity can open details and unrelated metadata is discarded', () => {
  const first = book(1);
  assert.deepEqual(coverWallBooks([{...first, title:'  普通作品 1  ', arbitrary:'ignored'}]), [first]);
  for (const extra of [{title:''}, {detailUrl:'javascript:alert(1)'}, {detailUrl:'https://user:password@example.test/1'}, {siteId:''}]) {
    const normalized = coverWallBooks([{...first, ...extra}])[0];
    assert.equal(normalized.coverUrl, first.coverUrl);
    assert.equal(normalized.detailUrl, undefined);
  }
});

test('duplicates do not consume the pool while distinct source referers remain separate', () => {
  const first = book(1);
  const otherSource = {...first, siteId: 'other'};
  assert.deepEqual(coverWallBooks([first, first, {...first, title: '另一个显示名'}, otherSource]), [
    first, otherSource,
  ]);
});

test('four bounded rows stay deterministic for repeat groups and fill from a small pool', () => {
  assert.deepEqual(coverWallRows([]), []);
  for (const count of [1, 3, 8, 12, 32, 100]) {
    const books = Array.from({length: count}, (_, index) => book(index));
    const rows = coverWallRows(books), allowed = new Set(coverWallBooks(books).map(item => item.coverUrl));
    assert.equal(rows.length, 4);
    assert.ok(rows.every(row => row.length === 8 && row.every(item => allowed.has(item.coverUrl))));
    assert.deepEqual(coverWallRows(books), rows);
    if (count >= 8) assert.notDeepEqual(rows[0], rows[1]);
  }
});
