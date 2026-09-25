# Source HTML fixtures

Captured 2026-09-20 from public native source responses using the existing client User-Agent. These are small, unchanged HTML excerpts from the relevant search/metadata/directory containers; scripts and unrelated bulk chapter links were omitted. They do not contain account data. Tests assert independent expected values against the real HTML parser.

| Fixture | Source |
| --- | --- |
| dm5-search | `https://www.dm5.com/search?title=三月的狮子` |
| mangabz-search | `https://www.mangabz.com/search?title=三月的狮子` |
| manben-search | `https://www.manben.com/search?title=三月的狮子&language=1` |
| baozimh-search | `https://www.baozimh.com/search?q=三月的狮子` |
| dm5-detail | `https://www.dm5.com/manhua-sanyuedeshizi/` |
| dm5-available-directory | `https://www.dm5.com/manhua-longzhu-x-yiquanchaoren/` |
| mangabz-detail | `https://www.mangabz.com/290bz/` |
| manben-detail | `https://www.manben.com/mh-xiaoshizi-leo/` |
| baozimh-detail | `https://www.baozimh.com/comic/sanyuedeshizi-yuhaiyeqianhua` |
| manhuazhijia-detail | `https://www.manhuazhijia.cc/comic/sanyuedeshizi` |
| manhuagui-detail | `https://www.manhuagui.com/comic/4779/` |
| tuku-detail | `https://www.tuku.cc/manga-68379/` |
| rumanhua-detail | `http://rumanhua2.com/TRrNETo/` |

DM5's exact search hit lives in `banner_detail_form`, separately from `mh-list`. The observed detail page for 三月的狮子 has a copyright removal notice and only other books' chapter links in recommendations. The directory fixture retains the real five chapters and adds one clearly marked synthetic sidebar link to exercise exclusion. Additional hostile/irrelevant links are supplied directly by tests.

“最新” or “更新至第217话” does not prove publication status. Mangabz/Manben search pages do not expose authors in their cards, so those values remain empty. Details recover their authors from the already-required detail request.
