"""Inventory is informative; it can never activate a source or expose its code."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from client import source_catalog as catalog
from client import providers


class SourceCatalogTests(unittest.TestCase):
    def test_all_99_imported_rules_retained_and_no_executable_rule_fields(self):
        data = catalog.catalog(providers.sites())
        entries = data['entries']
        rule_ids = {row['entryId'] for row in entries if row['entryId'].startswith(('vomic:', 'octopus:'))}
        self.assertEqual(rule_ids, {f'vomic:{i}' for i in range(95)} | {f'octopus:{i}' for i in range(4)})
        plugin_ids = {row['entryId'] for row in entries if row['entryId'].startswith('tachiyomi:')}
        self.assertEqual(plugin_ids, {f'tachiyomi:{i}' for i in range(29)})
        self.assertEqual(data['ruleEntryCount'], 99)
        self.assertEqual(data['activeSourceCount'], len(providers.SOURCES))
        self.assertEqual(sum(data['counts'].values()), len(entries))
        for row in entries:
            self.assertFalse({'searchRule', 'detailRule', 'readRule', 'headers', 'probes', 'features'} & row.keys())
            self.assertEqual(row['searchEnabled'], row['siteId'] in providers.SOURCES)
        self.assertFalse(data['liveAvailabilityChecked'])

    def test_discovery_capabilities_describe_active_sources_without_enabling_others(self):
        sources = [{'siteId': 'manben', 'siteName': '漫本'}, {'siteId': 'another', 'siteName': '另一搜索源'}]
        capabilities = [{'siteId': 'manben', 'modes': [{'kind': 'popular'}, {'kind': 'latest'}]},
                        {'siteId': 'not-registered', 'modes': [{'kind': 'popular'}]}]
        result = catalog.catalog(sources, discovery_sources=capabilities)
        self.assertEqual(result['activeSourceCount'], 2)
        self.assertEqual(result['activeSources'][0]['discoveryModes'], ['popular', 'latest'])
        self.assertEqual(result['activeSources'][1]['discoveryModes'], [])
        self.assertNotIn('discoveryModes', sources[0])
        compatibility = catalog.catalog(sources, mode='mangayun', discovery_sources=capabilities)
        self.assertNotIn('discoveryModes', compatibility['activeSources'][0])

    def test_inventory_cannot_enable_unregistered_source_or_claim_live_coverage(self):
        data = catalog.catalog([], mode='mangayun')
        self.assertEqual(data['activeSourceCount'], 0)
        self.assertEqual(data['counts']['integrated'], 0)
        self.assertTrue(all(not row['searchEnabled'] for row in data['entries']))
        self.assertEqual(data['activeSources'], [])

    def test_dumanwu_imported_rule_maps_to_its_executable_source(self):
        from client.discovery import sources
        data = catalog.catalog(providers.sites(), discovery_sources=sources())
        entry = next(row for row in data['entries'] if row['entryId'] == 'vomic:16')
        self.assertEqual(entry['siteId'], 'dumanwu')
        self.assertEqual(entry['status'], 'integrated')
        self.assertTrue(entry['searchEnabled'])
        self.assertEqual(entry['mappedName'], '读漫屋')
        active = next(row for row in data['activeSources'] if row['siteId'] == 'dumanwu')
        self.assertEqual(active['discoveryModes'], ['popular', 'latest'])
        disabled = catalog.catalog([], mode='mangayun')
        entry = next(row for row in disabled['entries'] if row['entryId'] == 'vomic:16')
        self.assertEqual(entry['status'], 'pending')
        self.assertFalse(entry['searchEnabled'])

    def test_duplicate_unimplemented_entry_stays_a_duplicate_without_search_access(self):
        snapshot = {'updatedAt': '2026-09-21', 'ruleEntryCount': 1, 'entries': [
            {'entryId': 'fixture:1', 'name': '同一来源备用规则', 'origin': 'https://source.example',
             'status': 'duplicate', 'reason': '重复资料；目标尚未接入', 'siteId': '',
             'searchRule': {'process': 'do not expose or execute this'}}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'catalog.json'; path.write_text(json.dumps(snapshot))
            with patch.object(catalog, 'CATALOG_PATH', path):
                result = catalog.catalog([])
        row = result['entries'][0]
        self.assertEqual(row['status'], 'duplicate')
        self.assertFalse(row['searchEnabled'])
        self.assertNotIn('searchRule', row)


if __name__ == '__main__':
    unittest.main()
