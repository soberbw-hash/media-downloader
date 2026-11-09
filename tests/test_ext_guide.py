# -*- coding: utf-8 -*-
"""扩展安装引导回归：拖文件夹 + 图文三步（不依赖服务和浏览器）。

跑法：python tests/test_ext_guide.py
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import login  # noqa: E402

ok = 0


def log(*a):
    print(*a, flush=True)


def check(cond, msg):
    global ok
    assert cond, msg
    ok += 1
    log("  OK", msg)


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


log("=== 1. 图文三步 ===")
html = read("web/index.html")
panel = html[html.index('id="acctDrawer"'):html.index('id="histDrawer"')]
how = panel[panel.index('id="extHow"'):panel.index('</details>')]
check(how.count('class="gstep"') == 3, "三步各有一张卡片")
check(how.count('class="gpic"') == 3, "三步各有一张示意图（内联 SVG，不依赖外部图片）")
check(how.count("<svg") == 3, "示意图是内联 SVG，打包就带着")
check('class="gq"' in how, "「怎么装」是个带 ? 的小按钮")
check("拖进网页" in how, "明确告诉用户拖文件夹")
check("开发者模式" in how and "加载已解压的扩展程序" in how, "保留了官方两条安装路径")
check(".crx" not in how and "zip" not in how.lower() and "改后缀" not in how,
      "没让人去拖 Chrome 已拒收的 crx / 压缩包")
check("选中的文件夹" in how, "说的是刚被选中的那个文件夹，不用记英文目录名")
check('id="extFolder"' in how and 'id="extPage"' in how, "两个兜底按钮还在")
check("<p" not in how.replace("<path", ""), "没有说明段落 <p>")
FLUFF = ["不上传", "只读取", "只发到本机", "127.0.0.1", "无痕窗口",
         "不用管", "一次就够", "更省事", "什么都不用做"]
check(not [w for w in FLUFF if w in panel], "账号面板没混进废话")

css = read("web/style.css")
check(".gsteps" in css and ".gpic" in css and ".gt" in css, "图文样式已落地")
check(".ext-card .qbox > summary" in css, "小按钮样式只作用于扩展卡，不动别处")
check(".qbox[hidden]" in css, "CSS 的 [hidden] 兜底还在（flex 元素会失效）")

js = read("web/app.js")
check("文件夹已选中，拖进网页就行" in js, "一键按钮的反馈改成动作，不是「照三步」")
check("已选中扩展文件夹，拖进网页就行" in js, "只开文件夹的反馈也一致")

log("=== 2. 选中文件夹（不是打开进去） ===")
tmp = tempfile.mkdtemp(prefix="extguide_")
real_ext, real_popen, real_platform = login.EXT_DIR, login.subprocess.Popen, sys.platform
real_open = login.open_in_default_browser
saved_noopen = os.environ.pop("VDL_NO_OPEN", None)
calls = []
try:
    login.EXT_DIR = tmp
    login.subprocess.Popen = lambda args, *a, **k: calls.append(list(args))
    login.open_in_default_browser = lambda url: (True, "ok")

    sys.platform = "win32"
    okw, msgw = login.open_ext_dir()
    check(okw and calls and calls[-1][0] == "explorer.exe"
          and calls[-1][1].startswith("/select,"),
          "Windows 用 explorer /select, 选中文件夹本身")

    sys.platform = "darwin"
    okm, msgm = login.open_ext_dir()
    check(okm and calls[-1][0] == "open" and calls[-1][1] == "-R",
          "Mac 用 Finder 的 open -R 选中文件夹本身")

    sys.platform = "linux"
    okl, _ = login.open_ext_dir()
    check(okl and calls[-1][0] == "xdg-open", "Linux 退回打开上级目录，不报错")

    sys.platform = "darwin"
    oks, msgs = login.setup_ext()
    check(oks and "拖进网页" in msgs, "一键装小助手的返回语说清下一步动作")
    check("crx" not in msgs.lower(), "没有推荐会装不上的 crx")

    os.environ["VDL_NO_OPEN"] = "1"
    n = len(calls)
    okn, _ = login.open_ext_dir()
    check(okn and len(calls) == n, "测试环境下不会真弹窗口")
finally:
    login.EXT_DIR, login.subprocess.Popen = real_ext, real_popen
    login.open_in_default_browser = real_open
    sys.platform = real_platform
    if saved_noopen is None:
        os.environ.pop("VDL_NO_OPEN", None)
    else:
        os.environ["VDL_NO_OPEN"] = saved_noopen
    shutil.rmtree(tmp, ignore_errors=True)

log("=== 3. 扩展本体没被动过 ===")
import json  # noqa: E402
m = json.loads(read("browser-extension/manifest.json"))
check(m["manifest_version"] == 3, "还是 MV3（Chrome 已不收 MV2）")
check(os.path.isfile(os.path.join(ROOT, "browser-extension", "manifest.json")),
      "随包扩展目录完整，拖的就是它")

log("\n全部通过：%d 项" % ok)
