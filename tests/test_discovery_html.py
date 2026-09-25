import re
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from client import discovery_html as d
from client.html_metadata import parse_html, text_of


FIX = Path(__file__).parent / 'fixtures/discovery-html'
FILES = {
    'dm5': ('dm5-popular', 'dm5-latest'), 'mangabz': ('mangabz-classify', 'mangabz-latest'),
    'baozimh': ('baozimh', 'baozimh'), 'manhuazhijia': ('manhuazhijia-rank', 'manhuazhijia-latest'),
    'tuku': ('tuku-rank', 'tuku'), 'rumanhua': ('rumanhua-popular', 'rumanhua-latest'),
    'comicbox': ('comicbox-rank', 'comicbox'),
}


def fixture(name):
    return (FIX / (name + '.html')).read_text()


def fetch(site, kind='popular', period=None, page=1, source=None):
    period = ('week' if site == 'dm5' and kind == 'popular' else '') if period is None else period
    source = fixture(FILES[site][kind == 'latest']) if source is None else source
    with patch.object(d, 'read_text', return_value=source) as download:
        value = d.fetch(site, kind, period, page)
    download.assert_called_once_with(value['sourceUrl'], hosts=(urlsplit(value['sourceUrl']).hostname,))
    return value


class HtmlDiscoveryTests(unittest.TestCase):
    def test_capabilities_expose_only_verified_modes_periods_and_page_ranges(self):
        sites = d.sources()
        self.assertEqual([s['siteId'] for s in sites], list(FILES))
        self.assertTrue(all(s['coverLookup'] is False for s in sites))
        self.assertEqual(sites[0]['modes'][0]['periods'], [
            {'id': 'week', 'label': '周榜'}, {'id': 'month', 'label': '月榜'}, {'id': 'total', 'label': '总榜'}])
        for site in sites:
            self.assertEqual([mode['kind'] for mode in site['modes']], ['popular', 'latest'])
            self.assertTrue(all(mode['maxPage'] == (10 if site['siteId'] == 'mangabz' else 1) for mode in site['modes']))
        sites[0]['modes'][0]['periods'].clear()
        self.assertEqual(len(d.sources()[0]['modes'][0]['periods']), 3)

    def test_invalid_selection_is_rejected_before_any_network_request(self):
        cases = [(site, 'popular', '', 1) for site in (None, [], {}, True, 'unknown')]
        cases += [('baozimh', kind, '', 1) for kind in (None, [], True, 'ranking')]
        cases += [('baozimh', 'popular', period, 1) for period in (None, [], {}, 'day', True)]
        cases += [('dm5', 'popular', period, 1) for period in ('', 'day', 'year')]
        cases += [('mangabz', 'latest', '', page) for page in (True, False, 0, -1, 11, 1.5, '2', [], {})]
        cases += [('tuku', 'latest', '', 2)]
        with patch.object(d, 'read_text') as download:
            for args in cases:
                with self.subTest(args=args), self.assertRaises(ValueError):
                    d.fetch(*args)
        download.assert_not_called()

    def test_all_sources_parse_real_scoped_cards_and_return_source_attribution(self):
        for site in FILES:
            for kind in ['popular', 'latest']:
                with self.subTest(site=site, kind=kind):
                    data = fetch(site, kind)
                    self.assertEqual(data['siteId'], site)
                    self.assertEqual(data['kind'], kind)
                    self.assertEqual(len(data['items']), 11 if (site, kind) == ('tuku', 'popular') else 3)
                    self.assertTrue(all(row['coverUrl'] for row in data['items']))
                    self.assertTrue(all(row['siteId'] == site and row['siteName'] == data['siteName'] for row in data['items']))
                    self.assertIs(type(data['hasMore']), bool)
                    self.assertRegex(data['fetchedAt'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
                    self.assertTrue(data['paginationNote'])
                    self.assertTrue(all('rank' not in row for row in data['items']) if kind == 'latest' else True)

    def test_dm5_periods_use_distinct_tab_lists_and_ignore_duplicate_hover_cards(self):
        weekly = fetch('dm5', period='week')
        total = fetch('dm5', period='total')
        self.assertEqual([row['rank'] for row in weekly['items']], [1, 2, 3])
        self.assertEqual(total['items'][0]['title'], '鬼灭之刃')
        self.assertNotEqual(weekly['items'][0]['detailUrl'], total['items'][0]['detailUrl'])
        self.assertIn('猫子', weekly['items'][0]['author'])
        self.assertEqual(weekly['items'][0]['latestChapter'], '第180话')
        self.assertIn('_180x240_', weekly['items'][0]['coverUrl'])
        self.assertNotIn('_480x369_', weekly['items'][0]['coverUrl'])
        with self.assertRaises(d.DiscoveryError):
            fetch('dm5', source=fixture('dm5-popular').replace('>月<', '>年<'))
        latest = fetch('dm5', 'latest')
        self.assertEqual(latest['label'], '今日更新')
        self.assertEqual(latest['items'][0]['updatedAtText'], '12分钟前更新')
        self.assertIn('今日', latest['paginationNote'])

    def test_mangabz_uses_observed_next_link_and_caps_explicitly_labelled_range(self):
        for kind in ['popular', 'latest']:
            first = fetch('mangabz', kind)
            second = fetch('mangabz', kind, page=2, source=fixture(f'mangabz-{kind}-p2'))
            tenth = fetch('mangabz', kind, page=10, source=fixture(f'mangabz-{kind}-p10'))
            self.assertTrue(first['hasMore'])
            self.assertTrue(second['hasMore'])
            self.assertFalse(tenth['hasMore'])
            self.assertIn('前10页', tenth['paginationNote'])
            self.assertIn('-p2/', second['sourceUrl'])
            self.assertTrue({r['detailUrl'] for r in first['items']}.isdisjoint(r['detailUrl'] for r in second['items']))
            self.assertTrue(all('rank' not in row for row in first['items']))

    def test_mangabz_rejects_wrong_sort_repeated_page_and_untrusted_next(self):
        with self.assertRaises(d.DiscoveryError):
            fetch('mangabz', 'latest', source=fixture('mangabz-classify'))
        with self.assertRaises(d.DiscoveryError):
            fetch('mangabz', page=2, source=fixture('mangabz-classify'))
        source = fixture('mangabz-classify')
        for href in ['https://evil.test/manga-list-p2/', '/manga-list-0-0-2-p2/', '/manga-list-p8/']:
            changed = re.sub(r'href="/manga-list-p2/" data-index="2">\s*&gt;',
                             'href="' + href + '" data-index="2">&gt;', source)
            self.assertNotEqual(changed, source)
            with self.assertRaises(d.DiscoveryError):
                fetch('mangabz', source=changed)
        without_arrow = re.sub(r'<li><a[^>]+>\s*&gt;\s*</a></li>', '', source)
        self.assertNotEqual(without_arrow, source)
        self.assertTrue(fetch('mangabz', source=without_arrow)['hasMore'])

    def test_homepage_scopes_do_not_mix_ranking_recommendations_and_updates(self):
        popular, latest = fetch('baozimh'), fetch('baozimh', 'latest')
        self.assertEqual(popular['items'][0]['rank'], 1)
        self.assertEqual(latest['items'][0]['title'], 'UNLIMITED')
        self.assertTrue({x['detailUrl'] for x in popular['items']}.isdisjoint(x['detailUrl'] for x in latest['items']))
        self.assertIn('首页', latest['label'])
        unrelated = '<aside><a class="rank-item" href="/comic/other">无关推荐</a></aside>'
        actual = fetch('manhuazhijia', source=unrelated + fixture('manhuazhijia-rank'))
        self.assertEqual(len(actual['items']), 3)
        self.assertEqual(actual['items'][0]['title'], '鬼灭之刃')

    def test_tuku_preserves_stale_source_date_and_only_printed_rank_numbers(self):
        popular = fetch('tuku')
        self.assertEqual([row['rank'] for row in popular['items'][:9]], list(range(1, 10)))
        self.assertTrue(all('rank' not in row for row in popular['items'][9:]))
        latest = fetch('tuku', 'latest')
        self.assertEqual(latest['sourceUrl'], 'https://www.tuku.cc/')
        self.assertEqual(latest['items'][0]['updatedAtText'], '2025-12-06 更新')
        self.assertEqual(latest['items'][0]['latestChapter'], '第34卷')
        self.assertEqual(latest['label'], '首页最近更新')

    def test_rumanhua_retains_published_http_origin_and_real_author_without_fake_dates(self):
        popular = fetch('rumanhua')
        latest = fetch('rumanhua', 'latest')
        self.assertEqual(popular['sourceUrl'], 'http://rumanhua2.com/rank/2')
        self.assertTrue(all(x['detailUrl'].startswith('http://rumanhua2.com/') for x in latest['items']))
        self.assertIn('七猫免费小说', latest['items'][0]['author'])
        self.assertTrue(all('updatedAtText' not in x for x in latest['items']))
        self.assertTrue(all(urlsplit(x['coverUrl']).hostname == 'p6.ecombdimg.com' for x in latest['items']))

    def test_comicbox_carries_current_cover_version_and_never_uses_chapter_count_as_latest(self):
        for kind, name in [('popular', 'comicbox-rank'), ('latest', 'comicbox')]:
            data = fetch('comicbox', kind)
            version = d.comicbox.cache_key(fixture(name))
            self.assertTrue(all(parse_qs(urlsplit(x['coverUrl']).query)['v'] == [version] for x in data['items']))
            self.assertTrue(all('/cover_pc.jpg' in x['coverUrl'] for x in data['items']))
            self.assertTrue(all(x['latestChapter'] == '' for x in data['items']))
        source = fixture('comicbox-rank')
        changed = re.sub(r'<script>.*?</script>', '', source, flags=re.S)
        with self.assertRaises(RuntimeError):
            fetch('comicbox', source=changed)

    def test_comicbox_ad_slots_do_not_become_ranked_cards(self):
        source = fixture('comicbox-rank')
        advert = '<div data-ad-slot="test"><a class="sp-rank-card" href="https://evil.test/book/1"><span class="sp-rank-card-title">广告</span></a></div>'
        source = source.replace('<div class="sp-rank-grid">', '<div class="sp-rank-grid">' + advert)
        self.assertEqual(len(fetch('comicbox', source=source)['items']), 3)

    def test_invalid_book_addresses_never_reach_arbitrary_or_chapter_urls(self):
        examples = {
            'dm5': ['/m123/', '/manhua-rank/', '/manhua-new/', '/manhua-name/?p=1'],
            'mangabz': ['/m123/', '/manga-list/', '/1bz/?p=1'],
            'baozimh': ['/user/page_direct?comic_id=one', '/classify', '/comic/one/chapter'],
            'manhuazhijia': ['/chapter/1', '/top', '/comic/one?x=1'],
            'tuku': ['/chapter123/', '/rank/', '/manga-1/#x'],
            'rumanhua': ['/rank/2', '/abcde12/123.html', '/abcde12/?p=1'],
            'comicbox': ['/chapter/1', '/free-chapter/1', '/booklist', '/book/1?x=1'],
        }
        for site, urls in examples.items():
            urls += ['https://evil.test/book/1', d._SITES[site][1] + ':443' + '/comic/1', '/book/../book/1', '\\evil.test\\book\\1']
            for url in urls:
                with self.subTest(site=site, url=url), self.assertRaises(d.DiscoveryError):
                    d._book(site, url)

    def test_missing_titles_duplicate_books_and_rank_gaps_fail_instead_of_partial_success(self):
        source = fixture('manhuazhijia-rank')
        for changed in [source.replace('<h3>鬼灭之刃</h3>', '<h3></h3>'),
                        source.replace('/comic/yiquanchaoren', '/comic/guimiezhiren'),
                        re.sub(r'(rank-num top2">\s*)2', r'\g<1>9', source)]:
            self.assertNotEqual(changed, source)
            with self.assertRaises(d.DiscoveryError):
                fetch('manhuazhijia', source=changed)

    def test_unsafe_cover_hosts_become_placeholder_without_losing_real_book(self):
        source = fixture('mangabz-classify')
        for host in ['cover.mangabz.com.evil.test', 'evil-mangabz.com', 'user@cover.mangabz.com']:
            changed = source.replace('cover.mangabz.com', host)
            data = fetch('mangabz', source=changed)
            self.assertEqual(len(data['items']), 3)
            self.assertTrue(all(row['coverUrl'] == '' for row in data['items']))

    def test_challenges_missing_list_and_download_failure_are_not_empty_success(self):
        for site in FILES:
            with self.subTest(site=site), self.assertRaises(d.DiscoveryError):
                fetch(site, source='<html><title>Just a moment...</title></html>')
            with self.subTest(site=site), self.assertRaises(RuntimeError):
                fetch(site, source='<html><title>来源正常</title><aside>推荐</aside></html>')
        with patch.object(d, 'read_text', side_effect=d.DiscoveryError('连接失败')), self.assertRaisesRegex(d.DiscoveryError, '连接失败'):
            d.fetch('baozimh', 'latest', '', 1)


if __name__ == '__main__':
    unittest.main()
