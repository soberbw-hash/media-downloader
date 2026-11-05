# -*- coding: utf-8 -*-
"""yt-dlp 去掉 nopart 后的续传实测：传到一半故意掐断，.part 必须留着，
重下时 yt-dlp 自己用 Range 接着下（首条进度就带着已下字节数）。"""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core

TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ytest")
URL = "https://cdn1.suno.ai/7dd097b8-ea7c-47f1-974d-816fd61127eb.mp4"


def main():
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP)
    ok = True

    def check(name, cond, extra=""):
        nonlocal ok
        ok = ok and cond
        print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))

    # 1) 传到 1.5MB 就掐断（模拟取消 / 断网）
    marks = []

    def cut_progress(**kw):
        marks.append(kw)
        if kw.get("done", 0) >= 1500 * 1024:
            raise RuntimeError("模拟中断")

    try:
        core.ytdlp_download(URL, TMP, cut_progress, "best")
        check("第一次应中断", False)
    except Exception as e:
        check("第一次中断", True, "%s: %s" % (type(e).__name__, str(e)[:40]))

    parts = [f for f in os.listdir(TMP) if f.endswith(".part")]
    finals = [f for f in os.listdir(TMP) if not f.endswith(".part")]
    check("中断后留着 .part", bool(parts), "、".join(parts) or "无")
    check("半截文件没顶正式名", not finals, "、".join(finals) or "干净")
    psize = os.path.getsize(os.path.join(TMP, parts[0])) if parts else 0
    check(".part 有实际字节", psize > 500 * 1024, "%d bytes" % psize)

    # 2) 重下：必须接着 .part 续，而不是从零开始
    marks2 = []

    def prog2(**kw):
        marks2.append(kw)

    path = core.ytdlp_download(URL, TMP, prog2, "best")
    first = [m for m in marks2 if m.get("done")]
    check("续传后下完", os.path.exists(path), os.path.basename(path))
    check("进度从断点开始（首条 done >= .part 大小）",
          bool(first) and first[0]["done"] >= psize,
          "首条 done=%s .part=%d" % (first[0]["done"] if first else None, psize))
    size = os.path.getsize(path)
    head = open(path, "rb").read(12)
    check("成品是完整 mp4", size > 5 * 1024 * 1024 and head[4:8] == b"ftyp",
          "%d bytes, head=%r" % (size, head[:12]))
    left = [f for f in os.listdir(TMP) if f.endswith(".part")]
    check("完成后无 .part 残留", not left, "、".join(left) or "干净")

    shutil.rmtree(TMP, ignore_errors=True)
    print("\n== 结果：%s ==" % ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
