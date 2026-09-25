import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from client import discovery_apk as d
from client import apk_wap, apk_vomic_html, apk_dmzj, apk_kuaikan
from client.discovery_common import DiscoveryError

FIX = Path(__file__).parent / 'fixtures' / 'discovery-apk'


def html(name):
    return (FIX / (name + '.html')).read_text()


def data(name):
    return json.loads((FIX / (name + '.json')).read_text())


class DiscoveryApkTests(unittest.TestCase):
    def test_capabilities_reflect_verified_modes_and_periods(self):
        sources = {s['siteId']: s for s in d.sources()}
        self.assertEqual(len(sources), 10)
        self.assertTrue(all(s['coverLookup'] is False for s in sources.values()))
        self.assertEqual([m['kind'] for m in sources['kuaikan']['modes']], ['popular'])
        self.assertEqual([p['id'] for p in sources['mkzhan']['modes'][0]['periods']], ['week', 'month', 'total'])
        self.assertEqual([p['id'] for p in sources['manhua6']['modes'][0]['periods']], ['year', 'month', 'week', 'day'])
        self.assertEqual(sources['manhua1234']['modes'][1]['maxPage'], 1)

    def test_invalid_selections_never_fetch(self):
        invalid = [('kuaikan', 'latest', '', 1), ('mkzhan', 'popular', 'day', 1),
                   ('cocoecar', 'popular', '', 1), ('manhua1234', 'latest', '', 2),
                   ('missing', 'latest', '', 1), ('kanman', 'latest', '', True), ('kanman', 'latest', '', 1001)]
        with patch.object(d, 'read_json') as json_read, patch.object(d, 'read_text') as text_read:
            for args in invalid:
                with self.subTest(args=args), self.assertRaises(ValueError):
                    d.fetch(*args)
            json_read.assert_not_called(); text_read.assert_not_called()

    def test_wap_brands_keep_identity_and_do_not_invent_ranks(self):
        for site, product in [('manhuatai', 'mht'), ('shenmanhua', 'smh')]:
            for kind, suffix, order in [('popular', 'popular', 'click'), ('latest', 'update', 'date')]:
                with self.subTest(site=site, kind=kind), patch.object(d, 'read_json', return_value=data(f'wap-{product}-{suffix}')) as get:
                    result = d.fetch(site, kind)
                query = parse_qs(urlsplit(get.call_args.args[0]).query)
                self.assertEqual(query['productname'], [product]); self.assertEqual(query['orderby'], [order])
                self.assertTrue(result['hasMore']); self.assertEqual(len(result['items']), 48)
                for row in result['items']:
                    self.assertTrue(row['coverUrl']); self.assertNotIn('rank', row)
                    self.assertEqual(apk_wap._comic_id(site, row['detailUrl']), parse_qs(urlsplit(row['detailUrl']).query)['comic_id'][0])
                if kind == 'latest':
                    self.assertRegex(result['items'][0]['updatedAtText'], r'^\d{4}-\d\d-\d\d \d\d:\d\d$')

    def test_kanman_lookahead_ignores_source_broken_total_and_shares_deadline(self):
        with patch.object(d, 'read_json', side_effect=[data('wap-kmh-popular'), data('wap-kmh-popular2')]) as get:
            result = d.fetch('kanman', 'popular')
        self.assertTrue(result['hasMore']); self.assertEqual(len(result['items']), 33)
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args_list[0].kwargs['deadline'], get.call_args_list[1].kwargs['deadline'])
        self.assertEqual(parse_qs(urlsplit(get.call_args_list[1].args[0]).query)['page'], ['2'])

    def test_kanman_repeated_next_page_is_error(self):
        first = data('wap-kmh-popular'); following = copy.deepcopy(first)
        following['data']['page']['current_page'] = 2
        with patch.object(d, 'read_json', side_effect=[first, following]), self.assertRaises(DiscoveryError):
            d.fetch('kanman', 'popular')

    def test_wap_wrong_page_or_order_never_masquerades_as_valid(self):
        for key, value in [('current_page', 2), ('orderby', 'click')]:
            payload = data('wap-mht-update'); payload['data']['page'][key] = value
            with patch.object(d, 'read_json', return_value=payload), self.assertRaises(DiscoveryError):
                d.fetch('manhuatai', 'latest')

    def test_real_html_cards_have_covers_and_provider_book_urls(self):
        cases = [('manhua1234', 'popular', '', 'manhua1234-rank'), ('manhua1234', 'latest', '', 'manhua1234-update'),
                 ('cocoecar', 'popular', 'total', 'cocoecar-rank'), ('cocoecar', 'latest', '', 'cocoecar-update'),
                 ('guazimanhua', 'popular', 'total', 'guazimanhua-rank'), ('guazimanhua', 'latest', '', 'guazimanhua-update-all'),
                 ('manhua6', 'popular', 'year', 'manhua6-popular'), ('manhua6', 'latest', '', 'manhua6-update')]
        for site, kind, period, fixture in cases:
            with self.subTest(site=site, kind=kind), patch.object(d, 'read_text', return_value=html(fixture)):
                result = d.fetch(site, kind, period)
            self.assertGreaterEqual(len(result['items']), 2)
            for row in result['items']:
                self.assertTrue(row['coverUrl']); self.assertEqual(apk_vomic_html._book_url(site, row['detailUrl']), row['detailUrl'])
                self.assertEqual('rank' in row, kind == 'popular' and site != 'manhua1234')

    def test_guazi_uses_whole_mobile_list_not_pc_ten_item_preview(self):
        with patch.object(d, 'read_text', return_value=html('guazimanhua-rank')):
            result = d.fetch('guazimanhua', 'popular', 'total')
        self.assertEqual([row['rank'] for row in result['items']], [1, 2])
        self.assertEqual(result['items'][0]['title'], '从姑获鸟开始')

    def test_guazi_all_updates_follow_source_next_page_despite_omitted_date(self):
        with patch.object(d, 'read_text', return_value=html('guazimanhua-update-all')) as get:
            result = d.fetch('guazimanhua', 'latest')
        self.assertTrue(result['hasMore'])
        self.assertEqual(parse_qs(urlsplit(get.call_args.args[0]).query), {'date': ['all'], 'page': ['1']})

    def test_mkzhan_periods_select_one_published_group(self):
        values = []
        for period in ('week', 'month', 'total'):
            with patch.object(d, 'read_text', return_value=html('mkzhan-popular')):
                result = d.fetch('mkzhan', 'popular', period)
            self.assertEqual(len(result['items']), 2)
            self.assertEqual([row['rank'] for row in result['items']], [1, 2])
            values.append(result['items'][0]['title'])
        self.assertEqual(values[0], '妖神记')

    def test_seven_day_updates_keep_newest_occurrence_and_never_number_as_rank(self):
        with patch.object(d, 'read_text', return_value=html('mkzhan-update')):
            result = d.fetch('mkzhan', 'latest')
        rows = result['items']; self.assertEqual(rows[0]['latestChapter'], '第30话')
        self.assertEqual(len({row['detailUrl'] for row in rows}), len(rows))
        self.assertFalse(any('rank' in row for row in rows))
        self.assertIn('七日', result['paginationNote'])

    def test_six_manga_updates_paginate_locally_in_order(self):
        rows = [{'title': str(i), 'detailUrl': f'https://www.hzxidou.com/comic/{i}'} for i in range(1, 70)]
        with patch.object(d, '_html', return_value=None), patch.object(d, '_mkzhan_cards', return_value=rows):
            first = d.fetch('manhua6', 'latest', '', 1); second = d.fetch('manhua6', 'latest', '', 2)
        self.assertEqual(len(first['items']), 48); self.assertEqual(len(second['items']), 21)
        self.assertTrue(first['hasMore']); self.assertFalse(second['hasMore'])
        self.assertEqual(second['items'][0]['title'], '49')

    def test_kuaikan_literal_state_rank_and_identity(self):
        with patch.object(d, 'read_text', return_value=html('kuaikan-popular')):
            result = d.fetch('kuaikan', 'popular')
        self.assertEqual([r['rank'] for r in result['items']], [1, 2, 3])
        self.assertEqual(result['items'][0]['title'], '错撩')
        self.assertTrue(all(r['coverUrl'] and apk_kuaikan._id(r['detailUrl']) for r in result['items']))
        wrong = html('kuaikan-popular').replace('"rank_id": 9', '"rank_id": 2')
        with patch.object(d, 'read_text', return_value=wrong), self.assertRaises(DiscoveryError):
            d.fetch('kuaikan', 'popular')

    def test_zaimanhua_updates_use_one_based_pages(self):
        ids = []
        for page, name in [(1, 'zaimanhua-update2'), (2, 'zaimanhua-update3')]:
            with patch.object(d, 'read_json', return_value=data(name)) as get:
                result = d.fetch('zaimanhua', 'latest', '', page)
            self.assertTrue(get.call_args.args[0].endswith('/0/' + str(page)))
            self.assertEqual(len(result['items']), 20); self.assertTrue(result['hasMore'])
            ids.append({apk_dmzj._url_ids('zaimanhua', r['detailUrl'])[0] for r in result['items']})
        self.assertFalse(ids[0] & ids[1])

    def test_zaimanhua_real_period_mapping_and_rank_offset(self):
        for period, number, fixture in [('week', 1, 'zaimanhua-web-rank'), ('month', 2, 'zaimanhua-web-month'), ('total', 3, 'zaimanhua-web-total3')]:
            with patch.object(d, 'read_json', return_value=data(fixture)) as get:
                result = d.fetch('zaimanhua', 'popular', period)
            self.assertEqual(parse_qs(urlsplit(get.call_args.args[0]).query)['duration'], [str(number)])
            self.assertEqual(result['items'][0]['rank'], 1); self.assertTrue(result['hasMore'])
        with patch.object(d, 'read_json', return_value=data('zaimanhua-web-rank2')):
            result = d.fetch('zaimanhua', 'popular', 'week', 2)
        self.assertEqual(result['items'][0]['rank'], 21)

    def test_empty_error_challenge_and_missing_html_do_not_become_empty_success(self):
        for source in ('<title>Just a moment</title>', '<div>unrelated navigation</div>'):
            with patch.object(d, 'read_text', return_value=source), self.assertRaises(DiscoveryError):
                d.fetch('manhua1234', 'latest')
        with patch.object(d, 'read_json', return_value={'errno': 1, 'data': []}), self.assertRaises(DiscoveryError):
            d.fetch('zaimanhua', 'latest')

    def test_duplicate_or_foreign_book_and_unsafe_cover(self):
        fixture = html('manhua1234-rank')
        hostile = fixture.replace('/comic/13704.html', 'https://evil.test/comic/13704.html')
        with patch.object(d, 'read_text', return_value=hostile), self.assertRaises(DiscoveryError):
            d.fetch('manhua1234', 'popular')
        unsafe_cover = fixture.replace('https://wmh1234.wszwhg.net/', 'https://evil.test/')
        with patch.object(d, 'read_text', return_value=unsafe_cover):
            result = d.fetch('manhua1234', 'popular')
        self.assertTrue(all(not row['coverUrl'] for row in result['items']))
        duplicate = fixture.replace('/comic/11742.html', '/comic/13704.html')
        with patch.object(d, 'read_text', return_value=duplicate), self.assertRaises(DiscoveryError):
            d.fetch('manhua1234', 'popular')


if __name__ == '__main__':
    unittest.main()
