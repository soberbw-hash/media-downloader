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
how = panel[panel.index('id="extHow"'):panel.index('id="logins"')]
check(how.count('class="gstep"') == 3, "三步各有一张卡片")
check(how.count('class="gpic"') == 3, "三步各有一张图")
check(how.count("<svg") <= 1, "图换成了真实截图，不再是手画示意图（只剩一个折叠箭头）")
for _n in ("ext-step2", "ext-step3", "ext-step4"):
    check(_n + ".png" in how, "第 %s 步指向自己的截图" % _n[-1])
check("ext-step1" not in how, "「点按钮」不再是第一步（按钮就在上面，点了才看到这页）")
check('data-full="/img/ext-page.png"' in how and 'data-full="/img/ext-page-menu.png"' in how,
      "点开能看整页大图（知道目标在页面哪个角落）")
for _n in ("ext-step2", "ext-step3", "ext-step4", "ext-page", "ext-page-menu"):
    _p = os.path.join(ROOT, "web", "img", _n + ".png")
    check(os.path.isfile(_p) and os.path.getsize(_p) > 1500,
          "截图 %s 在包里（%d 字节）" % (_n, os.path.getsize(_p) if os.path.isfile(_p) else 0))
check("egToggle" in how and "egBody" in how,
      "怎么装可折叠：默认收起，点一下展开")
check("aria-expanded" in how, "折叠按钮带 aria-expanded 可达性标记")
check("点开看" not in how, "没有「点开看四步」这种已经点开还让人点的字")
check(how.index("管理扩展程序") < how.index("开发者模式") < how.index("加载未打包"),
      "顺序是：进扩展页 → 开发者模式 → 加载文件夹")
check("地址栏" not in how, "全程不教往浏览器地址栏里粘东西")
_steps = how.split('class="gstep"')          # [前缀, 第1步, 第2步, 第3步]
check("粘贴" not in _steps[1] and "Ctrl" not in _steps[1],
      "第一步只走浏览器菜单，不提复制粘贴")
check("粘贴" in _steps[3] and "Ctrl+V" in _steps[3],
      "第三步说了在加载弹窗里粘贴（Ctrl+V）地址直达")
check("选中刚才那个扩展文件夹" in how, "说的是刚选中的那个文件夹，不用记英文目录名")
check("加载未打包的扩展程序" in how, "按钮名用新版 Chrome 的叫法")
check("旧版叫" in how and "加载已解压" in how, "老版本叫法也给了，免得找不到")
check("开发者模式" in how and "右上角" in how, "说清了开发者模式在右上角")
check("管理扩展程序" in how or "扩展程序" in how, "给了从浏览器菜单进扩展页的另一条路")
check("左上角" in how, "说清了加载按钮在左上角")
check(".crx" not in how and "zip" not in how.lower() and "改后缀" not in how,
      "没让人去拖 Chrome 已拒收的 crx / 压缩包")
check("选中扩展文件夹" in panel and "选中刚才那个扩展文件夹" in how,
      "按钮名和第三步说的是同一个东西")
check('id="extFolder"' not in how and 'id="extPage"' not in how,
      "点不开的「只开文件夹 / 只开安装页」已删掉")
check("ext-acts small" not in how, "没有残留的次要按钮行")
check("<p" not in how.replace("<path", ""), "没有说明段落 <p>")
FLUFF = ["不上传", "只读取", "只发到本机", "127.0.0.1", "无痕窗口",
         "不用管", "一次就够", "更省事", "什么都不用做"]
check(not [w for w in FLUFF if w in panel], "账号面板没混进废话")

css = read("web/style.css")
check(".gsteps" in css and ".gpic" in css and ".gt" in css, "图文样式已落地")
check(".ext-guide" in css and ".eg-h" in css, "常驻的「怎么装」标题有自己的样式")
check(".ext-guide[hidden]" in css, "装好后整块能藏起来（flex 会吃掉 [hidden]）")

js = read("web/app.js")
check("toast(r.msg" in js, "一键按钮原样显示服务端给的下一步")
check('$("#extPage")' not in js and '$("#extFolder")' not in js,
      "前端不再绑定那两个点不开的按钮")
check("在浏览器右上角点" in how and "管理扩展程序" in how,
      "第一步写清了怎么进扩展页")

log("=== 2. 一键装 = 选中文件夹 + 复制地址 ===")
tmp = tempfile.mkdtemp(prefix="extguide_")
real_ext, real_popen, real_platform = login.EXT_DIR, login.subprocess.Popen, sys.platform
real_copy = login.copy_to_clipboard
saved_noopen = os.environ.pop("VDL_NO_OPEN", None)
calls, copied = [], []
try:
    login.EXT_DIR = tmp
    login.subprocess.Popen = lambda args, *a, **k: calls.append(list(args))
    login.copy_to_clipboard = lambda t: (copied.append(t), True)[1]

    sys.platform = "win32"
    okw, _ = login.open_ext_dir()
    check(okw and calls[-1][0] == "explorer.exe"
          and calls[-1][1].startswith("/select,"),
          "Windows 用 explorer /select, 选中文件夹本身")

    oks, msgs = login.setup_ext()
    check(oks and "文件夹已选中" in msgs, "一键装把文件夹选中")
    check("扩展程序" in msgs and "管理扩展程序" in msgs,
          "下一步给的是浏览器菜单那条路")
    check("地址已复制" in msgs and "粘贴" in msgs and "Ctrl+V" in msgs,
          "顺手复制地址，提示在加载弹窗里粘贴（Ctrl+V）直达")
    check(copied and os.path.normpath(copied[-1]) == os.path.normpath(tmp),
          "放进剪贴板的就是扩展文件夹的路径")
    check(len(calls) == 2, "只开文件管理器：不弹浏览器")
    check(hasattr(login, "copy_to_clipboard") and not hasattr(login, "open_ext_page"),
          "复制的是文件夹地址，不自动开安装页（chrome:// 外部程序打不开）")
    check(not login.open_in_default_browser("chrome://extensions/")[0],
          "系统关联入口仍然挡住内部页地址")

    login.copy_to_clipboard = lambda t: False
    okf, msgf = login.setup_ext()
    check(okf and "地址已复制" not in msgf and "管理扩展程序" in msgf,
          "复制失败时不假称已复制，仍指路浏览器菜单")
    login.copy_to_clipboard = lambda t: (copied.append(t), True)[1]

    sys.platform = "darwin"
    okm, _ = login.open_ext_dir()
    check(okm and calls[-1][0] == "open" and calls[-1][1] == "-R",
          "Mac 用 Finder 的 open -R 选中文件夹本身")
    oksm, msgm = login.setup_ext()
    check(oksm and "Cmd+V" in msgm, "Mac 上提示用 Cmd+V 粘贴")

    sys.platform = "linux"
    okl, _ = login.open_ext_dir()
    check(okl and calls[-1][0] == "xdg-open", "Linux 退回打开上级目录，不报错")

    login.EXT_DIR = tmp + "/nope"
    bad, msg = login.setup_ext()
    check(not bad and "没找到扩展文件夹" in msg, "文件夹不在时说清原因，不假装成功")

    os.environ["VDL_NO_OPEN"] = "1"
    login.EXT_DIR = tmp
    n = len(calls)
    okq, _ = login.setup_ext()
    check(okq and len(calls) == n, "测试环境下不会真弹文件管理器")
finally:
    login.EXT_DIR, login.subprocess.Popen = real_ext, real_popen
    login.copy_to_clipboard = real_copy
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
