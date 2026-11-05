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
            if f.endswith(".part") or f.endswith(".part.src"):
                continue
            full = os.path.join(TMP, f)
            try:
                if os.path.isdir(full):
                    shutil.rmtree(full, ignore_errors=True)   # 同名附件文件夹
                else:
                    os.remove(full)
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
    # 弹幕要走网络，比状态「已完成」晚一两秒落地——等到齐为止。
    # 附件会收进跟视频同名的文件夹，去里面找
    ok = False
    vids, xml, txt = [], [], []
    for _ in range(30):
        vids, xml, txt = [], [], []
        for f in sorted(os.listdir(TMP)):
            full = os.path.join(TMP, f)
            if os.path.isdir(full):
                for g in sorted(os.listdir(full)):
                    if g.endswith(".xml"):
                        xml.append(os.path.join(full, g))
                    elif g.endswith(".txt"):
                        txt.append(os.path.join(full, g))
            elif f.endswith((".mp4", ".mkv", ".flv", ".webm")):
                vids.append(f)
        if vids and xml and txt:
            ok = True
            break
        time.sleep(0.5)
    print("files:", sorted(os.listdir(TMP)))
    print("视频:", vids, "| 弹幕:", xml, "| 简介:", txt)
    if vids and xml:
        # 附件文件夹要跟视频同名
        folder = os.path.basename(os.path.dirname(xml[0]))
        vstem = os.path.splitext(vids[0])[0]
        if folder != vstem:
            print("文件夹名跟视频对不上:", folder, "!=", vstem)
            ok = False
    if xml:
        data = open(xml[0], encoding="utf-8").read()
        print("弹幕条数:", data.count("<d "), "| 简介前 6 行:")
    if txt:
        print("\n".join(open(txt[0], encoding="utf-8").read().splitlines()[:6]))
    shutil.rmtree(TMP, ignore_errors=True)
    print("\n== %s ==" % ("通过" if ok else "失败"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
