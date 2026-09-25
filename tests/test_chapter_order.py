"""Reading-order regressions from ordinary-work metadata captured 2026-09-21."""
from copy import deepcopy
import unittest

from client.chapter_order import order_chapters


# Minimal metadata fixtures from 三月的狮子. Exact observed labels and IDs;
# no comic images or full source HTML are needed to reproduce these bugs.
BAOZI_PREFIX = "https://www.baozimh.com/user/page_direct?comic_id=sanyuedeshizi-yuhaiyeqianhua&section_slot=0&chapter_slot="
BAOZI = [
    {"name": name, "group": "", "url": BAOZI_PREFIX + str(slot)}
    for slot, name in enumerate([
        "3月的獅子 番外篇", "第1話", "第1卷", "第2話", "第2卷", "第3話",
    ])
]
MANGABZ = [
    {"name": name, "group": "", "url": f"https://www.mangabz.com/m{cid}/"}
    for cid, name in [
        (18093, "3月的獅子 番外篇 （19P）"),
        (18094, "第1話 （31P）"),
        (18095, "第1卷 （97P）"),
        (18096, "第2話 （16P）"),
        (18097, "第2卷 （98P）"),
        (18098, "第3話 （16P）"),
    ]
]
RUMANHUA = [
    {"name": name, "group": "", "url": f"http://rumanhua2.com/TRrNETo/{cid}.html"}
    for cid, name in [
        ("IWXXXpq", "第194话 明里的银座物语 2 压力的彼岸"),
        ("DhRRRvf", "第193话 遥远的音乐 5"),
        ("TExxxRo", "番外 Special Episode 明里的银座物语"),
        ("IWXXyXq", "番外 番外漫画 Fighter"),
        ("CgIIaLY", "第2话 chapter. 2 河边的小镇"),
        ("twssbbH", "第1话 chapter. 1 桐山零"),
    ]
]


class ChapterOrderTests(unittest.TestCase):
    def test_baozi_special_then_interleaved_volumes(self):
        result = order_chapters(BAOZI)
        self.assertEqual([r["name"] for r in result], ["第1話", "第2話", "第3話", "第1卷", "第2卷", "3月的獅子 番外篇"])
        self.assertEqual([r["url"].rsplit("=", 1)[-1] for r in result], ["1", "3", "5", "2", "4", "0"])

    def test_mangabz_database_id_is_not_reading_order(self):
        result = order_chapters(MANGABZ)
        self.assertEqual([r["url"].split("/")[-2] for r in result], ["m18094", "m18096", "m18098", "m18095", "m18097", "m18093"])
        self.assertEqual(result[0]["name"], "第1話 （31P）")

    def test_source_descending_order_is_normalized_before_same_volume_ties(self):
        # DM5's observed newest-first DOM. The adapter reverses this source's
        # list before calling the helper; identical volume numbers then keep
        # the source's reading order, without parsing plot subtitles or IDs.
        raw = [
            {"name": name, "url": f"https://www.dm5.com/m{cid}/"}
            for cid, name in [
                (590583, "第3卷 极致的恶 （85P）"),
                (470373, "第2卷 复活的「C」 （89P）"),
                (463655, "第1卷 番外篇 （22P）"),
                (463653, "第1卷 平日白天的超激战（第二击） （46P）"),
                (463652, "第1卷 平日白天的超激战（第一击） （21P）"),
            ]
        ]
        result = order_chapters(reversed(raw))
        self.assertEqual([r["url"].split("/")[-2] for r in result], ["m463652", "m463653", "m470373", "m590583", "m463655"])

    def test_rumanhua_descending_body_and_embedded_extras(self):
        result = order_chapters(RUMANHUA)
        self.assertEqual([r["name"] for r in result], [RUMANHUA[i]["name"] for i in [5, 4, 1, 0, 2, 3]])
        self.assertEqual(result[0]["url"], "http://rumanhua2.com/TRrNETo/twssbbH.html")

    def test_unclassified_entries_keep_absolute_slots_and_numbers_are_not_guessed(self):
        rows = [{"name": n, "id": str(100 - i), "url": f"https://example.test/{100 - i}"} for i, n in enumerate([
            "作者的话", "第12话 (8p)", "2019 夏日随笔", "第2话 (200p)", "三月的狮子", "第3话",
        ])]
        result = order_chapters(rows)
        self.assertEqual([r["name"] for r in result], ["作者的话", "第2话 (200p)", "2019 夏日随笔", "第3话", "三月的狮子", "第12话 (8p)"])
        self.assertEqual([result[i] for i in [0, 2, 4]], [rows[i] for i in [0, 2, 4]])

    def test_known_groups_classify_without_inventing_chapter_numbers(self):
        rows = [
            {"name": "附赠短篇", "group": "番外篇"},
            {"name": "第2卷", "group": "单行本"},
            {"name": "第2话", "group": "单话"},
            {"name": "没有编号的故事", "group": "单话"},
            {"name": "第1话", "group": "单话"},
            {"name": "第1卷", "group": "单行本"},
        ]
        self.assertEqual([r["name"] for r in order_chapters(rows)], ["第1话", "没有编号的故事", "第2话", "第1卷", "第2卷", "附赠短篇"])

    def test_explicit_prelude_moves_before_body_unknown_introduction_stays(self):
        rows = [{"name": n} for n in ["没有标号的引言", "第2章", "前言", "第1章", "序章 开始"]]
        self.assertEqual([r["name"] for r in order_chapters(rows)], ["没有标号的引言", "前言", "序章 开始", "第1章", "第2章"])

    def test_decimal_numbers_fullwidth_and_ties_preserve_meaning(self):
        rows = [{"title": n} for n in ["第10話", "第２回", "第2.5话", "第2话 后篇", "第2话 前篇"]]
        self.assertEqual([r["title"] for r in order_chapters(rows)], ["第２回", "第2话 后篇", "第2话 前篇", "第2.5话", "第10話"])

    def test_regular_subtitle_is_not_mistaken_for_a_special(self):
        rows = [{"name": n} for n in ["第12话", "第8话 意外传送", "第9话 番外的秘密", "第10话 来自 番外篇 的信", "第11话 外传", "外传：第1话 100话特別访谈"]]
        self.assertEqual([r["name"] for r in order_chapters(rows)], ["第8话 意外传送", "第9话 番外的秘密", "第10话 来自 番外篇 的信", "第11话 外传", "第12话", "外传：第1话 100话特別访谈"])

    def test_rows_are_copied_existing_order_is_refreshed_and_result_is_idempotent(self):
        rows = [dict(row, order=i, id="source-" + str(i)) for i, row in enumerate(BAOZI)]
        original = deepcopy(rows)
        result = order_chapters(rows)
        self.assertEqual(rows, original)
        self.assertTrue(all(all(row is not old for old in rows) for row in result))
        self.assertEqual([r["order"] for r in result], list(range(len(result))))
        self.assertEqual({r["id"] for r in result}, {r["id"] for r in rows})
        self.assertEqual(order_chapters(result), result)
        self.assertNotIn("order", order_chapters(BAOZI)[0])

    def test_empty_and_unknown_only_lists_remain_unchanged(self):
        self.assertEqual(order_chapters([]), [])
        rows = [{"name": "001"}, {"name": "Chapter 2"}, {"name": "2019"}, {"name": ""}]
        self.assertEqual(order_chapters(rows), rows)


if __name__ == "__main__":
    unittest.main()
