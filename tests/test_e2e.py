# -*- coding: utf-8 -*-
"""端到端：走完整任务队列下一次（解析→下载→命名→历史），确认没回归。"""
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core

TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "e2e")


def main():
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP)
    cfg = {"out_dir": TMP, "quality": "best", "max_workers": 2, "proxy": ""}
    m = core.Manager(cfg)
    t, why = m.add("https://music.163.com/song?id=1357374736")
    if not t:
        print("add failed:", why)
        return 1
    tid = t.id
    print("added:", tid)
    deadline = time.time() + 180
    while time.time() < deadline:
        t = m.tasks.get(tid)
        if t and t.status in ("done", "error", "canceled"):
            break
        time.sleep(0.5)
    t = m.tasks.get(tid)
    d = t.to_dict()
    print("status:", d["status"], "| stage:", d.get("stage"), "| title:", d.get("title"))
    if d["status"] != "done":
        print("error:", d.get("error"))
        return 1
    files = os.listdir(TMP)
    print("files:", files)
    assert not [f for f in files if f.endswith(".part")], "有 .part 残留"
    assert d["filepath"] and os.path.exists(d["filepath"]), "成品不见了"
    size = os.path.getsize(d["filepath"])
    print("file:", os.path.basename(d["filepath"]), size, "bytes")
    print("history:", len(m.history_snapshot()))
    shutil.rmtree(TMP, ignore_errors=True)
    print("\n== 端到端通过 ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
