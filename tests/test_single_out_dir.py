# -*- coding: utf-8 -*-
"""设置里只留一个「下载目录」：视频、音乐、Suno 全部下到同一个地方。

重点不是删界面那个框，而是老用户 config.json 里可能已经填过 music_dir，
不能让界面删了、后台还照旧把音乐下到另一个盘去。
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="vdl_onedir_")
os.environ["VDL_DATA_DIR"] = TMP

OK = [0]
FAIL = []


def check(cond, name):
    if cond:
        OK[0] += 1
        print("  ok  " + name)
    else:
        FAIL.append(name)
        print("  FAIL " + name)


def read(p):
    with open(os.path.join(ROOT, p), "r", encoding="utf-8") as f:
        return f.read()


print("=== 1. 界面只剩一个目录输入框 ===")
html = read("web/index.html")
js = read("web/app.js")
check('id="cfgOut"' in html and 'id="pickOut"' in html, "下载目录输入框和浏览按钮还在")
check("cfgMusicDir" not in html and "pickMusic" not in html,
      "音乐目录的输入框和浏览按钮已删")
check("音乐目录" not in html and "跟视频放一起" not in html,
      "界面上没有音乐目录相关文字")
check(html.count("path-row") == 1, "设置里只有一个路径行")
for token in ("cfgMusicDir", "pickMusic", "music_dir", "音乐目录"):
    check(token not in js, "app.js 里没有 %s" % token)

print("=== 2. 后端不再认识 music_dir ===")
server_src = read("server.py")
core_src = read("core.py")
copy_src = read("tools/make_clean_copy.py")
readme = read("README.md")
check('"music_dir"' not in server_src.split("def load_cfg")[0],
      "DEFAULT_CFG 里没有 music_dir")
check('"music_dir"' not in server_src.split('elif p == "/api/config":')[1].split("elif p ==")[0],
      "配置保存接口不再接受 music_dir")
check("cfg.pop(\"music_dir\", None)" in server_src, "读旧配置时丢掉残留的 music_dir")
check("music_dir" not in core_src, "下载内核完全不读 music_dir")
check("music_mod.download(t.url, out_dir" in core_src, "网易云等音乐走 out_dir")
check("suno.download(t.url, out_dir" in core_src, "Suno 走 out_dir")
check("data.pop(\"music_dir\", None)" in copy_src, "外发干净副本不带 music_dir")
check("music_dir" not in readme, "README 配置表不再列 music_dir")
check("视频和音乐都放这里" in readme, "README 说清一个目录管所有文件")

print("=== 3. 旧配置里填过的音乐目录不再生效 ===")
import server  # noqa: E402  （要先设好 VDL_DATA_DIR）

old_cfg = os.path.join(TMP, "old_config.json")
with open(old_cfg, "w", encoding="utf-8") as f:
    json.dump({"out_dir": os.path.join(TMP, "下载的文件"),
               "music_dir": os.path.join(TMP, "以前单独放音乐"),
               "quality": "720"}, f, ensure_ascii=False)
real_path = server.CONFIG_PATH
try:
    server.CONFIG_PATH = old_cfg
    loaded = server.load_cfg()
    check("music_dir" not in loaded, "load_cfg 丢掉旧配置里的 music_dir")
    check(loaded.get("quality") == "720", "别的旧设置照常读回来")
finally:
    server.CONFIG_PATH = real_path
check("music_dir" not in server.DEFAULT_CFG, "DEFAULT_CFG 里没有 music_dir")

print("=== 4. 真跑一遍：音乐和 Suno 都落到下载目录 ===")
import core  # noqa: E402

out_dir = os.path.join(TMP, "下载的文件")
os.makedirs(out_dir, exist_ok=True)
fake_music_dir = os.path.join(TMP, "以前单独放音乐")
os.makedirs(fake_music_dir, exist_ok=True)
# 故意保留旧键，模拟老用户没升级界面时的配置
cfg = dict(server.DEFAULT_CFG)
cfg["out_dir"] = out_dir
cfg["music_dir"] = fake_music_dir
cfg["name_tpl"] = ""
cfg["proxy"] = ""

calls = []


class _FakeMod(object):
    def __init__(self, tag):
        self.tag = tag

    def download(self, url, target, progress, quality, *a, **kw):
        calls.append((self.tag, target))
        return os.path.join(target, self.tag + ".mp3")


saved = {}
for name in ("music", "suno"):
    saved[name] = sys.modules.get(name)
sys.modules["music"] = _FakeMod("netease")
sys.modules["suno"] = _FakeMod("suno")
try:
    mgr = core.Manager(cfg)
    for tid, url, plat in ((1, "https://music.163.com/song?id=1", "netease"),
                           (2, "https://suno.com/s/abc", "suno")):
        t = core.Task(tid, url, plat, plat)
        mgr._run(t)
        check(t.status == "done", "%s 任务正常跑完" % plat)
finally:
    for name, old in saved.items():
        if old is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = old

check(len(calls) == 2, "音乐和 Suno 两条分支都走到了")
check(all(target == out_dir for _, target in calls),
      "两条分支都用的下载目录，没用旧 music_dir")
check(not os.listdir(fake_music_dir), "旧音乐目录里没有新文件")

print("\n=== %d 项通过，%d 项失败 ===" % (OK[0], len(FAIL)))
if FAIL:
    for f in FAIL:
        print("  - " + f)
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
