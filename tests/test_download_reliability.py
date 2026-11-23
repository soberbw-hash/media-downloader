# -*- coding: utf-8 -*-
"""Isolated Windows/Chrome acceptance: real local HTTP, forced exit, Range and history."""
import errno
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import core
import netdisk
from tools.make_clean_copy import copy_base

PAYLOAD = bytes(range(256)) * 16384


def until(fn, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = fn()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError('等待操作完成超时')


class Media(http.server.BaseHTTPRequestHandler):
    release = threading.Event()
    ranges = []

    def log_message(self, *args):
        pass

    drop_connection = True

    def do_GET(self):
        short = self.path.startswith('/document')
        disconnect = self.path.startswith('/disconnect') and type(self).drop_connection
        data = b'hello\n' if short else PAYLOAD
        rng = self.headers.get('Range')
        start = int(rng[6:-1]) if rng else 0
        if rng:
            self.ranges.append(start)
        self.send_response(206 if rng else 200)
        if rng:
            self.send_header('Content-Range', f'bytes {start}-{len(data)-1}/{len(data)}')
        self.send_header('Content-Length', str(len(data) - start))
        self.send_header('Content-Type', 'text/plain' if short else 'video/mp4')
        self.end_headers()
        try:
            self.wfile.write(data[start:start + 524288])
            self.wfile.flush()
            if disconnect:
                self.close_connection = True
                return
            if not short and not start:
                self.release.wait(30)
            self.wfile.write(data[start + 524288:])
        except OSError:
            pass


def check_netdisk(base, tmp):
    t = core.Task('1', 'https://pan.baidu.com/s/test', 'netdisk:baidu', '百度网盘')
    doc, other = {'name': '说明.txt', 'size': 6}, {'name': '失败.pdf'}
    info = {'title': '分享', 'files': [doc]}

    def direct(_info, item, _progress):
        if item is other:
            raise RuntimeError('需要登录')
        return base + '/document.bin', item['name'], item['size'], {}

    with patch.object(netdisk, 'resolve', return_value=info), patch.object(netdisk, 'get_direct', side_effect=direct):
        out, _ = core._netdisk_download(t, str(tmp), lambda **kw: None)
        assert Path(out).name == '说明.txt' and Path(out).read_bytes() == b'hello\n'
        info['files'] = [doc, other]
        folder, results = core._netdisk_download(t, str(tmp), lambda **kw: None)
        assert len(list(Path(folder).iterdir())) == 1 and results[1]['error'] and results[1]['size'] == 0
        info['files'] = [other, other]
        try:
            core._netdisk_download(t, str(tmp), lambda **kw: None)
        except RuntimeError as e:
            assert str(e) == '需要登录'
        else:
            raise AssertionError('全部下载失败不能报完成')
    assert core.task_failure(OSError(errno.ENOSPC, 'no space'), 'no space')[0] == 'directory'
    assert core.task_failure(RuntimeError(), '小红书登录过期了')[0] == 'login'
    assert core.task_failure(RuntimeError(), '分享已经失效了')[0] == 'bad_link'
    src, dst = tmp / 'copy_src', tmp / 'copy_dst'
    src.mkdir()
    (src / 'queue.json').write_text('private', encoding='utf-8')
    (src / 'queue.json.tmp').write_text('private', encoding='utf-8')
    copy_base(str(src), str(dst))
    assert not (dst / 'queue.json').exists() and not (dst / 'queue.json.tmp').exists()
    print('PASS 小文件、文件夹部分/全部失败、错误分类、队列不进入外发副本')


def check_browser(base, tmp):
    from playwright.sync_api import sync_playwright
    data, out = tmp / 'data', tmp / 'output'
    data.mkdir()
    (data / 'history.json').write_text('[{"id":"old","title":"旧记录","key":"old"}]', encoding='utf-8')
    (data / 'config.json').write_text(json.dumps({'out_dir': str(out), 'name_tpl': '{title}_验收'}), encoding='utf-8')
    portfile = data / 'port'
    env = dict(os.environ, VDL_DATA_DIR=str(data), VDL_PORT_FILE=str(portfile), VDL_FORCE_NEW='1',
               VDL_NO_AUTO_UPDATE='1', PYTHONIOENCODING='utf-8')

    def start():
        portfile.write_text('')
        p = subprocess.Popen([sys.executable, str(ROOT / 'server.py'), '--server'], env=env,
                             cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            port = until(lambda: portfile.read_text().strip())
            return p, 'http://127.0.0.1:' + port
        except Exception:
            p.kill()
            p.wait()
            raise

    p, app = start()
    try:
        with sync_playwright() as pw:
            chrome = pw.chromium.launch(channel='chrome', headless=True)
            context = chrome.new_context(permissions=['clipboard-read', 'clipboard-write'])
            page = context.new_page()
            page.goto(app, wait_until='domcontentloaded')
            source = base + '/fixture.mp4'
            page.locator('#autoPaste').uncheck(force=True)
            page.evaluate('''url => navigator.clipboard.writeText(url)''', source)
            page.locator('#input').press('Control+V')
            page.wait_for_function('url => document.querySelector("#input").value === url', arg=source)
            page.locator('#detected').wait_for(state='visible')
            with page.expect_response('**/api/add') as submitted:
                page.locator('#submit').click()
            assert len(submitted.value.json()['added']) == 1
            page.locator('.task.running').wait_for()
            duplicate = context.request.post(app + '/api/add', data={'text': source}).json()
            assert not duplicate['added'] and len(duplicate['skipped']) == 1
            part = until(lambda: next((f for f in out.glob('*.part') if f.stat().st_size), None))
            assert not list(out.glob('*.mp4'))
            original_size = part.stat().st_size
            p.kill()
            p.wait()
            Media.release.set()
            p, app = start()
            page.goto(app, wait_until='domcontentloaded')
            page.get_by_role('button', name='继续下载').wait_for()
            page.get_by_role('button', name='复制诊断').click()
            clip = page.evaluate('navigator.clipboard.readText()')
            assert 'interrupted' in clip and '/fixture.mp4' not in clip and '127.0.0.1' not in clip
            page.get_by_role('button', name='继续下载').click()
            page.locator('.task.done').wait_for()
            history = until(lambda: (h if len(h) == 2 else None)
                            if (h := json.load(urllib.request.urlopen(app + '/api/history'))['items']) else None)
            latest = Path(history[0]['path'])
            assert latest.read_bytes() == PAYLOAD and latest.stem.endswith('_验收')
            assert history[1]['title'] == '旧记录' and Media.ranges == [original_size]
            assert not list(out.glob('*.part'))
            request = context.request
            tid = history[0]['id']
            assert not request.post(app + '/api/retry', data={'id': tid}).json()['ok']
            assert not request.post(app + '/api/open', data={'id': 'missing'}).json()['ok']
            page.route('**/api/open', lambda route: route.fulfill(json={'ok': True}))
            with page.expect_request('**/api/open'):
                page.locator('.task.done .open').click()
            page.locator('#openHist').click()
            page.locator('#histList .hlocate').wait_for()
            assert page.locator('#histList .hitem').count() == 2
            assert '旧记录' in page.locator('#histList .hitem:not(.hlocate)').inner_text()
            assert page.locator('#histList .hlocate .hs').inner_text().count('·') >= 2
            page.locator('#histQ').fill('MP4 验收')
            page.locator('#histList .hitem').nth(1).wait_for(state='detached')
            with page.expect_request('**/api/open') as located:
                page.get_by_role('button', name='定位', exact=False).click()
            assert located.value.post_data_json == {'path': str(latest)}
            page.locator('#histQ').fill('不存在的搜索条件')
            page.get_by_text('没有匹配的下载记录').wait_for()
            missing = request.post(app + '/api/open', data={'path': str(latest) + '.missing'}).json()
            assert not missing['ok'] and '移动、改名或删除' in missing['error']
            page.locator('#closeHist').click()
            interrupted = base + '/disconnect.mp4'
            page.locator('#input').fill(interrupted)
            page.locator('#submit').click()
            page.locator('.task.error').wait_for()
            assert len(list(out.glob('*.part'))) == 1
            Media.drop_connection = False
            page.locator('.task.error .retry').click()
            page.wait_for_function('document.querySelectorAll(".task.done").length === 2')
            assert Media.ranges[-1] == 524288 and not list(out.glob('*.part'))
            recovered = request.get(app + '/api/history').json()['items']
            assert len(recovered) == 3 and recovered[-1]['title'] == '旧记录'
            assert Path(recovered[0]['path']).read_bytes() == PAYLOAD
            completed_task = request.get(app + '/api/state').json()['tasks'][0]
            page.locator('#clearBtn').click()
            page.locator('#tasksBlock').wait_for(state='hidden')
            assert len(request.get(app + '/api/history').json()['items']) == 3
            assert latest.read_bytes() == PAYLOAD
            print('PASS 主动粘贴、识别、重复提交去重、下载连接断开后界面重试，并且清理不删文件和历史')
            for code, label, target in [('login', '去登录', '#acctDrawer.on'),
                                        ('directory', '换目录', '#drawer.on'),
                                        ('bad_link', '换链接', '#input:focus')]:
                page.unroute('**/api/state')
                state = request.get(app + '/api/state').json()
                state['tasks'] = [dict(completed_task, status='error', error_code=code,
                                       err_title='测试失败提示', err_tip='测试下一步')]
                page.route('**/api/state', lambda route: route.fulfill(json=state))
                page.reload(wait_until='domcontentloaded')
                page.get_by_role('button', name=label).click()
                page.locator(target).wait_for()
            chrome.close()
    finally:
        p.kill()
        p.wait()
    print('PASS Chrome 提交 → 中断 → 强退 → 重开恢复 → 续传字节校验 → 最终命名、打开入口和历史保留')


if __name__ == '__main__':
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Media)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = 'http://127.0.0.1:%d' % server.server_port
        with tempfile.TemporaryDirectory(prefix='vdl_reliability_') as root:
            check_netdisk(base, Path(root))
            check_browser(base, Path(root))
    finally:
        Media.release.set()
        server.shutdown()
        server.server_close()
