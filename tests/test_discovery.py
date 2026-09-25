import io
import json
from email.message import Message
from pathlib import Path
import ssl
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request

from client import discovery as d, providers


FIXTURES = Path(__file__).parent / "fixtures" / "discovery"


def fixture(name):
    return (FIXTURES / name).read_text()


class Response(io.BytesIO):
    def __init__(self, body=b"<html></html>", *, url="https://www.manhuagui.com/rank/", content_type="text/html", length=None):
        super().__init__(body)
        self.url = url
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        if length is not None:
            self.headers["Content-Length"] = str(length)

    def geturl(self):
        return self.url


class DiscoveryTests(unittest.TestCase):
    def test_cover_request_is_strict_and_returns_canonical_book_identity(self):
        self.assertEqual(d.normalize_cover_request({"siteId": "manhuagui", "detailUrl": "https://www.manhuagui.com/comic/43661"}),
                         ("manhuagui", "https://www.manhuagui.com/comic/43661/", False))
        self.assertEqual(d.normalize_cover_request({"siteId": "manben", "detailUrl": "https://www.manben.com/mh-yaoshenji/", "refresh": True}),
                         ("manben", "https://www.manben.com/mh-yaoshenji/", True))
        base = {"siteId": "manhuagui", "detailUrl": "https://www.manhuagui.com/comic/43661/"}
        invalid = [None, [], True, {}, {**base, "url": base["detailUrl"]}]
        invalid += [{**base, "siteId": x} for x in [None, [], {}, True, "dm5", "manben"]]
        invalid += [{**base, "refresh": x} for x in [None, 1, 0, "true", [], {}]]
        invalid += [{**base, "detailUrl": x} for x in [None, [], {}, 123,
                    "http://www.manhuagui.com/comic/43661/", "https://manhuagui.com/comic/43661/",
                    "https://www.manhuagui.com:443/comic/43661/", "https://user@www.manhuagui.com/comic/43661/",
                    "https://www.manhuagui.com.evil.test/comic/43661/", "https://www.manhuagui.com/comic/43661/910038.html",
                    "https://www.manhuagui.com/rank/", "https://www.manhuagui.com/update/",
                    "https://www.manhuagui.com/comic/0/", "https://www.manhuagui.com/comic/43661/?",
                    "https://www.manhuagui.com/comic/43661/#", "https://www.manhuagui.com/comic/43661/?x=1",
                    "https://www.manhuagui.com/comic/43661/../43662/", "https://www.manhuagui.com/comic/%34%33/",
                    "https://www.manhuagui.com/comic/43661/\n"]]
        invalid += [{"siteId": "manben", "detailUrl": "https://www.manben.com" + path} for path in [
            "/mh-ranklist/", "/mh-ranklist-collections/", "/mh-updated/", "/mh-list/", "/m1833499/",
            "/mh-yaoshenji/p2/", "/mh-yaoshenji/?page=2", "/mh-yaoshenji/#chapters"]]
        with patch.object(d, "_download") as download:
            for body in invalid:
                with self.subTest(body=body), self.assertRaises(ValueError):
                    d.normalize_cover_request(body)
        download.assert_not_called()

    def test_real_main_cover_excerpts_ignore_recommendations_and_fetch_only_one_page(self):
        cases = [("manhuagui", "https://www.manhuagui.com/comic/43661/", "https://cf.mhgui.com/cpic/h/43661.jpg"),
                 ("manben", "https://www.manben.com/mh-yaoshenji/", "https://mhfm8us.cdndm5.com/34/33771/20251023133237_180x240_27.jpg")]
        for site, url, cover in cases:
            source = ('<meta property="og:image" content="https://cf.mhgui.com/cpic/h/wrong.jpg">'
                      '<aside><img src="https://cf.mhgui.com/cpic/h/recommendation.jpg"></aside>'
                      + fixture(site + "-book-cover.html"))
            with self.subTest(site=site), patch.object(d, "_download", return_value=source) as download:
                self.assertEqual(d.book_cover(site, url), {"siteId": site, "detailUrl": url, "coverUrl": cover})
            download.assert_called_once_with(url, book_cover=True)

    def test_cover_missing_primary_structure_never_falls_back_to_recommendations(self):
        for site, url, old, new in [
            ("manhuagui", "https://www.manhuagui.com/comic/43661/", 'class="book-cover fl"', 'class="missing-cover"'),
            ("manben", "https://www.manben.com/mh-yaoshenji/", 'class="ib cover"', 'class="missing-cover"'),
        ]:
            source = fixture(site + "-book-cover.html").replace(old, new)
            source += '<aside class="book-cover cover"><img src="https://cf.mhgui.com/cpic/h/recommendation.jpg"></aside>'
            with self.subTest(site=site), self.assertRaises(d.DiscoveryError):
                d._main_cover(site, source, url)

    def test_cover_rejects_wrong_book_identity_title_and_ambiguous_main_image(self):
        gui = fixture("manhuagui-book-cover.html")
        manben = fixture("manben-book-cover.html")
        cases = [("manhuagui", gui.replace('href="/comic/43661/"', 'href="/comic/2/"')),
                 ("manhuagui", gui.replace('<h1>成为铁匠在异世界度过悠闲人生</h1>', '<h1>其他作品</h1>')),
                 ("manhuagui", gui.replace('<img ', '<img src="https://cf.mhgui.com/cpic/h/2.jpg"><img ', 1)),
                 ("manhuagui", gui + '<link rel="canonical" href="https://www.manhuagui.com/comic/2/">'),
                 ("manhuagui", gui.replace('class="crumb w998"', 'class="missing"')),
                 ("manben", manben.replace('mh-yaoshenji', 'mh-another')),
                 ("manben", manben.replace('alt="妖神记"', 'alt="其他作品"')),
                 ("manben", manben.replace('shareDetail', 'missing')),
                 ("manben", manben + '<meta property="og:url" content="https://evil.test/mh-yaoshenji/">')]
        for site, source in cases:
            url = "https://www.manhuagui.com/comic/43661/" if site == "manhuagui" else "https://www.manben.com/mh-yaoshenji/"
            with self.subTest(site=site, source=source[-90:]), self.assertRaises(d.DiscoveryError):
                d._main_cover(site, source, url)

    def test_cover_rejects_missing_placeholder_untrusted_image_and_challenge(self):
        source = fixture("manhuagui-book-cover.html")
        for raw in ["", "//cf.mhgui.com/cpic/h/placeholder.jpg", "https://mhgui.com.evil.test/cover.jpg",
                    "https://user@cf.mhgui.com/cover.jpg", "https://cf.mhgui.com:444/cover.jpg",
                    "https://cf.mhgui.com/cover.jpg#bad", "https://cf.mhgui.com/bad cover.jpg"]:
            changed = source.replace("//cf.mhgui.com/cpic/h/43661.jpg", raw)
            with self.subTest(raw=raw), self.assertRaises(d.DiscoveryError):
                d._main_cover("manhuagui", changed, "https://www.manhuagui.com/comic/43661/")
        with self.assertRaises(d.DiscoveryError):
            d._main_cover("manhuagui", '<title>Just a moment...</title>' + source, "https://www.manhuagui.com/comic/43661/")

    def test_cover_download_opt_in_preserves_list_endpoint_and_post_policy(self):
        url = "https://www.manhuagui.com/comic/43661/"
        source = fixture("manhuagui-book-cover.html")
        opener = Mock(open=Mock(return_value=Response(source.encode(), url=url)))
        with patch.object(d, "build_opener", return_value=opener):
            self.assertEqual(d._download(url, book_cover=True), source)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.full_url, url)
        self.assertLessEqual(opener.open.call_args.kwargs["timeout"], d._TIMEOUT)
        with patch.object(d, "build_opener") as build:
            for kwargs in ({}, {"book_cover": True, "pageindex": 3}):
                with self.assertRaises(ValueError):
                    d._download(url, **kwargs)
            with self.assertRaises(ValueError):
                d._download("https://www.manhuagui.com/rank/", book_cover=True)
        build.assert_not_called()

    def test_cover_transport_preserves_response_caps_deadline_and_book_identity(self):
        url = "https://www.manhuagui.com/comic/43661/"
        for response in [Response(url="https://www.manhuagui.com/comic/2/"),
                         Response(url=url, content_type="application/json"), Response(url=url, content_type="image/jpeg"),
                         Response(url=url, length=d._MAX_RESPONSE + 1), Response(b"x" * (d._MAX_RESPONSE + 1), url=url)]:
            with patch.object(d, "build_opener", return_value=Mock(open=Mock(return_value=response))), self.assertRaises(d.DiscoveryError):
                d._download(url, book_cover=True)
        with patch.object(d, "build_opener", return_value=Mock(open=Mock(return_value=Response(url=url)))), \
                patch.object(d.time, "monotonic", side_effect=[1, 2]), self.assertRaisesRegex(d.DiscoveryError, "超时"):
            d._download(url, book_cover=True, deadline=2)
        redirect = d._Redirect(url, book_cover=True)
        for other in ["https://www.manhuagui.com/comic/2/", "https://www.manben.com/mh-yaoshenji/",
                      "https://www.manhuagui.com/comic/43661/910038.html", "https://www.manhuagui.com/rank/",
                      "https://evil.test/comic/43661/"]:
            with self.subTest(url=other), self.assertRaises((ValueError, d.DiscoveryError)):
                redirect.redirect_request(Request(url), None, 302, "", {}, other)

    def test_capabilities_are_real_and_return_independent_values(self):
        sites = d.sources()
        self.assertEqual([s["siteId"] for s in sites[:2]], ["manhuagui", "manben"])
        self.assertEqual({s["siteId"] for s in sites}, set(providers.SOURCES))
        self.assertEqual(len(sites), len({s["siteId"] for s in sites}))
        self.assertEqual([p["id"] for p in sites[0]["modes"][0]["periods"]], ["day", "week", "month", "total"])
        self.assertEqual(sites[1]["modes"][0]["periods"], [])
        sites[0]["modes"][0]["periods"].clear()
        self.assertEqual(len(d.sources()[0]["modes"][0]["periods"]), 4)

    def test_request_defaults_and_integer_page_normalization(self):
        self.assertEqual(d.normalize_request({}), ("manhuagui", "popular", "day", 1))
        self.assertEqual(d.normalize_request({"siteId": "manben", "kind": "latest", "page": "2"}), ("manben", "latest", "", 2))
        self.assertEqual(d.normalize_request({"kind": "latest", "refresh": True}), ("manhuagui", "latest", "", 1))

    def test_request_rejects_wrong_types_values_and_arbitrary_urls(self):
        for value in (None, [], "popular", True):
            with self.subTest(body=value), self.assertRaises(ValueError):
                d.normalize_request(value)
        cases = [{"siteId": v} for v in ([], {}, True, "unknown", "https://example.com")]
        cases += [{"kind": v} for v in ([], {}, False, "top")]
        cases += [{"period": v} for v in ([], {}, None, True, "year", "")]
        cases += [{"kind": "latest", "page": v} for v in (True, False, [], {}, 0, -1, 1.5, 1001, "01", "1.0")]
        cases += [{"kind": "latest", "period": "day"}, {"siteId": "manben", "period": "total"}, {"page": 2}, {"url": "https://example.com/"}]
        for value in cases:
            with self.subTest(body=value), self.assertRaises(ValueError):
                d.normalize_request(value)

    def test_gui_rank_reads_source_ranks_author_and_update_without_fake_covers(self):
        data = fixture("manhuagui-rank.html")
        items = d._gui_popular(data, "day")
        self.assertEqual([x["rank"] for x in items], [1, 2, 3])
        self.assertEqual(items[2]["title"], "我独自升级")
        self.assertEqual(items[2]["author"], "DUBU / Chugong(추공)")
        self.assertEqual(items[2]["updatedAtText"], "2026-09-20")
        self.assertTrue(all(x["coverUrl"] == "" for x in items))
        self.assertTrue(all(x["detailUrl"].startswith("https://www.manhuagui.com/comic/") for x in items))
        with self.assertRaises(d.DiscoveryError):
            d._gui_popular(data, "month")

    def test_gui_local_pagination_covers_full_fixture_without_repeating_first_page(self):
        source = fixture("manhuagui-latest.html")
        with patch.object(d, "_download", return_value=source) as download:
            first = d.fetch("manhuagui", "latest", "", 1)
            last = d.fetch("manhuagui", "latest", "", 2)
            empty = d.fetch("manhuagui", "latest", "", 3)
        self.assertEqual([len(x["items"]) for x in (first, last, empty)], [24, 1, 0])
        self.assertEqual([x["hasMore"] for x in (first, last, empty)], [True, False, False])
        self.assertNotIn(last["items"][0]["detailUrl"], {x["detailUrl"] for x in first["items"]})
        self.assertEqual(first["label"], "最近7天更新")
        self.assertEqual(first["sourceUrl"], last["sourceUrl"])
        self.assertIn("24", first["paginationNote"])
        self.assertRegex(first["fetchedAt"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertTrue(all(call.args == ("https://www.manhuagui.com/update/",) for call in download.call_args_list))
        self.assertEqual(first["items"][0]["updatedAtText"], "2026-09-21")
        self.assertNotIn("rank", first["items"][0])

    def test_manben_rank_is_scoped_and_uses_real_fields(self):
        source = '<nav><a href="/mh-fake/">导航推荐</a></nav>' + fixture("manben-rank.html")
        items = d._manben_popular(source)
        self.assertEqual(len(items), 3)
        self.assertEqual(items[1]["title"], "妖神记")
        self.assertEqual(items[1]["rank"], 2)
        self.assertEqual(items[1]["author"], "踏雪动漫")
        self.assertEqual(items[1]["latestChapter"], "第531回")
        self.assertIn("cdndm5.com", items[1]["coverUrl"])
        self.assertNotIn("updatedAtText", items[1])
        with self.assertRaises(d.DiscoveryError):
            d._manben_popular(source.replace("bak_1 active", "bak_1"))

    def test_manben_initial_update_and_next_api_batch_follow_official_binding(self):
        with patch.object(d, "_download", side_effect=[fixture("manben-latest.html"), fixture("manben-batch3.json")]) as download:
            first = d.fetch("manben", "latest", "", 1)
        self.assertEqual(len(first["items"]), 19)
        self.assertTrue(first["hasMore"])
        self.assertNotIn("updatedAtText", first["items"][0])
        self.assertEqual(download.call_args_list[1].kwargs["pageindex"], 3)
        self.assertEqual(download.call_args_list[0].kwargs["deadline"], download.call_args_list[1].kwargs["deadline"])
        with patch.object(d, "_download", side_effect=[fixture("manben-batch3.json"), fixture("manben-batch4.json")]) as download:
            second = d.fetch("manben", "latest", "", 2)
        self.assertEqual([c.kwargs["pageindex"] for c in download.call_args_list], [3, 4])
        self.assertEqual(second["items"][0]["updatedAtText"], "2024-11-07")
        self.assertEqual(second["items"][0]["author"], "李余灵疑")
        self.assertTrue(second["hasMore"])
        self.assertFalse({x["detailUrl"] for x in first["items"]} & {x["detailUrl"] for x in second["items"]})

    def test_manben_only_confirmed_empty_batch_means_end(self):
        with patch.object(d, "_download", side_effect=[fixture("manben-batch3.json"), "[]"]):
            result = d.fetch("manben", "latest", "", 2)
        self.assertFalse(result["hasMore"])
        self.assertEqual(len(result["items"]), 2)
        with patch.object(d, "_download", return_value="[]") as download:
            result = d.fetch("manben", "latest", "", 3)
        self.assertEqual(result["items"], [])
        self.assertFalse(result["hasMore"])
        self.assertEqual(download.call_count, 1)

    def test_manben_unconfirmed_or_repeated_next_page_is_an_error(self):
        for next_result in ("<html>访问验证</html>", '{"error":"blocked"}', fixture("manben-batch3.json")):
            with self.subTest(next=next_result[:25]), patch.object(d, "_download", side_effect=[fixture("manben-batch3.json"), next_result]), self.assertRaises(d.DiscoveryError):
                d.fetch("manben", "latest", "", 2)
        with patch.object(d, "_download", side_effect=[fixture("manben-batch3.json"), d.DiscoveryError("超时")]), self.assertRaisesRegex(d.DiscoveryError, "超时"):
            d.fetch("manben", "latest", "", 2)

    def test_changed_manben_pagination_binding_does_not_guess(self):
        source = fixture("manben-latest.html")
        for changed in (source.replace("mypage = 2", "mypage = 1"), source.replace("t: 8", "t: 9"), source.replace("'POST'", "'GET'")):
            with self.assertRaisesRegex(d.DiscoveryError, "分页协议"):
                d._manben_latest(changed)

    def test_broken_records_and_challenge_pages_are_not_empty_success(self):
        for fn in (d._gui_latest, d._manben_popular, d._manben_latest):
            with self.assertRaises(d.DiscoveryError):
                fn('<html><title>Just a moment...</title></html>')
        with self.assertRaises(d.DiscoveryError):
            d._gui_latest('<html><title>最新更新漫画_7天内更新的漫画</title></html>')
        rows = json.loads(fixture("manben-batch3.json"))
        rows[0]["Url"] = "https://evil.example/book"
        with self.assertRaises(d.DiscoveryError):
            d._manben_batch(json.dumps(rows))
        with self.assertRaises(d.DiscoveryError):
            d._manben_batch('[null]')

    def test_rank_gaps_and_cross_domain_books_are_rejected(self):
        source = fixture("manhuagui-rank.html")
        with self.assertRaises(d.DiscoveryError):
            d._gui_popular(source.replace('rank-no2">2', 'rank-no2">8'), "day")
        for site, path in (("manhuagui", "https://www.manhuagui.com.evil.test/comic/1/"),
                           ("manhuagui", "/comic/1/2.html"), ("manben", "/mh-ranklist/"),
                           ("manben", "/mh-updated/"), ("manben", "http://user@www.manben.com/mh-one/")):
            with self.subTest(url=path), self.assertRaises(d.DiscoveryError):
                d._book_url(site, path)

    def test_cover_domains_have_exact_boundaries(self):
        self.assertEqual(d._cover("manhuagui", "//cf.mhgui.com/cpic/m/1.jpg"), "https://cf.mhgui.com/cpic/m/1.jpg")
        for url in ("https://mhgui.com.evil.test/1.jpg", "https://evil-mhgui.com/1.jpg", "file:///1.jpg", "https://u:p@cf.mhgui.com/1.jpg"):
            self.assertEqual(d._cover("manhuagui", url), "")

    def test_outbound_endpoints_reject_url_and_redirect_variants(self):
        for url in ("http://www.manhuagui.com/rank/", "https://www.manhuagui.com:443/rank/", "https://www.manhuagui.com.evil.test/rank/",
                    "https://www.manhuagui.com/comic/1/", "https://www.manhuagui.com/rank/?page=2", "https://www.manhuagui.com/rank/#x",
                    "https://www.manben.com/mh-updated/../pagerdata.ashx", "https://user@www.manben.com/mh-ranklist/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                d._endpoint(url)
        redirect = d._Redirect("https://www.manhuagui.com/rank/")
        with self.assertRaises((ValueError, d.DiscoveryError)):
            redirect.redirect_request(Request("https://www.manhuagui.com/rank/"), None, 302, "", {}, "https://www.manben.com/mh-ranklist/")

    def test_download_verifies_https_caps_and_response_identity(self):
        self.assertEqual(d._ssl_context().verify_mode, ssl.CERT_REQUIRED)
        responses = [Response(url="https://www.manhuagui.com/rank/week.html"),
                     Response(content_type="image/jpeg"), Response(length=d._MAX_RESPONSE + 1),
                     Response(b"x" * (d._MAX_RESPONSE + 1)), Response(b"\xff")]
        for response in responses:
            with patch.object(d, "build_opener", return_value=Mock(open=Mock(return_value=response))), self.assertRaises(d.DiscoveryError):
                d._download("https://www.manhuagui.com/rank/")

    def test_download_posts_only_validated_pagination_parameters(self):
        opener = Mock(open=Mock(return_value=Response(b"[]", url=d._MANBEN_MORE)))
        with patch.object(d, "build_opener", return_value=opener):
            self.assertEqual(d._download(d._MANBEN_MORE, pageindex=3), "[]")
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.data, b"t=8&pageindex=3&sc=1")
        self.assertLessEqual(opener.open.call_args.kwargs["timeout"], d._TIMEOUT)
        with patch.object(d, "build_opener") as build:
            for pageindex in (True, 2, "3"):
                with self.assertRaises(ValueError):
                    d._download(d._MANBEN_MORE, pageindex=pageindex)
            with self.assertRaises(ValueError):
                d._download(d._MANBEN_MORE)
            with self.assertRaises(d.DiscoveryError):
                d._download("https://www.manhuagui.com/rank/", deadline=0)
        build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
