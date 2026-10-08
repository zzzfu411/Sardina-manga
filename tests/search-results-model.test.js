import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {bookKey, buildSearchModel, rekeyWorkStates, withAuthorEvidence} from '../web/search-model.js';
import {buildSearchResults, searchTitleKey} from '../web/search-results-model.js';
import {sameWork, workIdentity} from '../web/book-identity.js';

const row = (title, id, fields = {}) => ({title, detailUrl: `https://manga.example/${id}`, ...fields});
const group = (siteId, results) => ({siteId, siteName: siteId, results});
const allWorks = model => [...model.works, ...model.related, ...model.hidden];
const fixture = JSON.parse(readFileSync(new URL('./fixtures/search-duplicate-titles.json', import.meta.url)));

test('observed duplicate titles group without relying on compatible author credits', () => {
  const expected = [
    {'装备仔': 5, '装备我最强': 14, '装备妖精的日常': 8, '我的装备天下第一': 4, '我获得了神级装备': 10, '洁癖少年完全装备': 5, '入手神话级专属装备': 14},
    {'全知读者视角': 25}, {'妖神记': 17},
  ];
  fixture.forEach(({groups, keyword}, index) => {
    const original = structuredClone(groups), model = buildSearchResults(groups, keyword);
    for (const [title, count] of Object.entries(expected[index])) {
      const matches = model.works.filter(work => work.canonicalTitle === title);
      assert.equal(matches.length, 1, title);
      assert.equal(matches[0].books.length, count, title);
    }
    const keys = allWorks(model).flatMap(work => work.books.map(bookKey));
    assert.equal(keys.length, model.candidateCount);
    assert.equal(new Set(keys).size, model.candidateCount, 'every source entry appears exactly once');
    assert.deepEqual(buildSearchResults([...groups].reverse().map(g => ({...g, results: [...g.results].reverse()})), keyword), model);
    assert.deepEqual(groups, original, 'source responses are not mutated');
  });
});

test('every incremental source and detail update keeps one card per title and variant', () => {
  for (const {groups, keyword} of fixture) {
    const evidence = new Map();
    for (let count = 1; count <= groups.length; count++) {
      const current = groups.slice(0, count);
      const before = buildSearchResults(current, keyword);
      for (const work of allWorks(before)) {
        // A late artist/publisher/localized name must not split the card.
        evidence.set(bookKey(work.preferred), `新署名 ${count}`);
      }
      const after = buildSearchResults(withAuthorEvidence(current, evidence), keyword);
      assert.deepEqual(allWorks(after).map(w => w.key), allWorks(before).map(w => w.key));
      assert.equal(after.candidateCount, before.candidateCount);
    }
  }
});

test('search handles traditional characters, invisible formatting and explicit script labels', () => {
  for (const [left, right] of [['全知讀者視角', '全知读者视角'], ['潔癖少年完全裝備', '洁癖少年完全装备'],
    ['關於我轉生變成史萊姆這檔事', '关于我转生变成史莱姆这档事'], ['全知读者视角（简体）', '全知读者视角'],
    ['全知讀者視角【繁體中文版】', '全知读者视角'], ['全知\u200b读者视角', '全知读者视角']]) {
    assert.equal(searchTitleKey(left), searchTitleKey(right));
  }
  assert.notEqual(searchTitleKey('装备1'), searchTitleKey('装备2'));
  assert.notEqual(searchTitleKey('装备我最强(原版自译)'), searchTitleKey('装备我最强'));
  assert.notEqual(searchTitleKey('妖神记（全彩）'), searchTitleKey('妖神记'));
  assert.notEqual(searchTitleKey('全知读者视角同人'), searchTitleKey('全知读者视角'));
  assert.notEqual(searchTitleKey('第3月的狮子'), searchTitleKey('三月的狮子'));
});

test('traditional exact matches rank with simplified matches instead of hiding as related', () => {
  const model = buildSearchResults([group('a', [row('全知讀者視角', 1, {author: '甲'}), row('全知读者视角同人', 2)]),
    group('b', [row('全知读者视角', 3, {author: '乙'}), row('全知单恋视角', 4)])], '全知读者视角');
  assert.equal(model.workCount, 1);
  assert.equal(model.works[0].score, 100);
  assert.equal(model.works[0].books.length, 2);
  assert.equal(model.relatedCount, 2);
});

test('display grouping keeps conflicting identities and source records independent', () => {
  const a = {siteId: 'a', ...row('同名漫画', 1, {author: '甲'})}, b = {siteId: 'b', ...row('同名漫画', 2, {author: '乙'})};
  const groups = [group('a', [a]), group('b', [b]), group('c', [row('同名漫画', 3)])];
  const model = buildSearchResults(groups, '同名漫画');
  assert.equal(model.workCount, 1);
  assert.equal(model.works[0].books.length, 3);
  assert.equal(model.works[0].hasDifferentCredits, true, 'source picker needs credit labels for disambiguation');
  assert.notEqual(model.works[0].identityByBook.get(bookKey(a)), model.works[0].identityByBook.get(bookKey(b)), 'cover fallback must respect conflicting identities');
  assert.equal(buildSearchModel(groups, '同名漫画').workCount, 3, 'identity evidence remains conservative');
  assert.equal(sameWork(a, b), false);
  assert.notEqual(workIdentity(a), workIdentity(b));
});

test('late metadata preserves selected source, card key and chapter order', () => {
  const groups = [group('a', [row('同名漫画', 1, {author: '甲'})]), group('b', [row('同名漫画', 2)])];
  const before = buildSearchResults(groups, '同名漫画').works[0];
  const state = {work: before, selectedKey: 'b::https://manga.example/2', userSelected: true, mounted: true, reverse: true};
  const after = buildSearchResults(withAuthorEvidence(groups, new Map([[state.selectedKey, '乙']])), '同名漫画');
  assert.equal(after.workCount, 1);
  assert.equal(after.works[0].key, before.key);
  assert.equal(rekeyWorkStates(after.works, [state]).get(before.key), state);
});

test('edition and language distinctions remain visible while Chinese script variants group', () => {
  const groups = [group('a', [row('漫画', 1), row('漫画', 2, {edition: '全彩版'}), row('漫画', 3, {language: 'en'}),
    row('漫畫', 4, {language: 'zh-Hant'}), row('漫画（简体）', 5, {language: 'zh-CN'})])];
  const model = buildSearchResults(groups, '漫画');
  assert.equal(model.workCount, 3);
  assert.equal(model.works.find(work => work.books.length === 3).sourceCount, 1);
  assert.equal(new Set(model.works.map(work => work.key)).size, 3);
  assert.deepEqual(buildSearchResults([group('a', [...groups[0].results].reverse())], '漫画'), model);
});

test('source filters, source aliases and duplicate URLs retain their original semantics', () => {
  const groups = [group('a', [row('漫畫', 1), row('漫畫', 1, {author: '甲'})]), group('b', [row('漫画', 2, {author: '乙'})])];
  assert.equal(buildSearchResults(groups, '漫画').candidateCount, 2);
  const filtered = buildSearchResults(groups, '漫画', 'b');
  assert.equal(filtered.rawCount, 1);
  assert.equal(filtered.workCount, 1);
  assert.equal(filtered.works[0].preferred.siteId, 'b');
  assert.equal(filtered.works[0].hasDifferentCredits, false);
  const aliases = buildSearchResults([group('a', [row('别的书名', 3, {alternateTitles: ['目標漫畫']})])], '目标漫画');
  assert.equal(aliases.workCount, 1);
  assert.equal(aliases.works[0].title, '别的书名');
  assert.deepEqual(buildSearchResults(null).works, []);
});

test('the original March source corpus still yields one exact result with all alternatives', () => {
  const groups = JSON.parse(readFileSync(new URL('./fixtures/search-march-original.json', import.meta.url)));
  const model = buildSearchResults(groups, '三月的狮子');
  assert.equal(model.workCount, 1);
  assert.equal(model.works[0].books.length, 9);
  assert.equal(model.works[0].sourceCount, 8);
});
