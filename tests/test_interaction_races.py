# -*- coding: utf-8 -*-
"""Chrome regression for explicit paste, late searches and filtered bulk actions."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from playwright.sync_api import sync_playwright
from test_download_reliability import until

ROOT = Path(__file__).resolve().parents[1]


def clipboard(page):
    result = page.evaluate('''async () => {
      let reads = 0, adds = 0, finish;
      const nativeFetch = window.fetch;
      Object.defineProperty(navigator.clipboard, 'readText', { configurable: true,
        value: async () => { reads++; return 'https://example.com/audio.mp3'; } });
      window.fetch = (url, options) => url === '/api/add' ?
        (++adds, new Promise(resolve => { finish = resolve; })) : nativeFetch(url, options);
      try {
        window.dispatchEvent(new Event('focus'));
        document.dispatchEvent(new Event('visibilitychange'));
        await new Promise(resolve => setTimeout(resolve, 220));
        const focus = {reads, adds, text: $('#toast').innerText};
        const data = new DataTransfer();
        data.setData('text/plain', 'https://example.com/audio.mp3');
        $('#input').dispatchEvent(new ClipboardEvent('paste', {clipboardData: data, bubbles: true, cancelable: true}));
        await new Promise(resolve => setTimeout(resolve, 40));
        const before = {adds, text: $('#toast').innerText};
        finish(new Response(JSON.stringify({added: [{platform_name: '测试'}]})));
        await new Promise(resolve => setTimeout(resolve, 40));
        return {focus, before, after: $('#toast').innerText};
      } finally { window.fetch = nativeFetch; delete navigator.clipboard.readText; }
    }''')
    assert result['focus']['reads'] == result['focus']['adds'] == 0, result
    assert result['before']['adds'] == 1 and '已加入' not in result['before']['text'], result
    assert '已加入' in result['after'], result


def searches(page):
    page.locator('[data-tab="music"]').click()
    page.locator('#mVPlats [data-p="youtube"]').wait_for(state='attached')
    problems = page.evaluate('''async () => {
      const nativeFetch = window.fetch, requests = [], issues = [];
      const btn = mode => $(`#mSrc [data-s="${mode}"]`);
      const query = $('#mQuery');
      const tick = () => new Promise(resolve => setTimeout(resolve, 0));
      function finish(request, marker) {
        const kind = request.mode === 'sfx' ? 'audio' : request.mode === 'video' ? 'video' : undefined;
        const platform = kind ? 'mixkit' : 'netease';
        request.resolve(new Response(JSON.stringify({ok:true, done:true,
          items:[{kind, platform, id: marker, title: marker, url:'https://example.com/'}]})));
      }
      window.fetch = (url, opts) => ['/api/music/search', '/api/search', '/api/movie/search'].includes(url) ?
        new Promise(resolve => requests.push({resolve, mode:mMode, body:JSON.parse(opts.body)})) : nativeFetch(url, opts);
      try {
        query.value = 'fixture';
        for (const mode of ['music', 'sfx', 'video', 'movie']) btn(mode).click();
        const [music, sfx, video, movie] = requests;
        finish(movie, 'CURRENT-MOVIE'); await tick();
        for (const r of [video, sfx, music]) { finish(r, 'STALE'); await tick(); }
        if (!$('#mResults').textContent.includes('CURRENT-MOVIE') || mItems[0]?.title !== 'CURRENT-MOVIE')
          issues.push('older modes replaced movie state');
        btn('video').click();
        $('#mVPlats [data-p="youtube"]').click();
        const [oldSource, newSource] = requests.slice(-2);
        if (newSource.body.sources[0] !== 'youtube') issues.push('source did not change');
        finish(newSource, 'CURRENT-SOURCE'); await tick();
        finish(oldSource, 'STALE-SOURCE'); await tick();
        if (!$('#mResults').textContent.includes('CURRENT-SOURCE')) issues.push('older source replaced new source');
        musicSearch();
        const old = requests.at(-1);
        query.value = ''; query.dispatchEvent(new Event('input'));
        finish(old, 'STALE-CLEARED'); await tick();
        if ($('#mResults').textContent.trim() || $('#mGo').disabled) issues.push('cleared results returned');
        return issues;
      } finally { window.fetch = nativeFetch; }
    }''')
    assert not problems, problems


def filters(page):
    result = page.evaluate('''() => {
      const make = (id, status, extra = {}) => ({id, status, title:id, url:'https://example.com/', ...extra});
      render([make('live', 'running'), make('failed', 'error'), make('done', 'done'),
        make('partial', 'partial', {kind:'files', count:2, images:[{name:'ok'},{name:'bad',error:'失败'}]}),
        make('canceled', 'canceled')]);
      $('#filters [data-f="error"]').click();
      const snapshot = () => [...document.querySelectorAll('#tasks .task:not([hidden])')].map(el => el.dataset.id);
      const error = {ids:snapshot(), cancelHidden:$('#cancelAll').hidden, clearHidden:$('#clearBtn').hidden};
      const p = $('#filters [data-f="partial"]');
      p?.click();
      const partial = {ids:snapshot(), count:p?.dataset.n, text:$('#tasks .partial .status').textContent};
      $('#filters [data-f="all"]').click();
      return {error, partial, clear:$('#clearBtn').textContent};
    }''')
    assert result['error'] == {'ids': ['failed'], 'cancelHidden': True, 'clearHidden': True}, result
    assert result['partial']['ids'] == ['partial'] and result['partial']['count'] == '1', result
    assert result['partial']['text'] == '部分完成 · 已下 1 个 · 1 个失败', result
    assert '结束' in result['clear'], result


def layout(page):
    page.wait_for_function('Boolean(appVersion)')
    tasks = page.evaluate('''() => ['running', 'done', 'partial', 'error'].map((status, i) => ({id:String(i), status,
      kind:'files', count:2, title:'较长的测试任务文件名 · 测试文件.mp4', url:'https://example.com/',
      images:[{name:'ok'},{name:'bad',error:'失败'}]}))''')
    page.route('**/api/state', lambda route: route.fulfill(json={'tasks': tasks}))
    page.evaluate('poll()')
    def fits(selector):
        box = page.locator(selector).bounding_box()
        assert box and box['x'] >= 0 and box['x'] + box['width'] <= page.viewport_size['width'] + 1, (selector, box)
    for width in (1180, 420):
        page.set_viewport_size({'width': width, 'height': 800 if width > 500 else 660})
        for selector in ('#input', '#submit', '.list-head', '#filters', '.lh-right', '.task.partial .open'):
            fits(selector)
        page.locator('[data-tab="music"]').click()
        for selector in ('#mSrc', '#mQuery', '#mGo'):
            fits(selector)
        page.locator('#openSet').click()
        page.locator('#closeSet:focus').wait_for()
        fits('#drawer .mcard')
        page.locator('#openAcct').click()
        page.locator('#closeAcct:focus').wait_for()
        fits('#acctDrawer .mcard')
        page.evaluate('''() => { extInfo = {stale: true, synced: true, exists: true}; renderExt(); }''')
        page.get_by_role('button', name='更新扩展', exact=True).click()
        page.wait_for_function('''() => {
          const tip = $('#toast.on');
          if (!tip || !tip.textContent.includes('扩展页') || +getComputedStyle(tip).opacity < .95) return false;
          const rect = tip.getBoundingClientRect();
          tip.style.pointerEvents = 'auto';
          const top = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
          tip.style.pointerEvents = '';
          return tip.contains(top);
        }''', timeout=3000)
        page.locator('#closeAcct').click()
        page.locator('#openSet:focus').wait_for()
        page.locator('[data-tab="video"]').click()


def account_grid(page):
    sites = [{'id': pid, 'name': name, 'required': i < 8,
              'logged_in': i in (0, 3), 'tip': ''}
             for i, (pid, name) in enumerate([
                 ('xhs', '小红书'), ('instagram', 'Instagram'), ('music_netease', '网易云音乐'),
                 ('baidu', '百度网盘'), ('quark', '夸克网盘'), ('uc', 'UC网盘'),
                 ('xunlei', '迅雷网盘'), ('115', '115网盘'), ('douyin', '抖音'),
                 ('kuaishou', '快手'), ('bili', 'B站'), ('weibo', '微博')])]
    sites[2]['logged_in'] = True
    sites[2]['account'] = {'ok': True, 'name': '一个很长的网易云账号昵称', 'vip': True, 'detail': '黑胶VIP'}
    page.route('**/api/login/state', lambda route: route.fulfill(json={
        'ok': True, 'sites': sites, 'status': {}, 'default_browser': None, 'extension': None}))
    page.evaluate('''sites => { renderLogins(sites, {}); openAcct(); }''', sites)
    page.wait_for_function('document.querySelectorAll("#logins .login-row").length === 12')
    page.locator('#acctDrawer.on').wait_for()
    wide = page.evaluate('''() => {
      const list = document.querySelector('.lg-list');
      const rows = [...list.querySelectorAll('.login-row')];
      const rect = list.getBoundingClientRect(), r0 = rows[0].getBoundingClientRect(), r1 = rows[1].getBoundingClientRect();
      return {cols: getComputedStyle(list).gridTemplateColumns.split(' ').length,
        share: r0.width / rect.width, sameRow: Math.abs(r0.top - r1.top) < 2};
    }''')
    assert wide['cols'] == 2 and wide['sameRow'] and 0.4 < wide['share'] < 0.6, wide
    short = page.evaluate('''() => {
      const row = [...document.querySelectorAll('.login-row')]
        .find(r => r.querySelector('.nm-p').textContent === '网易云');
      const acc = row && row.querySelector('.acc');
      return {has: !!row, full: row && row.textContent.includes('网易云音乐'),
        accText: acc && acc.textContent, accTitle: acc && acc.title};
    }''')
    assert short['has'] and not short['full'], short
    assert short['accText'] == '一个很长的网易云账号昵称' and '一个很长的网易云账号昵称' in short['accTitle'], short
    page.set_viewport_size({'width': 420, 'height': 720})
    narrow = page.evaluate('''() => {
      const list = document.querySelector('.lg-list');
      const rows = [...list.querySelectorAll('.login-row')];
      return getComputedStyle(list).gridTemplateColumns.split(' ').length;
    }''')
    assert narrow == 1, narrow
    print('  账号列表宽窗两列、窄窗一列', flush=True)


def ext_copy(page):
    result = page.evaluate('''async () => {
      Object.defineProperty(navigator.clipboard, 'writeText', {configurable:true,
        value: async t => { window.__copied = t; }});
      window.__copied = null;
      extInfo = {stale: true, synced: true, exists: true};
      renderExt();
      $('#btnExtSetup').click();
      await new Promise(r => setTimeout(r, 30));
      const tip = $('#toast.on');
      const text = tip.textContent;
      const autoCopied = window.__copied;
      // 逐字量坐标，按渲染行分组，看最后一行是不是只剩一个字
      const node = [...tip.childNodes].find(n => n.nodeType === 3);
      const range = document.createRange(), lines = {};
      for (let i = 0; i < node.length; i++) {
        range.setStart(node, i); range.setEnd(node, i + 1);
        const top = Math.round(range.getBoundingClientRect().top);
        (lines[top] ||= []).push(node.textContent[i]);
      }
      const lastLine = lines[Object.keys(lines).sort((a,b)=>a-b).pop()].join('');
      window.__copied = null;
      tip.querySelector('button').click();
      await new Promise(r => setTimeout(r, 30));
      return {text, lastLine, autoCopied, recopied: window.__copied, after: $('#toast').textContent};
    }''')
    assert 'chrome://' not in result['text'], result
    assert len(result['lastLine']) >= 2, '孤字独占一行: %r' % result['lastLine']
    assert result['autoCopied'] == 'chrome://extensions', '弹提示时应自动复制: %s' % result
    assert result['recopied'] == 'chrome://extensions', '再复制按钮应仍可用: %s' % result
    assert '已复制' in result['text'] and '已复制' in result['after'], result
    fallback = page.evaluate('''async () => {
      Object.defineProperty(navigator.clipboard, 'writeText', {configurable:true,
        value: async () => { throw new Error('denied'); }});
      $('#btnExtSetup').click();
      await new Promise(r => setTimeout(r, 40));
      const tip = $('#toast.on'), btn = tip.querySelector('button');
      return {text: tip.textContent, label: btn && btn.textContent};
    }''')
    assert 'chrome://' not in fallback['text'], fallback
    assert fallback['label'] == '复制地址', '复制失败应退回手动按钮: %s' % fallback
    print('  扩展提示：弹出即自动复制、可再复制、失败可手动、文案无长链接无孤字', flush=True)


def history_layout(page):
    """长标题不能溢出、不能把「定位」挤走或让条目变高。"""
    items = [
        {'title': '短视频分享', 'platform_name': '抖音', 'author': '张三',
         'size': 1234567, 'done_at': 1731198000, 'path': 'C:/a.mp4'},
        {'title': '今日专家又掘出玄武门之变的监控资料 还原玄武门之变真实画面'
                  '#用ai打开人文新世界 #创作灵感 #历史 #唐朝',
         'platform_name': '抖音', 'author': '大橘放映厅',
         'size': 11800000, 'done_at': 1731198000, 'path': 'C:/b.mp4'},
        {'title': '"觉醒年代"导演！2026重磅史诗巨制《伟大的长征》预告，'
                  '豪华阵容！定档10.09 B站',
         'platform_name': 'B站', 'author': '乌鸦预告片',
         'size': 3500000, 'done_at': 1731198000, 'path': 'C:/c.mp4'},
        {'title': '另一个正常长度的标题', 'platform_name': '小红书',
         'author': '空恋', 'size': 1600000, 'done_at': 1731198000, 'path': 'C:/d.mp4'},
    ]
    page.route('**/api/history*', lambda route: route.fulfill(json={'items': items}))
    page.evaluate('openHist()')
    page.wait_for_function('document.querySelectorAll("#histList .hitem").length === 4')
    info = page.evaluate('''() => {
      const rows = [...document.querySelectorAll('#histList .hitem')];
      const data = rows.map(r => {
        const ht = r.querySelector('.ht'), hs = r.querySelector('.hs');
        const meta = r.querySelector('.hmeta'), loc = r.querySelector('.hlocate-label');
        const mb = meta.getBoundingClientRect(), tb = ht.getBoundingClientRect();
        const sb = hs.getBoundingClientRect(), lb = loc.getBoundingClientRect();
        const cs = getComputedStyle(ht);
        return {h: r.getBoundingClientRect().height,
                titleRight: tb.right, metaRight: mb.right,
                titleTop: tb.top, subTop: sb.top,
                titleDisplay: cs.display, titleOverflow: cs.overflow,
                clipped: ht.scrollWidth > ht.clientWidth + 1,
                locRight: lb.right, locTop: lb.top};
      });
      const card = document.querySelector('#histDrawer .mcard').getBoundingClientRect();
      return {data, cardRight: card.right};
    }''')
    rows = info['data']
    heights = [r['h'] for r in rows]
    assert max(heights) - min(heights) <= 2, '条目高度不一致: %s' % heights
    for i, r in enumerate(rows):
        assert r['titleRight'] <= r['metaRight'] + 2, \
            '第%d条标题溢出压到定位按钮: %s' % (i + 1, r)
        assert r['titleTop'] < r['subTop'], \
            '第%d条标题和副标题不在两行: %s' % (i + 1, r)
        assert r['titleDisplay'] != 'inline' and r['titleOverflow'] == 'hidden', \
            '第%d条标题没有省略能力: %s' % (i + 1, r)
        assert r['locRight'] <= info['cardRight'] - 8, \
            '第%d条定位按钮超出弹窗: %s' % (i + 1, r)
    locs = [r['locRight'] for r in rows]
    assert max(locs) - min(locs) <= 2, '定位按钮没右对齐: %s' % locs
    assert rows[1]['clipped'], '超长标题应该被省略而不是撑开: %s' % rows[1]
    print('  下载历史：长标题省略、条目等高、定位右对齐', flush=True)


def main():
    with tempfile.TemporaryDirectory(prefix='vdl_ui_') as folder:
        tmp = Path(folder)
        portfile = tmp / 'port'
        (tmp / 'config.json').write_text(json.dumps({'out_dir': str(tmp / 'downloads')}), encoding='utf-8')
        env = dict(os.environ, VDL_DATA_DIR=str(tmp), VDL_PORT_FILE=str(portfile),
                   VDL_FORCE_NEW='1', VDL_NO_OPEN='1', VDL_NO_AUTO_UPDATE='1')
        with subprocess.Popen([sys.executable, str(ROOT / 'server.py'), '--server'], cwd=ROOT, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as srv:
            try:
                port = until(lambda: portfile.read_text().strip() if portfile.exists() else None)
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(channel='chrome')
                    failures = []
                    for test in (clipboard, searches, filters, layout, account_grid, ext_copy, history_layout):
                        page = browser.new_page(viewport={'width': 1180, 'height': 800})
                        page.goto('http://127.0.0.1:' + port, wait_until='domcontentloaded')
                        try:
                            test(page)
                            print('PASS', test.__name__, flush=True)
                        except AssertionError as exc:
                            failures.append(f'{test.__name__}: {exc}')
                        finally:
                            page.close()
                    browser.close()
                    assert not failures, '\n'.join(failures)
            finally:
                srv.terminate()
                srv.wait(timeout=10)


if __name__ == '__main__':
    main()
