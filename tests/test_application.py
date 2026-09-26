import base64
from concurrent.futures import ThreadPoolExecutor
import json
import http.client
from email.message import Message
from io import BytesIO
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from urllib.parse import urlencode
from http.server import ThreadingHTTPServer
from PIL import Image

from client import providers as p
from server import Application, Cache, Handler, ImageRedirect, validate_image, image_referer
from client.source_coverage import compare_sources, REFERENCE_SOURCES


class Adapters(unittest.TestCase):
    def test_chapter_retry_can_refresh_expired_image_urls(self):
        app = Application()
        body = {'siteId': 'mangabz', 'chapterUrl': 'https://www.mangabz.com/m11380/'}
        with patch.object(p, 'images', side_effect=[['https://image.mangabz.com/old.png'], ['https://image.mangabz.com/fresh.png']]) as images:
            self.assertEqual(app.post('/api/chapter-images', body)['images'], ['https://image.mangabz.com/old.png'])
            self.assertEqual(app.post('/api/chapter-images', body)['images'], ['https://image.mangabz.com/old.png'])
            self.assertEqual(app.post('/api/chapter-images', {**body, 'refresh': True})['images'], ['https://image.mangabz.com/fresh.png'])
            self.assertEqual(images.call_count, 2)
            with self.assertRaises(ValueError):
                app.post('/api/chapter-images', {**body, 'refresh': 'yes'})

    def test_original_source_coverage_is_not_total_source_count(self):
        coverage = compare_sources(p.sites())
        self.assertEqual(coverage['coveredCount'], 12)
        self.assertEqual(coverage['missing'], [])
        self.assertEqual(set(coverage['additional']), set(p.APK_PROVIDERS))
        without_comicbox = [s for s in p.sites() if s['siteId'] != 'comicbox']
        previous = compare_sources(without_comicbox)
        self.assertGreater(previous['totalCount'], len(REFERENCE_SOURCES))
        self.assertEqual(previous['missing'], ['comicbox'])

    def test_comicbox_is_searchable_in_native_and_compatibility_modes(self):
        raw = {'title': '富家女姐姐', 'url': 'https://www.comicbox.xyz/book/3872'}
        app = Application()
        with patch.object(p.comicbox, 'search', return_value=[raw]):
            native = app.post('/api/search', {'siteId': 'comicbox', 'keyword': raw['title']})
        self.assertEqual(native[0]['results'][0]['detailUrl'], raw['url'])
        self.assertEqual(native[0]['results'][0]['title'], raw['title'])
        compat = Application('mangayun')
        with patch.object(compat.upstream, 'sites', return_value=[{'siteId': 'comicbox', 'siteName': '歪歪漫画'}]), patch.object(compat.upstream, 'search', return_value=native):
            self.assertEqual(compat.sites()[0]['siteId'], 'comicbox')
            result = compat.post('/api/search', {'keyword': raw['title']})
            self.assertEqual(result[0]['results'][0]['title'], raw['title'])

    def test_comicbox_detail_and_chapter_routing_validate_hosts(self):
        with patch.object(p.comicbox, 'details', return_value={'chapters': []}) as detail, patch.object(p.comicbox, 'images', return_value=['image']) as images:
            p.details('comicbox', 'https://www.comicbox.xyz/book/3872')
            self.assertEqual(p.images('comicbox', 'https://www.comicbox.xyz/free-chapter/14961'), ['image'])
            for run in (p.details, p.images):
                with self.assertRaises(ValueError):
                    run('comicbox', 'https://www.comicbox.xyz.evil.test/book/3872')
            self.assertEqual(detail.call_count, 1)
            self.assertEqual(images.call_count, 1)

    def test_hip_cover_uses_cover_cdn(self):
        with patch.object(p.n, 'hipmh_search', return_value=[{'id':'bTo0Mjk2-demo','title':'测试','vertical_image_url':'/kk/vertical/demo.webp'}]):
            row = p.search('hipmh', '测试')[0]
            self.assertEqual(row['coverUrl'], 'https://cover.s3imgs.top/kk/vertical/demo.webp')

    def test_copy_search_limit_respects_upstream(self):
        with patch.object(p.n, 'copy_search', return_value={'results': {'list':[]}}) as get:
            p.search('mangacopy', '测试')
            self.assertLessEqual(get.call_args.kwargs['limit'], 30)

    def test_business_error_is_not_empty_results(self):
        with patch.object(p.n, '_urlopen') as get:
            get.return_value.__enter__.return_value.read.return_value = b'{"code":210,"message":"invalid limit"}'
            with self.assertRaisesRegex(p.n.SourceBusinessError, 'invalid limit') as caught:
                p.n._json('https://api.mangacopy.com/example')
            self.assertEqual(caught.exception.code, '210')

    def test_upstream_hip_opaque_chapter_token(self):
        app = Application('mangayun')
        with patch.object(app.upstream, 'sites', return_value=[{'siteId': 'hipmh', 'siteName': '嬉皮漫画'}]), patch.object(app.upstream, 'chapter_images', return_value=['https://hip-tx-1.s3imgs.top/i/page.webp']) as get:
            data = app.post('/api/chapter-images', {'siteId':'hipmh','chapterUrl':'bTo0Mjk2LWM6MTA2MTI1-NDI5NjoxNzkuMDA'})
            self.assertEqual(len(data['images']),1)
            self.assertEqual(get.call_args.args[0], 'hipmh')

    def test_dm5_full_directory_deduplicates(self):
        html = '<div id="chapterlistload"><a href="/m10/">第2话</a><a href="/m20/">第1话</a><a href="/m10/">第2话</a></div><aside><a href="/m30/">other book</a></aside>'.encode()
        with patch.object(p.dm.Session,'get',return_value=(html,'https://www.dm5.com/manhua-demo/',200)):
            rows=p.details('dm5','https://www.dm5.com/manhua-demo/')['chapters']
            self.assertEqual([r['name'] for r in rows],['第1话','第2话'])
            self.assertEqual([r['url'] for r in rows], ['https://www.dm5.com/m20/', 'https://www.dm5.com/m10/'])

    def test_hip_pagination_and_reader_token(self):
        def row(cid, number):
            left = base64.b64encode(f'm:4296-c:{cid}'.encode()).decode().rstrip('=')
            right = base64.b64encode(f'4296:{number}.00'.encode()).decode().rstrip('=')
            return {'hid': left + '-' + right, 'title': f'第{number}卷'}
        with patch.object(p.n, 'hipmh_chapters', side_effect=[{'data': {'items': [row(11,1)], 'total': 2}}, {'data': {'items': [row(12,2)], 'total': 2}}]) as get:
            chapters = p.hip_chapters('bTo0Mjk2')
            self.assertEqual(len(chapters), 2)
            self.assertEqual([c.args[1] for c in get.call_args_list], [1,2])
            self.assertIn(base64.b64encode(b'c:11').decode().rstrip('='), chapters[0]['url'])

    def test_hip_original_mobile_urls_resolve_the_same_work(self):
        urls = ['https://reader.hipmh.top/manga/bTo0Mjk2',
                'https://m.hipmh.com/works/bTo0Mjk2-yi-quan-chao-ren-4288#mid=bTo0Mjk2',
                'https://m.hipmh.com/works/bTo0Mjk2-yi-quan-chao-ren-4288',
                'https://m.hipmh.com/works/a-title?mid=bTo0Mjk2']
        for url in urls:
            with self.subTest(url=url), patch.object(p, 'hip_chapters', return_value=[]) as chapters, \
                    patch.object(p.hipmh_metadata, 'metadata', return_value={'title': '一拳超人'}) as meta:
                self.assertEqual(p.details('hipmh', url)['title'], '一拳超人')
                chapters.assert_called_once_with('bTo0Mjk2')
                meta.assert_called_once_with('bTo0Mjk2')
        for url in ['https://reader.hipmh.top/manga/not-a-mid',
                    'https://m.hipmh.com/works/slug#mid=bTo0Mjk2&mid=bTox',
                    'https://reader.hipmh.top/manga/Yzo0Mjk2']:
            with self.subTest(url=url), patch.object(p, 'hip_chapters') as chapters, self.assertRaises(ValueError):
                p.details('hipmh', url)
                chapters.assert_not_called()

    def test_hip_metadata_failure_preserves_directory_with_visible_reason(self):
        with patch.object(p, 'hip_chapters', return_value=[{'name': '第1卷', 'url': 'https://reader.hipmh.top/chapter/example'}]), \
                patch.object(p.hipmh_metadata, 'metadata', side_effect=p.hipmh_metadata.MetadataError('wrong work')):
            detail = p.details('hipmh', 'https://reader.hipmh.top/manga/bTo0Mjk2')
        self.assertEqual(detail['title'], '')
        self.assertEqual(len(detail['chapters']), 1)
        self.assertIn('资料暂时无法获取', detail['unavailableReason'])

    def test_repeated_page_is_not_success(self):
        data = {'data': {'items': [{'hid': 'bTowLWM6MQ-MTox', 'title': '1'}], 'total': 3}}
        with patch.object(p.n, 'hipmh_chapters', return_value=data):
            with self.assertRaisesRegex(RuntimeError, '分页'):
                p.hip_chapters('1')

    def test_copy_all_groups_and_pages(self):
        with patch.object(p.n,'copy_comic', return_value={'results': {'groups': {'default':{},'extra':{}}, 'comic': {'name':'测试'}}}), patch.object(p.n,'_json',side_effect=[
            {'results': {'list':[{'uuid':'a','name':'1'}],'total':2}},
            {'results': {'list':[{'uuid':'b','name':'2'}],'total':2}},
            {'results': {'list':[{'uuid':'c','name':'番外'}],'total':1}},
        ]) as get:
            rows, meta = p.copy_chapters_all('demo')
            self.assertEqual(len(rows),3)
            self.assertEqual(meta['name'],'测试')
            self.assertIn('offset=1',get.call_args_list[1].args[0])
            self.assertIn('/extra/',get.call_args_list[2].args[0])

    def test_baozi_query_routing(self):
        with patch.object(p.n,'baozimh_chapter_images',return_value=['image']) as get:
            self.assertEqual(p.images('baozimh','https://www.baozimh.com/user/page_direct?comic_id=test&section_slot=2&chapter_slot=3'),['image'])
            get.assert_called_once_with('test',2,3)

    def test_copy_app_restriction_uses_public_website_without_hiding_empty_directory(self):
        url = 'https://www.mangacopy.com/comic/sydsz'
        restricted = {'title': '三月的獅子', 'chapters': [], 'unavailableReason': '官网未返回目录'}
        with patch.object(p, 'copy_chapters_all', side_effect=p.n.SourceBusinessError(210, 'restricted')), \
                patch.object(p.mangacopy_web, 'details', return_value=restricted) as web:
            result = p.details('mangacopy', url)
        web.assert_called_once_with(url)
        self.assertEqual(result['chapters'], [])
        self.assertEqual(result['unavailableReason'], '官网未返回目录')
        chapter = url + '/chapter/57a89552-b391-11ea-945e-00163e0ca5bd'
        with patch.object(p.n, 'copy_chapter_images', side_effect=p.n.SourceBusinessError('210', 'restricted')), \
                patch.object(p.mangacopy_web, 'images', side_effect=RuntimeError('官网未返回本章图片')) as web:
            with self.assertRaisesRegex(RuntimeError, '未返回本章图片'):
                p.images('mangacopy', chapter)
        web.assert_called_once_with(chapter)

    def test_copy_does_not_mask_transport_pagination_or_other_business_errors(self):
        for error in (TimeoutError('timeout'), RuntimeError('分页不完整'), p.n.SourceBusinessError(403, 'forbidden')):
            with self.subTest(error=error), patch.object(p, 'copy_chapters_all', side_effect=error), \
                    patch.object(p.mangacopy_web, 'details') as web:
                with self.assertRaises(type(error)):
                    p.details('mangacopy', 'https://www.mangacopy.com/comic/sydsz')
                web.assert_not_called()
            with patch.object(p.n, 'copy_chapter_images', side_effect=error), \
                    patch.object(p.mangacopy_web, 'images') as web:
                with self.assertRaises(type(error)):
                    p.images('mangacopy', 'https://www.mangacopy.com/comic/sydsz/chapter/57a89552-b391-11ea-945e-00163e0ca5bd')
                web.assert_not_called()

    def test_manhuazhijia_reading_does_not_jump_into_duplicate_volume(self):
        # Observed 三月的狮子 directory: source IDs decrease and volumes/extras
        # interrupt the single-chapter sequence. Keep every URL, fix reading order.
        rows = [{'name': name, 'url': f'https://www.manhuazhijia.cc/chapter/{cid}'}
                for cid, name in [(10321039, '第1话'), (10321038, '第1卷'),
                                  (10321037, '第2话'), (10321036, '第2卷'),
                                  (10321035, '3月的狮子 番外篇'), (10321034, '第3话')]]
        with patch.object(p.n, '_page', return_value='<h1>三月的狮子</h1>'), \
                patch.object(p.n, 'manhuazhijia_chapters', return_value=rows):
            result = p.details('manhuazhijia', 'https://www.manhuazhijia.cc/comic/sanyuedeshizi')['chapters']
        self.assertEqual([row['name'] for row in result],
                         ['第1话', '第2话', '第3话', '第1卷', '第2卷', '3月的狮子 番外篇'])
        self.assertEqual({row['url'] for row in result}, {row['url'] for row in rows})
        self.assertEqual([row['order'] for row in result], list(range(6)))

    def test_native_mode_never_calls_aggregator(self):
        app=Application()
        with patch.object(p,'search',return_value=[{'title':'测试'}]), patch.object(app.upstream,'search',side_effect=AssertionError('unexpected upstream')):
            data=app.post('/api/search',{'siteId':'hipmh','keyword':'测试'})
            self.assertEqual(data[0]['results'][0]['title'],'测试')

    def test_source_failure_isolated(self):
        def search(site, keyword):
            if site=='hipmh': raise RuntimeError('timeout')
            return [{'title':'测试'}]
        with patch.object(p,'search',side_effect=search):
            groups=Application().post('/api/search',{'keyword':'测试'})
            self.assertEqual(len(groups),len(p.SOURCES))
            self.assertEqual(sum('error' in g for g in groups),1)

    def test_compatibility_mode_does_not_search_native_only_apk_sources(self):
        app = Application('mangayun')
        with patch.object(app.upstream, 'sites', return_value=[{'siteId': 'hipmh', 'siteName': '嬉皮漫画'}]), patch.object(app.upstream, 'search', return_value=[]):
            groups = app.post('/api/search', {'keyword': '测试'})
            self.assertEqual([g['siteId'] for g in groups], ['hipmh'])
            with self.assertRaisesRegex(ValueError, '未接入'):
                app.post('/api/search', {'keyword': '测试', 'siteId': 'kanman'})

    def test_compatibility_source_label_stays_consistent_after_search(self):
        for group in ({'siteId': 'hipmh', 'results': []}, {'siteId': 'hipmh', 'error': 'timeout'}):
            app = Application('mangayun')
            with self.subTest(group=group), patch.object(app.upstream, 'sites', return_value=[{'siteId': 'hipmh', 'siteName': '云漫极速'}]), patch.object(app.upstream, 'search', return_value=[group]):
                result = app.post('/api/search', {'keyword': '测试'})
                self.assertEqual(result[0]['siteName'], app.sites()[0]['siteName'])

    def test_compatibility_mode_rejects_native_only_detail_and_chapter(self):
        app = Application('mangayun')
        with patch.object(app.upstream, 'sites', return_value=[{'siteId': 'hipmh', 'siteName': '嬉皮漫画'}]), patch.object(app.upstream, 'details') as details, patch.object(app.upstream, 'chapter_images') as images:
            for path, field, url in [('/api/details', 'detailUrl', 'https://www.kuaikanmanhua.com/web/topic/2625/'), ('/api/chapter-images', 'chapterUrl', 'https://www.kuaikanmanhua.com/web/comic/132643')]:
                with self.subTest(path=path), self.assertRaisesRegex(ValueError, '当前模式未接入'):
                    app.post(path, {'siteId': 'kuaikan', field: url})
            details.assert_not_called()
            images.assert_not_called()

    def test_apk_host_aliases_and_image_allowlist_remain_scoped(self):
        for site, url in [('kanman', 'https://www.kanman.com/27417/'), ('mkzhan', 'https://comic.mkzcdn.com/chapter/content/v1/?comic_id=1'), ('kuaikan', 'https://m.kuaikanmanhua.com/mobile/2625/list/'), ('zaimanhua', 'https://m.zaimanhua.com/pages/comic/detail?id=86003')]:
            p.validate_url(site, url)
        for site, url in [('kanman', 'https://m.kanman.com.evil.test/1/'), ('zaimanhua', 'https://m.kanman.com/1/'), ('mkzhan', 'https://evil.mkzcdn.com/1/')]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                p.validate_url(site, url)
        for domain in p.EXTRA_IMAGE_DOMAINS:
            validate_image('https://' + domain + '/page.jpg')
            with self.assertRaises(ValueError):
                validate_image('https://' + domain + '.evil.test/page.jpg')

    def test_validation(self):
        for url in ['http://localhost/comic/1','https://www.baozimh.com.evil.test/a','file:///tmp/x','https://www.baozimh.com:8765/a','https://x@www.baozimh.com/a']:
            with self.subTest(url=url), self.assertRaises(ValueError): p.validate_url('baozimh',url)
        p.validate_url('baozimh','https://www.baozimh.com/comic/test')
        for url in ['http://127.0.0.1/a','file:///etc/passwd','https://bzcdn.net.evil.test/a','https://s1.bzcdn.net:999/a']:
            with self.subTest(url=url), self.assertRaises(ValueError): validate_image(url)
        validate_image('https://s1.bzcdn.net/a.jpg')
        validate_image('https://cf.mhgui.com/cpic/b/4779.jpg')
        for url in ['https://mhgui.com.evil.test/cover.jpg', 'https://evil-mhgui.com/cover.jpg', 'https://user@cf.mhgui.com/cover.jpg', 'https://cf.mhgui.com:8080/cover.jpg']:
            with self.subTest(url=url), self.assertRaises(ValueError): validate_image(url)

    def test_image_redirect_rechecked(self):
        for url in ['http://127.0.0.1/private', 'https://mhgui.com.evil.test/cover.jpg']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                ImageRedirect().redirect_request(urllib.request.Request('https://cf.mhgui.com/a'),None,302,'',{},url)
        req = ImageRedirect().redirect_request(urllib.request.Request('https://cf.mhgui.com/a'),None,302,'',{},'https://cf.mhgui.com/b')
        self.assertEqual(req.full_url, 'https://cf.mhgui.com/b')

    def test_dm5_and_manben_image_referer_uses_signed_chapter_id(self):
        self.assertEqual(image_referer('dm5', 'https://cdn.cdndm5.com/other/1.jpg?cid=463652&key=abc'), 'https://www.dm5.com/m463652/')
        self.assertEqual(image_referer('manben', 'https://cdn.cdndm5.com/709607/1.jpg?cid=855422&key=abc'), 'https://www.manben.com/m855422/')
        self.assertEqual(image_referer('manben', 'https://cdn.cdndm5.com/cover.jpg'), 'https://www.manben.com/')
        for query in ('cid=1&cid=2', 'cid=', 'cid=../2', 'cid=https://evil.test'):
            with self.subTest(query=query), self.assertRaises(ValueError):
                image_referer('dm5', 'https://cdn.cdndm5.com/1.jpg?' + query)

    def test_empty_images_are_actionable_error(self):
        with patch.object(p,'images',return_value=[]):
            with self.assertRaisesRegex(RuntimeError,'切换'):
                Application().post('/api/chapter-images',{'siteId':'manhuazhijia','chapterUrl':'https://www.manhuazhijia.cc/chapter/1'})


class CacheTests(unittest.TestCase):
    def test_coalesce_and_expire(self):
        c=Cache(); calls=[]
        def load(): calls.append(1); time.sleep(.02); return len(calls)
        with ThreadPoolExecutor(max_workers=4) as pool:
            values=list(pool.map(lambda _:c.get('key',.05,load),range(4)))
        self.assertEqual(values,[1]*4)
        time.sleep(.06)
        self.assertEqual(c.get('key',1,load),2)

    def test_errors_not_cached_and_size_bounded(self):
        c=Cache(2)
        with self.assertRaises(ValueError): c.get('bad',1,lambda: int('bad'))
        for i in range(3): c.get(i,60,lambda:i)
        self.assertEqual(len(c.data),2)
        self.assertNotIn('bad',c.data)


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.server.app=Application()
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.base='http://127.0.0.1:'+str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join()

    def test_deep_link_and_not_found(self):
        for path in ('/read/test/chapter', '/discover', '/discover/popular', '/discover/latest'):
            with self.subTest(path=path), urllib.request.urlopen(self.base+path) as r:
                self.assertEqual(r.status,200)
                self.assertIn(b'reader-dialog',r.read())
        with self.assertRaises(urllib.error.HTTPError) as exc: urllib.request.urlopen(self.base+'/api/unknown')
        self.assertEqual(exc.exception.code,404)
        with self.assertRaises(urllib.error.HTTPError) as exc: urllib.request.urlopen(self.base+'/discover/unknown')
        self.assertEqual(exc.exception.code,404)

    def test_config_and_local_shelf_truthfulness(self):
        with urllib.request.urlopen(self.base+'/api/config') as r:
            data=json.load(r)['data'];self.assertFalse(data['sync']);self.assertEqual(data['mode'],'native')
        self.assertEqual(data['sourceCoverage']['missing'], [])
        self.assertEqual(data['sourceCoverage']['coveredCount'], 12)
        self.assertEqual(data['sourceCoverage']['totalCount'], len(p.SOURCES))
        self.assertEqual(data['sourceCoverage']['scope'], 'registered-source-ids')
        self.assertFalse(data['sourceCoverage']['liveAvailabilityChecked'])
        self.assertNotIn('comicbox', [s['siteId'] for s in data['disabledSources']])

    def test_source_catalog_has_all_rules_but_search_uses_only_registered_adapters(self):
        with urllib.request.urlopen(self.base + '/api/source-catalog') as response:
            data = json.load(response)['data']
        self.assertEqual(data['ruleEntryCount'], 99)
        self.assertEqual(data['activeSourceCount'], len(p.SOURCES))
        self.assertGreaterEqual(len(data['entries']), 99)
        self.assertEqual({s['siteId'] for s in data['activeSources']}, set(p.SOURCES))
        self.assertTrue(any(row['status'] in {'pending', 'blocked'} and not row['searchEnabled'] for row in data['entries']))

    def test_access_log_does_not_print_image_ticket_query(self):
        with patch.object(Handler, 'log_message') as log:
            with urllib.request.urlopen(self.base + '/api/config?ticket=private-fixture') as response:
                self.assertEqual(response.status, 200)
            log.assert_called_once()
            self.assertNotIn('private-fixture', repr(log.call_args))
            self.assertIn('/api/config', repr(log.call_args))

    def test_comicbox_proxy_dispatch_and_failed_decoding(self):
        logical = 'https://bmigmi-global-wuwu.ccavbox.com/break_2/static/upload/book/1/2/3.jpg?v=2026021808'
        # The CDN determines transport; omitting siteId must not send the
        # encrypted logical .jpg to the ordinary JPEG proxy.
        path = '/api/image?' + urlencode({'url': logical})
        with patch('server.comicbox_images.fetch_image', return_value=(b'synthetic-jpeg', 'image/jpeg')) as get:
            with urllib.request.urlopen(self.base + path) as response:
                self.assertEqual(response.headers.get_content_type(), 'image/jpeg')
                self.assertEqual(response.read(), b'synthetic-jpeg')
            get.assert_called_once_with(logical)
        with patch('server.comicbox_images.fetch_image', side_effect=RuntimeError('图片分片缺失')):
            with urllib.request.urlopen(self.base + path) as response:
                self.assertEqual(response.read(), b'synthetic-jpeg')
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(self.base + path + '&retry=1')
            self.assertEqual(error.exception.code, 502)
            self.assertIn('分片缺失', json.load(error.exception)['error'])
        with patch('server.comicbox_images.fetch_image') as get:
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(self.base + '/api/image?' + urlencode({'url': logical.replace('ccavbox.com', 'ccavbox.com.evil.test')}))
            self.assertEqual(error.exception.code, 400)
            get.assert_not_called()

    def test_komiic_metadata_then_lazy_ticket_image_through_http(self):
        kid = 'http-audit-synthetic-kid'
        logical = 'https://komiic.com/api/image/' + kid
        queries = []
        def query(operation, variables):
            queries.append((operation, variables))
            if operation == p.komiic.IMAGES_QUERY:
                return {'imagesByChapterId': [{'id': '1', 'kid': kid, 'width': 2, 'height': 3}]}
            self.assertEqual(operation, p.komiic.TICKET_QUERY)
            return {'getImageTickets': [{'kid': kid, 'url': 'https://img.komiic.com/synthetic.jpg', 'ticket': 'test-ticket', 'expiresAt': '2099-01-01T00:00:00Z'}]}
        output = BytesIO()
        with Image.new('RGB', (2, 3), 'blue') as image:
            image.save(output, 'JPEG')
        headers = Message(); headers['Content-Type'] = 'image/jpeg'
        with patch.object(p.komiic, '_query', side_effect=query), patch.object(p.komiic, 'build_opener') as build:
            response = build.return_value.open.return_value.__enter__.return_value
            response.headers = headers; response.read.return_value = output.getvalue()
            body = json.dumps({'siteId': 'komiic', 'chapterUrl': 'https://komiic.com/comic/1750/chapter/999777'}).encode()
            with urllib.request.urlopen(urllib.request.Request(self.base + '/api/chapter-images', data=body, headers={'Content-Type':'application/json'})) as response:
                self.assertEqual(json.load(response)['data']['images'], [logical])
            self.assertEqual(len(queries), 1)
            build.assert_not_called()
            with urllib.request.urlopen(self.base + '/api/image?' + urlencode({'siteId':'komiic', 'url':logical})) as response:
                self.assertEqual(response.read(), output.getvalue())
            self.assertEqual(queries[-1][1], {'kids': [kid]})
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.get_header('X-image-ticket'), 'test-ticket')
            self.assertEqual(request.full_url, 'https://img.komiic.com/synthetic.jpg')

    def test_invalid_komiic_marker_does_not_fall_through_to_upstream_get(self):
        with patch.object(p.komiic, '_query') as query, patch('server.urllib.request.build_opener') as proxy:
            connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
            try:
                connection.request('GET', '/api/image?' + urlencode({'url':'https://komiic.com/api/image/invalid%2Fkid'}))
                response = connection.getresponse()
                self.assertEqual(response.status, 400)
                response.read()
            finally:
                connection.close()
            query.assert_not_called()
            proxy.assert_not_called()

    def test_apk_source_list_and_image_referer(self):
        with urllib.request.urlopen(self.base + '/api/sites') as response:
            data = json.load(response)['data']
        self.assertTrue(set(p.APK_PROVIDERS) <= {s['siteId'] for s in data})
        self.assertIn('权限', next(s for s in data if s['siteId'] == 'zaimanhua')['notice'])
        headers = Message(); headers['Content-Type'] = 'image/jpeg'
        with patch('server.urllib.request.build_opener') as build:
            response = build.return_value.open.return_value.__enter__.return_value
            response.headers = headers; response.read.return_value = b'fixture-image'
            connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
            try:
                connection.request('GET', '/api/image?siteId=zaimanhua&url=https%3A%2F%2Fimages.zaimanhua.com%2Fpage.jpg')
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), b'fixture-image')
                request = build.return_value.open.call_args.args[0]
                self.assertEqual(request.get_header('Referer'), 'https://manhua.zaimanhua.com/')
                self.assertTrue(any(isinstance(handler, ImageRedirect) for handler in build.call_args.args))
            finally:
                connection.close()

    def test_module_static_routes_are_exact_and_have_correct_mime(self):
        names = ('search-model.js', 'search-view.js', 'search.css', 'reader.js', 'reader-model.js', 'reader-transport.js', 'reader.css',
                 'source-catalog.js', 'source-catalog.css', 'source-preferences.js', 'library-model.js', 'library.css',
                 'library-updates.js', 'discovery.js', 'discovery-model.js', 'discovery.css',
                 'recommendations.js', 'recommendations-model.js', 'recommendations.css', 'cover-wall.js', 'cover-wall.css', 'home.css', 'discovery-covers.js')
        names += ('book-identity.js', 'library-store.js', 'library-auto-updates.js', 'recommendations-feedback.js', 'recommendations-ranking.js')
        names += ('sardina.css', 'brand/sardina-070.png', 'brand/sardina-097.png', 'brand/sardina-097-a.png', 'brand/sardina-097-b.png', 'brand/sardina-097-c.png')
        names += ('image-loader.js', 'download-store.js', 'download-model.js', 'downloads.js', 'downloads.css')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'web').mkdir()
            for name in names:
                (root / 'web' / name).parent.mkdir(parents=True, exist_ok=True)
                (root / 'web' / name).write_text('/* ' + name + ' */')
            # This existing private file must still not become public.
            (root / 'web' / 'home.json').write_text('{}')
            (root / 'web' / 'brand' / 'private.json').write_text('{}')
            with patch('server.ROOT', root):
                for name in names:
                    with self.subTest(name=name), urllib.request.urlopen(self.base + '/' + name) as response:
                        self.assertEqual(response.status, 200)
                        self.assertIn(name.encode(), response.read())
                        expected = ('text/javascript', 'application/javascript') if name.endswith('.js') else ('image/png',) if name.endswith('.png') else ('text/css',)
                        self.assertIn(response.headers.get_content_type(), expected)
                for path in ('/home.json', '/unknown.js', '/web/reader.js', '/../server.py', '/brand/private.json', '/brand/../home.json'):
                    with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as exc:
                        urllib.request.urlopen(self.base + path)
                    self.assertEqual(exc.exception.code, 404)

    def test_invalid_body_and_origin(self):
        for body,headers,status in [(b'[]',{},400),(b'{}',{'Origin':'https://evil.test'},403)]:
            req=urllib.request.Request(self.base+'/api/search',data=body,headers=headers)
            with self.assertRaises(urllib.error.HTTPError) as exc: urllib.request.urlopen(req)
            self.assertEqual(exc.exception.code,status)

    def test_reject_foreign_host(self):
        req=urllib.request.Request(self.base+'/api/config',headers={'Host':'evil.test'})
        with self.assertRaises(urllib.error.HTTPError) as exc:urllib.request.urlopen(req)
        self.assertEqual(exc.exception.code,403)


if __name__=='__main__':unittest.main()
