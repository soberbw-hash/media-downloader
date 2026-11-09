# -*- coding: utf-8 -*-
"""下载到 100% 卡一会的回归：多段流不能提前报 100%，收尾要说明在做什么。

复现现场（B站 DASH，真机打点）：视频流 2.73s 下完、音频流还要 1.5s、
ffmpeg 合并 0.5s。旧代码用 max(p, last) 把后半程钉在 100%，文案还被
新一段的 downloading 重置成「下载中」→ 看着像卡死，其实一直在下。

跑法：python tests/test_progress_wrap.py        （纯逻辑 + 模拟两段流 + 静态，秒级）
      python tests/test_progress_wrap.py --live  （再真下一次 B 站视频，网络慢会自动跳过）
"""
import atexit
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="vdl_prog_")
os.environ["VDL_DATA_DIR"] = TMP
atexit.register(lambda: shutil.rmtree(TMP, ignore_errors=True))

import core  # noqa: E402

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


print("=== 1. 分段进度永远给收尾留位置 ===")
check(core._seg_percent(0, 0) == 0.0, "第一段从 0 开始")
check(abs(core._seg_percent(0, 100) - 95.0) < 1e-9, "第一段下完只到 95，不是 100")
check(abs(core._seg_percent(1, 0) - 95.0) < 1e-9, "第二段从 95 接上，不倒退")
check(abs(core._seg_percent(1, 100) - 99.5) < 1e-9, "第二段下完 99.5")
check(abs(core._seg_percent(2, 0) - 99.5) < 1e-9, "第三段从 99.5 接上")
check(core._seg_percent(9, 100) < 100.0, "再多的段也到不了 100")
check(core._seg_percent(0, -10) == 0.0 and core._seg_percent(0, 500) == 95.0,
      "异常字节数被夹住，不会冒出负进度或超 100")
for seg in range(4):
    check(all(core._seg_percent(seg, r) < 100.0 for r in range(0, 101, 5)),
          "第 %d 段全程 < 100" % (seg + 1))

print("=== 2. 两段连起来：单调、不动在 100、间距合理 ===")
seq = []
last = 0.0
for seg in range(2):
    for raw in range(0, 101, 2):
        p = max(core._seg_percent(seg, raw), last)
        seq.append(p)
        last = p
check(all(b >= a - 1e-9 for a, b in zip(seq, seq[1:])), "进度不倒退")
check(max(seq) < 100.0, "全程没有出现 100%")
check(seq[0] == 0.0 and seq[-1] > 99.0, "从 0 一路走到收尾附近")
check(seq[len(seq) // 2] > 90.0, "第一段下完已过 90%，不是刚起步")

print("=== 3. 收尾文案与界面反馈 ===")
src = read("core.py")
js = read("web/app.js")
css = read("web/style.css")
check("_seg_percent(state[\"seg\"], raw)" in src, "hook 用分段算法算进度")
check("state[\"seg\"] += 1" in src, "每段下载完才推进段号")
check('"正在合并音视频…" if state["seg"] >= 2' in src, "多段才说合并")
check('"正在处理…"' in src, "单段收尾不谎称在合并")
check('p = max(p, state["last"])' not in src, "旧的纯 max 定格写法已删")
check("const wrapping = t.status === \"running\"" in js, "前端识别收尾阶段")
check("!wrapping" in js, "收尾时不再显示定住的百分比")
check('classList.toggle("wrapping", wrapping)' in js, "收尾时给进度条换状态")
check(".bar.wrapping i" in css and "@keyframes barFlow" in css,
      "进度条有流动动画，不像死机")


def simulate():
    """用假的 yt-dlp 真跑一遍 hook：两段流 + 两次 finished，不依赖网络。"""
    print("=== 4. 模拟两段流真跑 hook ===")
    out = tempfile.mkdtemp(prefix="vdl_sim_", dir=TMP)
    produced = os.path.join(out, "假视频.mp4")
    with open(produced, "wb") as f:
        f.write(b"fake mp4")
    events = []

    class FakeYDL(object):
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return {"title": "假视频", "formats": [], "ext": "mp4"}

        def process_ie_result(self, info, download=True):
            hook = self.opts["progress_hooks"][0]
            # 视频流 1000 字节、音频流 200 字节，各自从 0 走到 100
            for name, total in (("v.mp4", 1000), ("a.m4a", 200)):
                for pct in (0, 25, 50, 75, 100):
                    hook({"status": "downloading", "filename": name,
                          "total_bytes": total,
                          "downloaded_bytes": int(total * pct / 100.0),
                          "speed": 1048576})
                hook({"status": "finished", "filename": name,
                      "total_bytes": total})
            return info

        def prepare_filename(self, info):
            return produced

    class FakeModule(object):
        YoutubeDL = FakeYDL

    real = core.yt_dlp
    core.yt_dlp = FakeModule
    try:
        core.ytdlp_download("https://example.com/v", out,
                            lambda **kw: events.append(kw), "best")
    finally:
        core.yt_dlp = real

    pcts = [e["percent"] for e in events if "percent" in e]
    stages = [e["stage"] for e in events if e.get("stage")]
    print("  进度序列:", [round(p, 1) for p in pcts])
    print("  阶段:", stages)
    check(all(b >= a - 1e-9 for a, b in zip(pcts, pcts[1:])), "两段之间进度不倒退")
    check(max(pcts) < 100.0, "下载阶段没有报过 100%")
    check(pcts[4] <= 95.0 + 1e-9 and pcts[5] >= 95.0 - 1e-9,
          "第一段收在 95，第二段从 95 接上")
    check(pcts[-1] > 99.0, "第二段把进度推到收尾附近")
    check(len(stages) == 2 and stages[0] == "正在处理…"
          and "合并音视频" in stages[1],
          "单段收尾说「处理」，两段结束说「合并」")
    check(all(e.get("speed") == 1.0 for e in events if "percent" in e),
          "速度照常回传，没被新逻辑吞掉")


def live_check():
    """真下一次 B 站 DASH 视频；站点限速时跳过，不把网络问题算成回归失败。"""
    print("=== 5. 真机下载（可选） ===")
    import threading
    import time
    out = tempfile.mkdtemp(prefix="vdl_live_", dir=TMP)
    events = []
    res = {}
    t0 = time.time()

    def progress(**kw):
        events.append(kw)

    def run():
        try:
            p = core.ytdlp_download("https://www.bilibili.com/video/BV1xx411c7mD",
                                    out, progress, "720")
            res["ok"] = os.path.isfile(p) and os.path.getsize(p) > 100000
        except Exception as e:
            res["err"] = "%s %s" % (type(e).__name__, str(e)[:100])

    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(70)
    if th.is_alive():
        print("  B站这次响应太慢，跳过真机项（不影响前面的回归）")
        return
    if "err" in res:
        print("  真机下载失败（网络问题）:", res["err"])
        return
    dl = [e["percent"] for e in events if "percent" in e]
    stages = [e["stage"] for e in events if e.get("stage")]
    print("  耗时 %.2fs | %d 次回调 | 进度 %.1f ~ %.1f" %
          (time.time() - t0, len(dl), min(dl), max(dl)))
    check(res.get("ok"), "真下出来一个能用的 mp4")
    check(all(p < 100.0 for p in dl), "真机下载阶段没有报过 100%")
    check(any("合并音视频" in s or "处理" in s for s in stages), "真机收尾有文案")
    check(any(p > 95.0 for p in dl), "真机后半段进度确实往前走")


simulate()
if "--live" in sys.argv:
    live_check()

print("\n=== %d 项通过，%d 项失败 ===" % (OK[0], len(FAIL)))
for f in FAIL:
    print("  - " + f)
sys.exit(1 if FAIL else 0)
