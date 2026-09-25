import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {bookKey, buildSearchModel, canonicalTitle, normalizeTitle, normalizeAuthor, rekeyWorkStates, titleRelevance, withAuthorEvidence} from '../web/search-model.js';

const row = (title, id, fields = {}) => ({title, detailUrl: `https://manga.example/${id}`, ...fields});
const group = (siteId, rows) => ({siteId, siteName: siteId, results: rows});

test('missing comicbox source explains the exact-title regression without relaxing relevance', () => {
  const fixture = JSON.parse(readFileSync(new URL('./fixtures/source-parity/rich-sister-titles.json', import.meta.url)));
  const previous = buildSearchModel(fixture.local, fixture.keyword);
  assert.ok(!previous.works.some(work => work.title === fixture.keyword));
  const restored = buildSearchModel([...fixture.local, ...fixture.original], fixture.keyword);
  assert.equal(restored.workCount, 1);
  assert.equal(restored.works[0].title, fixture.keyword);
  assert.equal(restored.works[0].preferred.siteId, 'comicbox');
  assert.equal(restored.works[0].books.length, 1);
  assert.equal(titleRelevance(restored.works[0].title, fixture.keyword).score, 100);
  assert.ok(!restored.works[0].books.some(book => book.title === '富家女120'));
});

test('known numeric and traditional aliases identify the same work', () => {
  const names = ['三月的狮子', '三月的獅子', '3月的狮子', '３ 月 的 獅 子'];
  assert.equal(new Set(names.map(canonicalTitle)).size, 1);
  const model = buildSearchModel(names.map((title, i) => group(`source${i}`, [row(title, i)])), '三月的狮子');
  assert.equal(model.workCount, 1);
  assert.equal(model.works[0].sourceCount, 4);
  assert.equal(model.works[0].title, '三月的狮子');
});

test('normalization is bounded and does not rewrite every Chinese number', () => {
  assert.equal(normalizeTitle('《 龍珠： ＳＵＰＥＲ 》'), '龙珠super');
  assert.notEqual(canonicalTitle('三只猫'), canonicalTitle('3只猫'));
  assert.notEqual(canonicalTitle('第3月的狮子'), canonicalTitle('三月的狮子'));
});

test('editions retain their identities while alias spelling can match within an edition', () => {
  const rows = ['三月的狮子', '3月的獅子 外傳', '三月的狮子外传', '三月的狮子同人', '三月的狮子彩色版', '三月的狮子重制版'];
  const model = buildSearchModel([group('baozimh', rows.map((title, i) => row(title, i)))], '三月的狮子');
  assert.equal(model.workCount, 1);
  assert.equal(model.relatedCount, 4);
  assert.equal(model.related.find(work => work.canonicalTitle.endsWith('外传')).books.length, 2);
});

test('conflicting authors split homonyms; an unknown author cannot bridge them', () => {
  const model = buildSearchModel([
    group('a', [row('同一个名字', 'a', {author: '甲'})]),
    group('b', [row('同一个名字', 'b', {author: '乙'})]),
    group('c', [row('同一个名字', 'c')]),
  ], '同一个名字');
  assert.equal(model.workCount, 3);
  assert.deepEqual(new Set(model.works.map(work => work.authorKey)), new Set(['甲', '乙', '']));
});

test('author metadata has explicit boundaries and descriptions are not treated as authors', () => {
  const model = buildSearchModel([
    group('a', [row('作品', 'a', {extra: {author: '作者：張三、李四'}})]),
    group('b', [row('作品', 'b', {extra: {'作者': '李四,张三'}})]),
    group('c', [row('作品', 'c', {description: '这是完全不同的一段简介。'})]),
    group('d', [row('作品', 'd', {author: '未知'})]),
  ], '作品');
  assert.equal(model.workCount, 1);
  assert.equal(model.works[0].books.length, 4);
  assert.equal(normalizeAuthor('作者：張三、李四'), normalizeAuthor('李四,张三'));
});

test('exact titles beat preferred sources with weak results', () => {
  const model = buildSearchModel([
    group('hipmh', [row('三月的兔子们', 1), row('与搜索词无关', 2)]),
    group('dm5', [row('三月的狮子', 3)]),
  ], '三月的狮子');
  assert.equal(model.works[0].preferred.siteId, 'dm5');
  assert.equal(model.related[0].title, '三月的兔子们');
  assert.equal(model.hidden.length, 1);
});

test('partial queries show title containment; with an exact match editions are secondary', () => {
  const groups = [group('a', [row('海贼王', 1), row('海贼王外传', 2), row('海贼王彩色版', 3)])];
  assert.equal(buildSearchModel(groups, '海贼').workCount, 3);
  assert.equal(buildSearchModel(groups, '海贼王').workCount, 1);
  assert.equal(buildSearchModel(groups, '海贼王').relatedCount, 2);
  assert.equal(titleRelevance('甲乙丙丁', '狮子').score, 0);
});

test('duplicate source URLs enrich metadata without becoming extra sources', () => {
  const model = buildSearchModel([group('a', [row('作品', 1), row('作品', 1, {author: '作者甲', coverUrl: 'https://cdn.example/cover.jpg'})])], '作品');
  assert.equal(model.rawCount, 2);
  assert.equal(model.candidateCount, 1);
  assert.equal(model.works[0].books[0].author, '作者甲');
  assert.equal(model.works[0].sourceCount, 1);
});

test('malformed entries are ignored safely and source filters retain their own counts', () => {
  const groups = [group('a', [null, {}, row('', 1), row('作品', 2)]), group('b', [row('作品', 3)]), {siteId: 'c', results: null}];
  const model = buildSearchModel(groups, '作品');
  assert.equal(model.rawCount, 5);
  assert.equal(model.candidateCount, 2);
  assert.equal(buildSearchModel(groups, '作品', 'b').rawCount, 1);
  assert.deepEqual(buildSearchModel(null, '').works, []);
});

test('source arrival order cannot change the final grouped ordering', () => {
  const groups = [
    group('komiic', [row('3月的獅子', 1, {author: '羽海野千花'}), row('三月的兔子们', 11)]),
    group('baozimh', [row('三月的獅子', 2), row('随机作品', 12)]),
    group('manhuazhijia', [row('三月的狮子', 3, {author: '羽海野千花'})]),
  ];
  const forward = buildSearchModel(groups, '三月的狮子');
  const reverse = buildSearchModel([...groups].reverse(), '三月的狮子');
  assert.deepEqual(forward, reverse);
  const incremental = buildSearchModel(groups.slice(0, 2), '三月的狮子');
  assert.equal(incremental.works[0].key, forward.works[0].key);
  assert.equal(forward.works[0].preferred.siteId, 'manhuazhijia');
});

test('original 174-row March fixture becomes one exact work across eight sources', () => {
  const groups = JSON.parse(readFileSync(new URL('./fixtures/search-march-original.json', import.meta.url)));
  const model = buildSearchModel(groups, '三月的狮子');
  assert.equal(model.rawCount, 174);
  assert.equal(model.workCount, 1);
  assert.equal(model.works[0].title, '三月的狮子');
  assert.equal(model.works[0].sourceCount, 8);
  assert.equal(model.works[0].books.length, 9);
  assert.equal(model.works[0].preferred.siteId, 'manhuazhijia');
  assert.ok(model.related.some(work => work.title === '三月的兔子们'));
  assert.ok(model.hidden.length > 80);
  assert.deepEqual(buildSearchModel([...groups].reverse(), '三月的狮子'), model);
});

test('reference-site aliases match exactly across established Chinese and English names', () => {
  const families = [
    ['海贼王', '航海王', 'ONE PIECE', '海賊王'],
    ['死神', '境·界', 'BLEACH'],
    ['间谍过家家', '间谍家家酒', 'spy family', '間諜過家家'],
    ['进击的巨人', '进击巨人', '進擊的巨人'],
    ['鬼灭之刃', '鬼灭', '鬼滅'],
    ['咒术回战', '咒术', '咒術迴戰'],
    ['火影忍者', '火影'],
    ['一拳超人', '一击男', '一擊男'],
    ['钢之炼金术师', '钢炼', '鋼之鍊金術師'],
  ];
  for (const family of families) {
    const groups = family.map((title, i) => group(`source${i}`, [row(title, i)]));
    const model = buildSearchModel(groups, family[0]);
    assert.equal(model.workCount, 1, family.join(' / '));
    assert.equal(model.works[0].books.length, family.length);
  }
});

test('known alias substrings cannot merge unrelated names and edition suffixes stay separate', () => {
  assert.notEqual(canonicalTitle('死神少爷'), canonicalTitle('死神'));
  assert.notEqual(canonicalTitle('火影忍者外传'), canonicalTitle('火影忍者'));
  assert.equal(canonicalTitle('ONE PIECE 彩色版'), canonicalTitle('航海王彩色版'));
  const model = buildSearchModel([group('a', [row('死神', 1), row('BLEACH', 2), row('死神少爷', 3), row('死神外传', 4)])], '死神');
  assert.equal(model.workCount, 1);
  assert.equal(model.works[0].books.length, 2);
  assert.equal(model.relatedCount, 2);
});

test('authors discovered in details split a formerly ambiguous work without mutating search responses', () => {
  const groups = [group('a', [row('同名漫画', 1, {author: '甲'})]), group('b', [row('同名漫画', 2)]), group('c', [row('同名漫画', 3)])];
  const before = buildSearchModel(groups, '同名漫画');
  assert.equal(before.workCount, 1);
  const evidence = new Map([[bookKey({siteId: 'b', detailUrl: 'https://manga.example/2'}), '乙']]);
  const after = buildSearchModel(withAuthorEvidence(groups, evidence), '同名漫画');
  assert.equal(after.workCount, 3);
  assert.deepEqual(new Set(after.works.map(work => work.authorKey)), new Set(['甲', '乙', '']));
  assert.equal(groups[1].results[0].author, undefined);
  // Returning source responses may replace root-owned arrays; the separate
  // detail evidence still applies and must not silently merge them again.
  assert.equal(buildSearchModel(withAuthorEvidence(structuredClone(groups), evidence), '同名漫画').workCount, 3);
});

test('unknown detail authors cannot erase existing author conflicts', () => {
  const groups = [group('a', [row('同名漫画', 1, {author: '甲'})]), group('b', [row('同名漫画', 2, {author: '乙'})])];
  const evidence = new Map([['a::https://manga.example/1', '未知'], ['b::https://manga.example/2', '']]);
  assert.equal(buildSearchModel(withAuthorEvidence(groups, evidence), '同名漫画').workCount, 2);
});

test('author-driven splitting preserves the selected source and chapter order in its new work', () => {
  const groups = [group('a', [row('同名漫画', 1, {author: '甲'})]), group('b', [row('同名漫画', 2)])];
  const oldWork = buildSearchModel(groups, '同名漫画').works[0];
  const currentState = {work: oldWork, selectedKey: 'b::https://manga.example/2', userSelected: true, mounted: true, reverse: true};
  const evidence = new Map([[currentState.selectedKey, '乙']]);
  const works = buildSearchModel(withAuthorEvidence(groups, evidence), '同名漫画').works;
  const rekeyed = rekeyWorkStates(works, [currentState]);
  const selectedWork = works.find(work => work.authorKey === '乙');
  assert.equal(rekeyed.get(selectedWork.key), currentState);
  assert.equal(rekeyed.get(selectedWork.key).reverse, true);
  assert.equal(rekeyed.has(oldWork.key), false);
});

test('when verified author identity rejoins a work, the active user selection wins', () => {
  const groups = [group('a', [row('同名漫画', 1, {author: '甲'})]), group('b', [row('同名漫画', 2, {author: '乙'})])];
  const before = buildSearchModel(groups, '同名漫画');
  const states = before.works.map(work => ({work, selectedKey: bookKey(work.preferred), userSelected: work.authorKey === '乙', mounted: true, reverse: work.authorKey === '乙'}));
  const works = buildSearchModel(withAuthorEvidence(groups, new Map([['b::https://manga.example/2', '甲']])), '同名漫画').works;
  const rekeyed = rekeyWorkStates(works, states);
  assert.equal(rekeyed.size, 1);
  assert.equal(rekeyed.get(works[0].key).selectedKey, 'b::https://manga.example/2');
  assert.equal(rekeyed.get(works[0].key).reverse, true);
});
