"""Source declarations, rather than one site's assumptions, control discovery."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from client import discovery as d


class DiscoveryRegistryTests(unittest.TestCase):
    def setUp(self):
        self.capabilities = [
            {'siteId':'extra','siteName':'测试来源','coverLookup':False,'modes':[
                {'kind':'popular','label':'热门','periods':[{'id':'weekly','label':'一周'}],'maxPage':3},
                {'kind':'latest','label':'首页更新','periods':[],'maxPage':1}]},
            {'siteId':'updates','siteName':'更新来源','coverLookup':False,'modes':[
                {'kind':'latest','label':'最近更新','periods':[],'maxPage':2}]}]
        self.adapter = SimpleNamespace(sources=lambda:self.capabilities, fetch=Mock(), IMAGE_DOMAINS=('covers.source.test',))
        self.patch = patch.object(d, '_extensions', return_value=(self.adapter,))
        self.patch.start(); self.addCleanup(self.patch.stop)

    def test_source_capabilities_are_independent_and_existing_sources_keep_their_protocol(self):
        sources=d.sources()
        self.assertEqual([s['siteId'] for s in sources],['manhuagui','manben','extra','updates'])
        self.assertTrue(sources[0]['coverLookup'])
        self.assertFalse(sources[2]['coverLookup'])
        sources[2]['modes'][0]['periods'].clear()
        self.assertEqual(len(d.sources()[2]['modes'][0]['periods']),1)
        self.assertEqual(d.normalize_request({}),('manhuagui','popular','day',1))
        self.assertEqual(d.normalize_request({'siteId':'manben','kind':'latest','page':2}),('manben','latest','',2))

    def test_each_source_controls_its_modes_periods_and_page_limits(self):
        self.assertEqual(d.normalize_request({'siteId':'extra','page':'3'}),('extra','popular','weekly',3))
        self.assertEqual(d.normalize_request({'siteId':'updates','kind':'latest','page':2}),('updates','latest','',2))
        for body in ({'siteId':'extra','page':4},{'siteId':'extra','period':'day'},
                     {'siteId':'extra','kind':'latest','page':2},{'siteId':'updates','kind':'popular'},
                     {'siteId':'extra','url':'https://other.test'},{'siteId':'extra','page':True}):
            with self.subTest(body=body),self.assertRaises(ValueError):d.normalize_request(body)
        self.adapter.fetch.assert_not_called()

    def test_dispatch_checks_identity_and_never_uses_wrong_family(self):
        payload={'siteId':'extra','kind':'popular','period':'weekly','page':2,'items':[],'hasMore':False}
        self.adapter.fetch.return_value=payload
        self.assertEqual(d.fetch('extra','popular','weekly',2),payload)
        self.adapter.fetch.assert_called_once_with('extra','popular','weekly',2)
        self.adapter.fetch.return_value={**payload,'page':1}
        with self.assertRaises(d.DiscoveryError):d.fetch('extra','popular','weekly',2)
        self.adapter.fetch.return_value={**payload,'hasMore':1}
        with self.assertRaises(d.DiscoveryError):d.fetch('extra','popular','weekly',2)
        self.assertEqual(d.image_domains(),('covers.source.test',))

    def test_discovery_extension_cannot_expand_legacy_cover_endpoint_or_duplicate_source(self):
        with self.assertRaises(ValueError):
            d.normalize_cover_request({'siteId':'extra','detailUrl':'https://covers.source.test/book/1'})
        self.capabilities[0]['siteId']='manben'
        with self.assertRaisesRegex(RuntimeError,'重复'):d.sources()
