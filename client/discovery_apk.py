"""Public discovery lists verified against the installed APK source adapters.

Only metadata is read. Ranking selectors retain the published rank; catalogue
popularity sorts deliberately have no rank numbers. No remote JS is executed.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import re
import time
from urllib.parse import urljoin, urlsplit, urlencode, parse_qs

from . import apk_wap, apk_vomic_html, apk_dmzj, apk_kuaikan
from .discovery_common import DEADLINE, DiscoveryError, image_url, read_json, read_text, result, text
from .html_metadata import Element, image_of, known_status, parse_html, text_of
from .serialized_state import nuxt_state

_NAMES = {**{k: v[0] for k, v in apk_wap.SOURCES.items()},
          **{k: v[0] for k, v in apk_vomic_html.SOURCES.items()},
          'zaimanhua': '再漫画', 'kuaikan': '快看漫画'}
_ORIGINS = {**{k: 'https://' + v[1] for k, v in apk_wap.SOURCES.items()},
            **{k: 'https://' + v[1] for k, v in apk_vomic_html.SOURCES.items()},
            'zaimanhua': apk_dmzj.WEB_BASE, 'kuaikan': apk_kuaikan.ORIGIN}
_ORIGINS['cocoecar'] = 'https://keke2026.com'
_PERIODS = [('total', '总榜'), ('month', '月榜'), ('week', '周榜'), ('day', '日榜')]
_CDN = {**apk_vomic_html._IMAGE_HOSTS, **{k: apk_wap.IMAGE_DOMAINS for k in apk_wap.SOURCES},
        'zaimanhua': apk_dmzj.IMAGE_DOMAINS, 'kuaikan': apk_kuaikan.IMAGE_DOMAINS}
_CDN['manhua6'] = ('manhuagui.caiji2029.com', 'copyimg.caiji2029.com', 'ya2028.mzdtour.com', 'kanmancc.mzdtour.com')
IMAGE_DOMAINS = ('kanmancc.mzdtour.com',)
_EMPTY = Element('empty')
_MAX_PAGE = 1000


def _mode(kind, label, periods=(), max_page=1):
    return {'kind': kind, 'label': label, 'periods': [{'id': p, 'label': label} for p, label in periods], 'maxPage': max_page}


def sources():
    specs = {
        **{site: [_mode('popular', '人气排序', max_page=_MAX_PAGE),
                  _mode('latest', '最近更新', max_page=_MAX_PAGE)]
           for site in ('kanman', 'manhuatai', 'shenmanhua')},
        'mkzhan': [_mode('popular', '人气榜', [('week', '周榜'), ('month', '月榜'), ('total', '总榜')]), _mode('latest', '七日更新')],
        'manhua1234': [_mode('popular', '热门排行'), _mode('latest', '最近更新')],
        'cocoecar': [_mode('popular', '人气榜', _PERIODS), _mode('latest', '今日更新')],
        'guazimanhua': [_mode('popular', '人气榜', _PERIODS), _mode('latest', '最近更新', max_page=_MAX_PAGE)],
        'manhua6': [_mode('popular', '人气榜', [('year', '年度榜'), *_PERIODS[1:]]), _mode('latest', '七日更新', max_page=_MAX_PAGE)],
        'kuaikan': [_mode('popular', '人气榜')],
        'zaimanhua': [_mode('popular', '人气榜', [('week', '周榜'), ('month', '月榜'), ('total', '总榜')], max_page=_MAX_PAGE),
                     _mode('latest', '最近更新', max_page=_MAX_PAGE)],
    }
    return [{'siteId': site, 'siteName': _NAMES[site], 'modes': modes, 'coverLookup': False} for site, modes in specs.items()]


def _selection(site, kind, period, page):
    source = next((s for s in sources() if s['siteId'] == site), None)
    mode = next((m for m in source['modes'] if m['kind'] == kind), None) if source else None
    if not mode or period not in ([p['id'] for p in mode['periods']] or ['']):
        raise ValueError('此漫画源不支持所选发现列表')
    if type(page) is not int or not 1 <= page <= mode['maxPage']:
        raise ValueError('此发现列表不支持该页码')
    return mode


def _html(url):
    source = read_text(url, hosts=(urlsplit(url).hostname,))
    tree = parse_html(source)
    if re.search(r'安全验证|人机验证|访问验证|Just a moment|Attention Required', text_of(tree.first('title')), re.I) or 'cf-chl-' in source:
        raise DiscoveryError('来源要求访问验证，暂时无法读取列表')
    return tree


def _book(site, raw):
    url = urljoin(_ORIGINS[site] + '/', raw or '')
    p = urlsplit(url)
    if p.query and site not in {'guazimanhua', 'manhuatai', 'shenmanhua', 'zaimanhua'} or p.fragment:
        raise DiscoveryError('列表作品地址格式发生变化')
    try:
        if site in apk_vomic_html.SOURCES:
            return apk_vomic_html._book_url(site, url)
        if site == 'mkzhan':
            if p.scheme != 'https' or p.netloc != 'www.mkzhan.com' or not re.fullmatch(r'/[1-9]\d{0,11}/', p.path):
                raise ValueError
            return url
        if site == 'kuaikan':
            mid = apk_kuaikan._id(url)
            return f'{apk_kuaikan.ORIGIN}/web/topic/{mid}/'
    except ValueError as exc:
        raise DiscoveryError('列表作品地址格式发生变化') from exc
    raise DiscoveryError('未知的列表作品地址')


def _row(site, title, url, cover, *, latest='', author='', status='', update='', rank=None):
    row = {'title': text(title, 300), 'detailUrl': url, 'coverUrl': image_url(cover, _ORIGINS[site], _CDN[site]),
           'latestChapter': text(latest, 400), 'author': text(author, 300), 'status': known_status(text(status)), 'updatedAtText': text(update, 100)}
    if not row['title'] or not row['detailUrl']:
        raise DiscoveryError('来源列表缺少作品信息')
    if rank is not None:
        if not str(rank).isdigit() or int(rank) < 1:
            raise DiscoveryError('来源榜单名次格式发生变化')
        row['rank'] = int(rank)
    return row


def _finish(site, kind, period, page, rows, url, label, *, more=False, note='', allow_empty=False):
    if not rows and not allow_empty:
        raise DiscoveryError('来源没有返回可识别的作品列表，请稍后重试')
    return result(site, _NAMES[site], kind, period, page, rows, has_more=more, source_url=url, label=label, note=note)


def _millis_date(value):
    try:
        stamp = int(value)
        if stamp > 10**12:
            stamp /= 1000
        return datetime.fromtimestamp(stamp, timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M') if stamp > 0 else ''
    except (ValueError, TypeError, OverflowError, OSError):
        return ''


def _wap(site, kind, page):
    order = 'click' if kind == 'popular' else 'date'
    def endpoint(number):
        return apk_wap._wap_url(site, 'getsortlist', comic_sort='', orderby=order, search_type='', search_key='', page=number, size=48)
    url = endpoint(page)
    deadline = time.monotonic() + DEADLINE
    payload = read_json(url, hosts=('m.kanman.com',), deadline=deadline)
    if not isinstance(payload, dict) or str(payload.get('status')) != '0':
        raise DiscoveryError('来源没有返回有效列表')
    data = payload.get('data')
    if not isinstance(data, dict) or not isinstance(data.get('data'), list) or not isinstance(data.get('page'), dict):
        raise DiscoveryError('来源列表结构发生变化')
    pagination = data['page']
    if pagination.get('current_page') != page or pagination.get('orderby') != order:
        raise DiscoveryError('来源返回了其他排序或页码')
    total = pagination.get('total_page')
    if type(total) is not int or not 0 <= total <= 10000:
        raise DiscoveryError('来源列表分页发生变化')
    rows = []
    for value in data['data']:
        if not isinstance(value, dict) or not re.fullmatch(r'[1-9]\d{0,11}', str(value.get('comic_id'))):
            raise DiscoveryError('来源列表作品编号无效')
        rows.append(_row(site, value.get('comic_name'), apk_wap._book_url(site, str(value['comic_id']), value.get('comic_newid', '')),
                         value.get('cover_img'), latest=value.get('last_chapter_name'), author=value.get('comic_author'),
                         update=_millis_date(value.get('update_time')) if kind == 'latest' else ''))
    more = page < min(total, _MAX_PAGE)
    if site == 'kanman' and rows and page < _MAX_PAGE:
        # kmh filters the catalogue after pagination and reports the filtered
        # page size as its total. Its observed second page still contains new
        # books even when total_page=1, so use one bounded metadata lookahead.
        following = read_json(endpoint(page + 1), hosts=('m.kanman.com',), deadline=deadline)
        next_data = following.get('data') if isinstance(following, dict) else None
        if (not isinstance(following, dict) or str(following.get('status')) != '0' or not isinstance(next_data, dict)
                or not isinstance(next_data.get('data'), list)
                or not isinstance(next_data.get('page'), dict)
                or next_data.get('page', {}).get('current_page') != page + 1
                or next_data.get('page', {}).get('orderby') != order):
            raise DiscoveryError('来源下一页信息发生变化')
        ids = {str(value['comic_id']) for value in data['data']}
        if any(not isinstance(value, dict) or str(value.get('comic_id')) in ids for value in next_data['data']):
            raise DiscoveryError('来源分页重复，请稍后重试')
        more = bool(next_data['data'])
    source_url = 'https://m.kanman.com/' + ('sort/all.html' if kind == 'popular' else 'gengxin/') if site == 'kanman' else url
    return _finish(site, kind, '', page, rows, source_url, '人气排序' if kind == 'popular' else '最近更新', more=more,
                   note='已显示前 1000 页。' if page == _MAX_PAGE else '', allow_empty=page > 1 and page >= total)


def _vomic(site, kind, period, page):
    origin = _ORIGINS[site]
    if site == 'manhua1234':
        url = origin + ('/custom/top' if kind == 'popular' else '/custom/update')
    elif site == 'guazimanhua':
        params = ({'period': 'today' if period == 'day' else period} if period != 'total' else {}) if kind == 'popular' else {'date': 'all', 'page': page}
        url = origin + ('/top.php' if kind == 'popular' else '/update.php') + ('?' + urlencode(params) if params else '')
    else:
        url = origin + ('/custom/' + ({'year': 'hot', 'total': 'hot'}.get(period, period)) if kind == 'popular' else '/custom/update')
    root = _html(url)
    rows, more = [], False
    if site == 'manhua1234':
        scope = root.first(cls='comic-grid') or _EMPTY
        for node in scope.all(cls='comic-card'):
            link = node.first('a', cls='comic-card__link') or _EMPTY
            # This source labels the page a ranking but renders no ordinal.
            # Preserve its order without fabricating numeric ranks.
            rows.append(_row(site, text_of(node.first(cls='comic-card__title')), _book(site, link.attrs.get('href')), image_of(node, url), latest=text_of(node.first(cls='comic-card__chapter'))))
    elif site == 'cocoecar':
        scope = root.first(cls='cy_ph_list_mh' if kind == 'popular' else 'cy_list_mh') or _EMPTY
        for node in scope.children:
            if not isinstance(node, Element) or node.tag != 'ul':
                continue
            title = node.first(cls='title') or _EMPTY
            link = title.first('a') or _EMPTY
            latest = next((text_of(a) for a in node.all('a') if re.fullmatch(r'/chapter/\d+/?', a.attrs.get('href', ''))), '')
            rank = text_of(node.first('p')).removeprefix('Top.') if kind == 'popular' else None
            rows.append(_row(site, text_of(title), _book(site, link.attrs.get('href')), image_of(node, url), latest=latest, rank=rank))
    elif site == 'guazimanhua':
        if kind == 'popular':
            scope = root.first(cls='mobile-rank-grid') or _EMPTY
            for node in scope.all(cls='mobile-rank-card'):
                heading = node.first('h2') or _EMPTY
                link = heading.first('a') or _EMPTY
                rows.append(_row(site, text_of(heading), _book(site, link.attrs.get('href')), image_of(node, url), rank=text_of(node.first(cls='mobile-rank-no')).removeprefix('NO.')))
        else:
            scope = root.first(cls='mobile-update-grid') or _EMPTY
            for node in scope.all(cls='mobile-update-card'):
                heading = node.first('h2') or _EMPTY
                link = heading.first('a') or _EMPTY
                rows.append(_row(site, text_of(heading), _book(site, link.attrs.get('href')), image_of(node, url), latest=text_of(node.first('p'))))
            # The all-date source omits date=all in its next link.
            def next_page(link):
                target = urlsplit(urljoin(url, link.attrs.get('href', '')))
                params = parse_qs(target.query)
                return (text_of(link) == '下一页' and target.netloc == urlsplit(origin).netloc
                        and target.path == '/update.php' and params.get('page') == [str(page + 1)]
                        and params.get('date', ['all']) == ['all'])
            more = any(next_page(a) for a in root.all('a')) and page < _MAX_PAGE
    else:
        rows = _mkzhan_cards(site, root, url, kind, period)
    label = '最近更新' if kind == 'latest' else ('年度榜' if period == 'year' else dict(_PERIODS).get(period, '热门排行'))
    note = ''
    if site == 'manhua1234':
        note = '源站当前公开的单页列表。'
    elif kind == 'latest' and site == 'cocoecar':
        label, note = '今日更新', '源站当前公开的今日更新列表。'
    elif kind == 'latest' and site == 'manhua6':
        label, note = '七日更新', '按源站最近七日顺序显示，同一作品保留最近一条。'
        start = (page - 1) * 48
        more = start + 48 < len(rows)
        rows = rows[start:start + 48]
    elif kind == 'popular' and site == 'guazimanhua':
        note = '源站当前公开的单页榜单。'
    if page == _MAX_PAGE:
        note = '已显示前 1000 页。'
    return _finish(site, kind, period, page, rows, url, label, more=more, note=note)


def _mkzhan_cards(site, root, url, kind, period):
    rows = []
    if kind == 'popular':
        scopes = list(root.all(cls='top-list__box'))
        if len(scopes) != (3 if site == 'mkzhan' else 1):
            raise DiscoveryError('来源榜单分组结构发生变化')
        scope = scopes[{'week': 0, 'month': 1, 'total': 2}[period] if site == 'mkzhan' else 0]
        nodes = list(scope.all(cls='top-list__box-item'))
    else:
        scopes = list(root.all(cls='update-list'))
        if len(scopes) != 7:
            raise DiscoveryError('来源更新分组结构发生变化')
        nodes = [node for scope in scopes for node in scope.all(cls='common-comic-item')]
    seen = set()
    for node in nodes:
        heading = node.first(cls='comic__title') or _EMPTY
        link = heading.first('a') or _EMPTY
        detail = _book(site, link.attrs.get('href'))
        if detail in seen and kind == 'latest':
            continue
        seen.add(detail)
        rows.append(_row(site, text_of(heading), detail, image_of(node, url),
                         latest=text_of((node.first(cls='comic-update') or _EMPTY).first('a')),
                         author=text_of((node.first(cls='comic-author') or _EMPTY).first('a')),
                         rank=text_of(node.first(cls='tag')) if kind == 'popular' else None))
    return rows


def _mkzhan(kind, period):
    url = _ORIGINS['mkzhan'] + ('/top/popularity/' if kind == 'popular' else '/update/')
    rows = _mkzhan_cards('mkzhan', _html(url), url, kind, period)
    return _finish('mkzhan', kind, period, 1, rows, url, dict(_PERIODS).get(period, '七日更新'),
                   note='按源站最近七日顺序显示，同一作品保留最近一条。' if kind == 'latest' else '')


def _kuaikan():
    url = _ORIGINS['kuaikan'] + '/ranking/9'
    source = read_text(url, hosts=('www.kuaikanmanhua.com',))
    try:
        state = nuxt_state(source)
        entry = state['data'][0]
        payload = entry['topicData']
        data = payload['data']
        values = data['list']
        if entry['id'] != 9 or payload['code'] != 200 or data['rank_id'] != 9 or not isinstance(values, list):
            raise ValueError
    except (ValueError, IndexError, KeyError, TypeError) as exc:
        raise DiscoveryError('快看人气榜结构发生变化') from exc
    rows = []
    for value in values:
        if not isinstance(value, dict) or not re.fullmatch(r'[1-9]\d{0,11}', str(value.get('id'))):
            raise DiscoveryError('快看榜单作品编号无效')
        rows.append(_row('kuaikan', value.get('title'), f"{_ORIGINS['kuaikan']}/web/topic/{value['id']}/", value.get('vertical_image_url'),
                         author=(value.get('user') or {}).get('nickname'), latest=(value.get('latest_comic') or {}).get('title'), rank=len(rows)+1))
    return _finish('kuaikan', 'popular', '', 1, rows, url, text(data.get('title')) or '人气榜')


def _zaimanhua(page):
    url = apk_dmzj.API_BASE + f'/comic/update/list/0/{page}'
    payload = read_json(url, hosts=('v4api.zaimanhua.com',))
    if not isinstance(payload, dict) or payload.get('errno', 0) not in (0, '0') or not isinstance(payload.get('data'), list):
        raise DiscoveryError('再漫画更新列表结构发生变化')
    rows = []
    for value in payload['data']:
        if not isinstance(value, dict):
            raise DiscoveryError('再漫画更新作品信息无效')
        try:
            detail = apk_dmzj._book_url(value.get('comic_id'))
        except ValueError as exc:
            raise DiscoveryError('再漫画更新作品编号无效') from exc
        rows.append(_row('zaimanhua', value.get('title'), detail, value.get('cover'), latest=value.get('last_update_chapter_name'),
                         author=value.get('authors'), status=value.get('status'), update=_millis_date(value.get('last_updatetime'))))
        rows[-1]['tags'] = [tag for tag in re.split(r'[,，/、\s]+', text(value.get('types'))) if tag][:20]
    return _finish('zaimanhua', 'latest', '', page, rows, 'https://manhua.zaimanhua.com/update', '最近更新', more=len(rows) == 20 and page < _MAX_PAGE,
                   note='已显示前 1000 页。' if page == _MAX_PAGE else '', allow_empty=page > 1)


def _zai_popular(period, page):
    # Observed in the official rank UI: 1=week, 2=month, 3=total,
    # cate=1 is popularity. Public metadata endpoint needs no account/signature.
    url = 'https://manhua.zaimanhua.com/api/v1/comic1/rank_list?' + urlencode({
        'page': page, 'size': 20, 'duration': {'week': 1, 'month': 2, 'total': 3}[period], 'cate': 1, 'tag': 0, 'theme': 0})
    payload = read_json(url, hosts=('manhua.zaimanhua.com',))
    data = payload.get('data') if isinstance(payload, dict) else None
    if (not isinstance(payload, dict) or payload.get('errno') != 0 or not isinstance(data, dict)
            or not isinstance(data.get('list'), list) or type(data.get('totalNum')) is not int
            or not 0 <= data['totalNum'] <= 20000):
        raise DiscoveryError('再漫画人气榜结构发生变化')
    rows = []
    for value in data['list']:
        if not isinstance(value, dict):
            raise DiscoveryError('再漫画榜单作品信息无效')
        try:
            detail = apk_dmzj._book_url(value.get('comic_id'))
        except ValueError as exc:
            raise DiscoveryError('再漫画榜单作品编号无效') from exc
        rows.append(_row('zaimanhua', value.get('title'), detail, value.get('cover'),
                         latest=value.get('last_update_chapter_name'), author=value.get('authors'), status=value.get('status'),
                         rank=(page - 1) * 20 + len(rows) + 1))
        rows[-1]['tags'] = [tag for tag in re.split(r'[,，/、\s]+', text(value.get('types'))) if tag][:20]
    return _finish('zaimanhua', 'popular', period, page, rows, 'https://manhua.zaimanhua.com/rank', dict(_PERIODS)[period],
                   more=page * 20 < data['totalNum'] and page < _MAX_PAGE, allow_empty=page > 1 and page * 20 > data['totalNum'])


def fetch(site, kind, period='', page=1):
    _selection(site, kind, period, page)
    if site in {'kanman', 'manhuatai', 'shenmanhua'}:
        return _wap(site, kind, page)
    if site in apk_vomic_html.SOURCES:
        return _vomic(site, kind, period, page)
    if site == 'mkzhan':
        return _mkzhan(kind, period)
    if site == 'kuaikan':
        return _kuaikan()
    return _zaimanhua(page) if kind == 'latest' else _zai_popular(period, page)
