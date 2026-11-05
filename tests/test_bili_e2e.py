# -*- coding: utf-8 -*-
"""B站端到端：下一个小视频，旁边必须有同名 .xml 弹幕 + .txt 简介。"""
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core

TMP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "Temp", "bili_e2e")
URL = "https://www.bilibili.com/video/BV1xx411c7mD"


def main():
    # 只清成品，留着 .part——重跑时顺带验证断点续传
    if os.path.isdir(TMP):
        for f in os.listdir(TMP):
            if not (f.endswith(".part") or f.endswith(".part.src")):
                try:
                    os.remove(os.path.join(TMP, f))
                except OSError:
                    pass
    else:
        os.makedirs(TMP)
    cfg = {"out_dir": TMP, "quality": "480", "max_workers": 2, "proxy": "",
           "save_danmaku": True, "save_desc": True}
    m = core.Manager(cfg)
    t, why = m.add(URL)
    if not t:
        print("add failed:", why)
        return 1
    deadline = time.time() + 600
    while time.time() < deadline:
        tt = m.tasks.get(t.id)
        if tt and tt.status in ("done", "error", "canceled"):
            break
        time.sleep(0.5)
    tt = m.tasks.get(t.id)
    d = tt.to_dict()
    print("status:", d["status"], "| stage:", d.get("stage"), "| err:", d.get("error"))
    if d["status"] != "done":
        return 1
    # 弹幕要走网络，比状态「已完成」晚一两秒落地——等到齐为止
    ok = False
    for _ in range(30):
        files = sorted(os.listdir(TMP))
        vids = [f for f in files if f.endswith((".mp4", ".mkv", ".flv", ".webm"))]
        xml = [f for f in files if f.endswith(".xml")]
        txt = [f for f in files if f.endswith(".txt")]
        if vids and xml and txt:
            ok = True
            break
        time.sleep(0.5)
    print("files:", sorted(os.listdir(TMP)))
    print("视频:", vids, "| 弹幕:", xml, "| 简介:", txt)
    if xml:
        data = open(os.path.join(TMP, xml[0]), encoding="utf-8").read()
        print("弹幕条数:", data.count("<d "), "| 简介前 6 行:")
    if txt:
        print("\n".join(open(os.path.join(TMP, txt[0]), encoding="utf-8").read().splitlines()[:6]))
    shutil.rmtree(TMP, ignore_errors=True)
    print("\n== %s ==" % ("通过" if ok else "失败"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
