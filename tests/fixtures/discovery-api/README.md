# Public discovery metadata excerpts

Recorded on 2026-09-22. These files contain list metadata and cover **addresses**,
not downloaded images. HTML excerpts omit scripts, SVG decoration, and unrelated
page sections. Python-literal data in CopyManga's server-rendered `list` attribute
is preserved as data; neither tests nor adapters execute remote JavaScript.

- `hip-popular.html`: first three explicit ranks, active page, next link from
  <https://m.hipmh.com/popularity>. `hip-latest.html`: the separate ten-work
  `recent-updates-section` on <https://m.hipmh.com/>. Source age remains “2周前”.
- `copy-popular.html`: first three explicit male/day ranks and the selected
  cycle from the default <https://www.mangacopy.com/rank> page.
  `copy-latest.html`: 50-work data attribute and pagination from the default
  <https://www.mangacopy.com/comics> page. Their links declare the explicit
  `type=male&table=day` and `ordering=-datetime_updated&offset=0&limit=50`
  selections, which were separately exercised by the live adapter checks.
- `sunday.html`: RANKING and TODAY (today/yesterday) sections from
  <https://www.sunday-webry.com/>. The main cover's `data-src` is retained;
  spacer assets are not accepted as covers.
- `nami-popular.html`: the Hot Titles carousel from <https://namicomi.com/en>.
  `nami-latest.html`: three real book groups from
  <https://namicomi.com/en/updates/latest>, including multiple chapters for one
  book and one gated chapter rendered without a link. Metadata is readable;
  the existing reader remains responsible for chapter permissions.
- `terra-recent.json`: all four episode updates returned by
  <https://comic.hypergryph.com/api/recentUpdate?topicKey=terra-historicus>.
  They belong to one book, so discovery displays the newest once.
- `komiic-*.json`: public read-only `/api/query` responses. The observed
  website uses `hotComics` with `MONTH_VIEWS`/`VIEWS` and `recentUpdate` with
  `DATE_UPDATED`, `asc:true`, `limit:20`, and offsets 0/20. These fields are
  descending in the actual responses. No image/ticket query was made.

Raw source responses, exact Komiic request bodies, complete live adapter results,
and six independent ordinary-directory checks are under
`output/discovery-expand-20260922/api-*`.
