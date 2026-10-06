import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from client import apk_vomic_html as source


FIXTURES = json.loads((Path(__file__).parent / "fixtures/apk-vomic-html/pages.json").read_text())


class VomicHtmlTests(unittest.TestCase):
    def test_double_escaped_author_credits_are_plain_text(self):
        fixture = FIXTURES['guazimanhua']
        page = fixture['detail'].replace('普通作者', 'Team Argo,Monohumbug&amp;#40Redice Studio&amp;#41,Saenal')
        detail = source.parse_details('guazimanhua', page, fixture['book'])
        self.assertEqual(detail['author'], 'Team Argo,Monohumbug(Redice Studio),Saenal')

    def test_all_four_search_scopes_exclude_recommendations_and_duplicates(self):
        for site, fixture in FIXTURES.items():
            with self.subTest(site=site):
                rows = source.parse_search(site, fixture["search"])
                self.assertEqual([row["title"] for row in rows], ["普通作品"])
                self.assertEqual(rows[0]["url"], fixture["book"])
                self.assertTrue(rows[0]["cover"].startswith("https://"))
                self.assertEqual(set(rows[0]), {"title", "url", "cover", "author", "latest", "description", "status"})

    def test_all_four_directories_use_book_metadata_and_reading_order(self):
        for site, fixture in FIXTURES.items():
            with self.subTest(site=site):
                detail = source.parse_details(site, fixture["detail"], fixture["book"])
                self.assertEqual(detail["title"], "普通作品")
                self.assertEqual(detail["author"], "普通作者")
                self.assertEqual(detail["description"], "合成作品简介")
                self.assertEqual(detail["status"], "连载")
                self.assertEqual(detail["sourceUrl"], fixture["book"])
                if site == "guazimanhua":
                    self.assertEqual(detail["catalogCompleteness"], "complete")
                else:
                    self.assertNotEqual(detail.get("catalogCompleteness"), "complete")
                self.assertEqual([row["name"] for row in detail["chapters"]], fixture["names"])
                self.assertEqual([row["order"] for row in detail["chapters"]], list(range(len(fixture["names"]))))
                self.assertEqual(detail["chapters"][0]["url"], fixture["chapter"])
                self.assertTrue(all(set(row) == {"id", "name", "url", "order", "group"} for row in detail["chapters"]))

    def test_reader_images_exclude_cover_ads_and_duplicate_mobile_nodes(self):
        for site, fixture in FIXTURES.items():
            with self.subTest(site=site):
                images = source.parse_images(site, fixture["read"], fixture["chapter"])
                self.assertEqual(len(images), 2)
                self.assertEqual(images[0], fixture["image1"])
                self.assertTrue(all("page" in url for url in images))

    def test_manhua6_verified_catalogue_cdns_keep_covers_when_opening_details(self):
        fixture = FIXTURES["manhua6"]
        for host in ("manhuagui.caiji2029.com", "copyimg.caiji2029.com", "ya2028.mzdtour.com", "kanmancc.mzdtour.com"):
            page = fixture["detail"].replace("manhuagui.caiji2029.com", host)
            with self.subTest(host=host):
                detail = source.parse_details("manhua6", page, fixture["book"])
                self.assertEqual(urlparse(detail["coverUrl"]).hostname, host)
                self.assertEqual([row["name"] for row in detail["chapters"]], fixture["names"])
        unknown = fixture["detail"].replace("manhuagui.caiji2029.com", "copyimg.caiji2029.com.evil.test")
        self.assertEqual(source.parse_details("manhua6", unknown, fixture["book"])["coverUrl"], "")

    def test_unknown_reader_cdn_fails_instead_of_silently_losing_pages(self):
        for site, fixture in FIXTURES.items():
            with self.subTest(site=site):
                host = urlparse(fixture["image1"]).hostname
                changed = fixture["read"].replace(host, "unknown-cdn.invalid")
                with self.assertRaisesRegex(RuntimeError, "图片地址发生变化"):
                    source.parse_images(site, changed, fixture["chapter"])

    def test_challenge_and_changed_page_are_not_reported_as_empty_search(self):
        for page, message in [("<title>安全验证</title>", "要求验证"), ("<title>欢迎首页</title>", "结构发生变化")]:
            with self.subTest(page=page):
                with self.assertRaisesRegex(RuntimeError, message):
                    source.parse_search("manhua1234", page)
        self.assertEqual(source.parse_search("manhua1234", '<div class="comic-grid"></div>'), [])

    def test_missing_directory_has_explicit_unavailability(self):
        fixture = FIXTURES["manhua6"]
        page = fixture["detail"].replace('class="chapter__list-box clearfix"', 'class="paywall"')
        detail = source.parse_details("manhua6", page, fixture["book"])
        self.assertEqual(detail["chapters"], [])
        self.assertIn("未返回公开章节目录", detail["unavailableReason"])

    def test_unknown_or_foreign_addresses_are_rejected_before_network(self):
        bad = [
            "https://outside.invalid/comic/10", "https://user:secret@www.hzxidou.com/comic/10",
            "https://www.hzxidou.com:9443/comic/10", "file:///comic/10",
            "https://www.hzxidou.com/chapter/10",
        ]
        with patch.object(source, "_get") as fetch:
            for url in bad:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    source.details("manhua6", url)
            with self.assertRaises(ValueError):
                source.images("manhua1234", "https://reader.hqread.cc/not-a-chapter")
            fetch.assert_not_called()

    def test_search_keyword_is_encoded_as_one_query_value(self):
        with patch.object(source, "_get", return_value=FIXTURES["manhua1234"]["search"]) as fetch:
            source.search("manhua1234", "普通作品 & 作者")
            url = fetch.call_args.args[1]
            self.assertEqual(parse_qs(urlparse(url).query), {"key": ["普通作品 & 作者"]})

    def test_public_read_host_mapping_is_fixed_and_makes_one_request(self):
        fixture = FIXTURES["manhua1234"]
        with patch.object(source, "_get", return_value=fixture["read"]) as fetch:
            images = source.images("manhua1234", fixture["chapter"])
            self.assertEqual(len(images), 2)
            reader_url = fixture["chapter"].replace("https://m.wmh1234.com/go/", "https://reader.hqread.cc/r/")
            fetch.assert_called_once_with("manhua1234", reader_url)
        self.assertEqual(source._chapter_url("manhua1234", reader_url), fixture["chapter"])

    def test_guazi_ids_are_canonical_and_duplicate_ids_rejected(self):
        self.assertEqual(source._book_url("guazimanhua", "https://www.guazimanhua.com/comic.php?id=10&utm=a"), FIXTURES["guazimanhua"]["book"])
        with self.assertRaises(ValueError):
            source._chapter_url("guazimanhua", "https://www.guazimanhua.com/chapter.php?id=10&id=11")

    def test_wrong_requested_book_or_chapter_is_rejected_for_every_source(self):
        for site, fixture in FIXTURES.items():
            for field, parse in [("book", source.parse_details), ("chapter", source.parse_images)]:
                with self.subTest(site=site, field=field):
                    page = fixture["detail" if field == "book" else "read"]
                    wrong = re.sub(r"10(?=\.html$|$)", "99999", fixture[field])
                    if site == "manhua1234" and field == "chapter":
                        wrong = fixture[field].rsplit("/", 1)[0] + "/wrong001"
                    with self.assertRaisesRegex(RuntimeError, "身份与请求不一致"):
                        parse(site, page, wrong)

    def test_same_host_redirect_cannot_change_identity(self):
        for site, fixture in FIXTURES.items():
            for field in ["book", "chapter"]:
                original = fixture[field]
                wrong = re.sub(r"10(?=\.html$|$)", "99999", original)
                if site == "manhua1234" and field == "chapter":
                    wrong = original.rsplit("/", 1)[0] + "/wrong001"
                with self.subTest(site=site, field=field):
                    with self.assertRaisesRegex(RuntimeError, "重定向到了其他作品或章节"):
                        source._check_redirect(site, original, wrong)
                    self.assertEqual(source._check_redirect(site, original, original), original)
        fixture = FIXTURES["manhua1234"]
        reader_url = fixture["chapter"].replace("https://m.wmh1234.com/go/", "https://reader.hqread.cc/r/")
        self.assertEqual(source._check_redirect("manhua1234", fixture["chapter"], reader_url), reader_url)

    def test_conflicting_canonical_or_observed_data_id_is_rejected(self):
        mutations = [
            ("manhua1234", "detail", 'data-comic-id="10"', 'data-comic-id="999"'),
            ("manhua1234", "read", 'data-chapter-id="10"', 'data-chapter-id="999"'),
            ("cocoecar", "detail", 'data-id="10"', 'data-id="999"'),
            ("manhua6", "detail", 'data-id="10"', 'data-id="999"'),
            ("guazimanhua", "detail", 'comic.php?id=10', 'comic.php?id=999'),
            ("guazimanhua", "read", 'chapterId = "10"', "chapterId = '999'"),
        ]
        for site, stage, old, new in mutations:
            fixture = FIXTURES[site]
            parse = source.parse_details if stage == "detail" else source.parse_images
            with self.subTest(site=site, stage=stage):
                with self.assertRaisesRegex(RuntimeError, "身份与请求不一致"):
                    parse(site, fixture[stage].replace(old, new, 1), fixture["book" if stage == "detail" else "chapter"])

    def test_missing_all_page_identity_evidence_is_not_accepted(self):
        for site, fixture in FIXTURES.items():
            page = re.sub(r'<link rel="canonical"[^>]*>', '', fixture["detail"])
            page = re.sub(r' data-(?:comic-)?id="\d+"', '', page)
            with self.subTest(site=site), self.assertRaisesRegex(RuntimeError, "缺少可核验"):
                source.parse_details(site, page, fixture["book"])

    def test_invalid_directory_link_is_not_silently_skipped(self):
        for site, fixture in FIXTURES.items():
            if site == "manhua1234":
                old = '/go/' + fixture["chapter"].rsplit('/', 1)[-1]
            elif site == "guazimanhua":
                old = '/chapter.php?id=10'
            else:
                old = '/chapter/10'
            page = fixture["detail"].replace(f'href="{old}"', 'href="/changed-chapter/10"', 1)
            with self.subTest(site=site), self.assertRaisesRegex(RuntimeError, "无法解析的章节地址"):
                source.parse_details(site, page, fixture["book"])

    def test_mint_directory_data_id_must_match_its_link(self):
        fixture = FIXTURES["manhua1234"]
        page = fixture["detail"].replace('data-chapter-id="10"', 'data-chapter-id="999"', 1)
        with self.assertRaisesRegex(RuntimeError, "目录的作品或章节标识不一致"):
            source.parse_details("manhua1234", page, fixture["book"])

    def test_guazi_declared_chapter_total_detects_missing_entry(self):
        fixture = FIXTURES["guazimanhua"]
        page = fixture["detail"].replace('<a href="/chapter.php?id=11">第2话</a>', '')
        with self.assertRaisesRegex(RuntimeError, "章节目录不完整.*3.*2"):
            source.parse_details("guazimanhua", page, fixture["book"])
        # A second contradictory declaration is also a failure, even when one
        # of the two page declarations happens to match the extracted count.
        with self.assertRaisesRegex(RuntimeError, "章节目录不完整"):
            source.parse_details("guazimanhua", fixture["detail"].replace('numberOfItems": 3', 'numberOfItems": 4'), fixture["book"])

    def test_changed_or_removed_reader_nodes_are_not_partial_success(self):
        for site, fixture in FIXTURES.items():
            page = fixture["read"]
            if site == "manhua1234":
                page = page.replace('class="reader-image lazy"', 'class="changed-image"')
            elif site == "guazimanhua":
                page = page.replace('class="reading-image is-active"', 'class="changed-image"')
            else:
                # Removing a whole page container must be detected by the
                # reader's declared count, not just the remaining selectors.
                page = re.sub(r'<div class="rd-article__pic[^\"]*"><img[^>]*page1\.[^>]*></div>', '', page)
            with self.subTest(site=site), self.assertRaisesRegex(RuntimeError, "图片.*(?:结构发生变化|不完整)"):
                source.parse_images(site, page, fixture["chapter"])

    def test_coco_and_six_image_class_changes_are_explicit_errors(self):
        for site in ["cocoecar", "manhua6"]:
            fixture = FIXTURES[site]
            page = fixture["read"].replace('class="lazy-read', 'class="changed', 2)
            with self.subTest(site=site), self.assertRaisesRegex(RuntimeError, "正文图片节点缺失或重复"):
                source.parse_images(site, page, fixture["chapter"])

    def test_missing_declared_page_count_is_not_assumed_complete(self):
        for site in ["guazimanhua", "cocoecar", "manhua6"]:
            fixture = FIXTURES[site]
            page = re.sub(r'<script type="application/ld\+json">.*?</script>', '', fixture["read"])
            page = page.replace('class="page-index__btn"', 'class="changed-page-index"')
            with self.subTest(site=site), self.assertRaisesRegex(RuntimeError, "缺少声明页数"):
                source.parse_images(site, page, fixture["chapter"])


if __name__ == "__main__":
    unittest.main()
