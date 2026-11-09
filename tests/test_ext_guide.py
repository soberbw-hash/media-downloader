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
check(how.count('class="gstep"') == 4, "四步各有一张卡片")
check(how.count('class="gpic"') == 4, "四步各有一张图")
check(how.count("<svg") == 0, "图换成了真实截图，不再是手画示意图")
for _n in ("ext-step1", "ext-step2", "ext-step3", "ext-step4"):
    check(_n + ".png" in how, "第 %s 步指向自己的截图" % _n[-1])
check('data-full="/img/ext-page.png"' in how and 'data-full="/img/ext-page-menu.png"' in how,
      "点开能看整页大图（知道目标在页面哪个角落）")
for _n in ("ext-step1", "ext-step2", "ext-step3", "ext-step4",
           "ext-page", "ext-page-menu"):
    _p = os.path.join(ROOT, "web", "img", _n + ".png")
    # 门槛只用于确认「不是空白图」；step1 只裁了那颗按钮，几 KB 是正常的
    check(os.path.isfile(_p) and os.path.getsize(_p) > 1500,
          "截图 %s 在包里（%d 字节）" % (_n, os.path.getsize(_p) if os.path.isfile(_p) else 0))
check('class="gq"' in how, "「怎么装」是个带 ? 的小按钮")
check("加载未打包的扩展程序" in how, "按钮名用新版 Chrome 的叫法")
check("旧版叫" in how and "加载已解压" in how, "老版本叫法也给了，免得找不到")
check("开发者模式" in how and "右上角" in how, "说清了开发者模式在右上角")
check("管理扩展程序" in how or "扩展程序" in how, "给了从浏览器菜单进扩展页的另一条路")
check("左上角" in how, "说清了加载按钮在左上角")
check(".crx" not in how and "zip" not in how.lower() and "改后缀" not in how,
      "没让人去拖 Chrome 已拒收的 crx / 压缩包")
check("选中那个文件夹" in how, "说的是刚被选中的那个文件夹，不用记英文目录名")
check('id="extFolder"' not in how and 'id="extPage"' not in how,
      "点不开的「只开文件夹 / 只开安装页」已删掉")
check("ext-acts small" not in how, "没有残留的次要按钮行")
check("<p" not in how.replace("<path", ""), "没有说明段落 <p>")
FLUFF = ["不上传", "只读取", "只发到本机", "127.0.0.1", "无痕窗口",
         "不用管", "一次就够", "更省事", "什么都不用做"]
check(not [w for w in FLUFF if w in panel], "账号面板没混进废话")

css = read("web/style.css")
check(".gsteps" in css and ".gpic" in css and ".gt" in css, "图文样式已落地")
check(".ext-card .qbox > summary" in css, "小按钮样式只作用于扩展卡，不动别处")
check(".qbox[hidden]" in css, "CSS 的 [hidden] 兜底还在（flex 元素会失效）")

js = read("web/app.js")
check("toast(r.msg" in js, "一键按钮原样显示服务端给的动作（Mac 会说 Cmd+V）")
check('$("#extPage")' not in js and '$("#extFolder")' not in js,
      "前端不再绑定那两个点不开的按钮")
check("地址栏粘上" in how and "文件夹会选中" in how,
      "图文说清了粘贴地址这一步")

log("=== 2. 选中文件夹 + 备好扩展页地址 ===")
tmp = tempfile.mkdtemp(prefix="extguide_")
real_ext, real_popen, real_platform = login.EXT_DIR, login.subprocess.Popen, sys.platform
real_chromium = login.chromium_exe
real_copy = login.copy_to_clipboard
real_startfile = getattr(login.os, "startfile", None)
saved_noopen = os.environ.pop("VDL_NO_OPEN", None)
calls, startfiles, copied = [], [], []
try:
    login.EXT_DIR = tmp
    login.subprocess.Popen = lambda args, *a, **k: calls.append(list(args))
    login.copy_to_clipboard = lambda text: (copied.append(text), True)[1]
    login.chromium_exe = lambda: "C:/Fake/Chrome/chrome.exe"
    if real_startfile is not None:
        login.os.startfile = lambda url: startfiles.append(url)

    sys.platform = "win32"
    okw, _ = login.open_ext_dir()
    check(okw and calls and calls[-1][0] == "explorer.exe"
          and calls[-1][1].startswith("/select,"),
          "Windows 用 explorer /select, 选中文件夹本身")

    okp, msgp = login.open_ext_page()
    check(okp and copied[-1] == "chrome://extensions/",
          "扩展页地址进了剪贴板")
    check(calls[-1] == ["C:/Fake/Chrome/chrome.exe", "about:blank"],
          "只开空白标签页，不再把 chrome:// 硬塞给 Chrome")
    check(not startfiles,
          "不再走 os.startfile（会弹「去应用商店找应用」）")
    check("Ctrl+V" in msgp, "Windows 提示按 Ctrl+V")
    check(not login.open_in_default_browser("chrome://extensions/")[0],
          "系统关联入口已挡住内部页地址")

    login.chromium_exe = lambda: "C:/Fake/Edge/msedge.exe"
    oke, _ = login.open_ext_page()
    check(oke and copied[-1] == "edge://extensions/",
          "Edge 复制的是 edge:// 地址")

    login.chromium_exe = lambda: ""
    oknone, msgnone = login.open_ext_page()
    check(not oknone and "Chrome 或 Edge" in msgnone,
          "没装 Chromium 时给个出路，不假装打开成功")

    login.chromium_exe = lambda: "C:/Fake/Chrome/chrome.exe"
    login.copy_to_clipboard = lambda text: False
    oknc, msgnc = login.open_ext_page()
    check(oknc and "地址栏输入" in msgnc and "chrome://extensions/" in msgnc,
          "剪贴板不可用时直接把地址写出来")
    login.copy_to_clipboard = lambda text: (copied.append(text), True)[1]

    sys.platform = "darwin"
    okm, _ = login.open_ext_dir()
    check(okm and calls[-1][0] == "open" and calls[-1][1] == "-R",
          "Mac 用 Finder 的 open -R 选中文件夹本身")

    app = (tmp + "/Google Chrome.app").replace("\\", "/")
    os.makedirs(os.path.join(app, "Contents", "MacOS"))
    fake = app + "/Contents/MacOS/Google Chrome"
    with open(fake, "w") as f:
        f.write("")
    login.chromium_exe = lambda: fake
    okmp, msgmp = login.open_ext_page()
    check(okmp and calls[-1][0] == "open" and calls[-1][1] == "-a"
          and calls[-1][2] == app and calls[-1][3] == "about:blank",
          "Mac 用 open -a 开空白标签页")
    check("Cmd+V" in msgmp, "Mac 提示按 Cmd+V")
    login.chromium_exe = lambda: "C:/Fake/Chrome/chrome.exe"

    sys.platform = "linux"
    okl, _ = login.open_ext_dir()
    check(okl and calls[-1][0] == "xdg-open", "Linux 退回打开上级目录，不报错")
    oklp, _ = login.open_ext_page()
    check(oklp and calls[-1][1] == "about:blank",
          "Linux 同样只开空白标签页")

    sys.platform = "darwin"
    oks, msgs = login.setup_ext()
    check(oks and "回车" in msgs, "一键装小助手的返回语说清下一步动作")
    check("文件夹已选中" in msgs, "顺便告诉用户文件夹也选好了")
    check("crx" not in msgs.lower(), "没有推荐会装不上的 crx")
    check(copied[-1] == "chrome://extensions/" and calls[-1][1] == "about:blank",
          "主按钮真的备好了地址和标签页")

    os.environ["VDL_NO_OPEN"] = "1"
    n = len(calls)
    okn, _ = login.open_ext_dir()
    okq, _ = login.open_ext_page()
    check(okn and okq and len(calls) == n, "测试环境下不会真弹窗口")
finally:
    login.EXT_DIR, login.subprocess.Popen = real_ext, real_popen
    login.chromium_exe = real_chromium
    login.copy_to_clipboard = real_copy
    if real_startfile is not None:
        login.os.startfile = real_startfile
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
