# -*- coding: utf-8 -*-
"""music._stream 断点续传实测：下载一首免费歌，删掉成品、伪造半截 .part，
再下一次必须从断点续上。注意 .part 里存的是**没加 ID3 的原始流**——
写完标签的成品会大出一张封面的体积，拿它当基准会全长对不上。"""
import os
import shutil
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import music

TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mtest")
SID = "1357374736"          # 免费曲目


def main():
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP)
    ok = True

    def check(name, cond, extra=""):
        nonlocal ok
        ok = ok and cond
        print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))

    def prog(**kw):
        pass

    # 1) 完整下一首
    p = music.download("https://music.163.com/song?id=%s" % SID, TMP, prog, "best")
    ref = open(p, "rb").read()
    check("完整下载", len(ref) > 300000, "%s %d bytes" % (os.path.basename(p), len(ref)))
    check("无 .part 残留", not os.path.exists(p + ".part"))

    # 取一份「没写 ID3 的原始流」——.part 里存的是它
    cands = music.resolve("netease", SID, "best")
    u = cands[0]["url"]
    req = urllib.request.Request(u, headers={"User-Agent": music.UA_PC,
                                             "Referer": "https://music.163.com/"})
    raw = urllib.request.urlopen(req, timeout=30).read()
    rawlen = len(raw)
    check("原始流比成品小（差值 = ID3 封面）", rawlen < len(ref),
          "%d → %d" % (rawlen, len(ref)))

    # 2) 伪造半截：删成品，留 1/3 的原始流 + 「全长 + 上次的签名地址」
    #    重试时网易云给的是新签名地址（跟 .src 里记的不一样），
    #    但全长一致 → 必须当成同一个文件接着续
    third = rawlen // 3
    os.remove(p)
    with open(p + ".part", "wb") as f:
        f.write(raw[:third])
    with open(p + ".part.src", "w", encoding="utf-8") as f:
        f.write("%d\n%s" % (rawlen, u))

    marks = []
    p2 = music.download("https://music.163.com/song?id=%s" % SID, TMP,
                        lambda **kw: marks.append(kw), "best")
    got = open(p2, "rb").read()
    check("续传后成品大小正常", len(got) == len(ref), "%d bytes" % len(got))
    first = [m for m in marks if m.get("done") and m.get("done") > 0]
    check("签名地址变了照样从断点续上（首条 done > 1/3）",
          bool(first) and first[0]["done"] >= third,
          str(first[0].get("done") if first else None))
    check("进度里的 total 是原始流全长",
          bool(first) and first[-1].get("total") == rawlen,
          str(first[-1].get("total") if first else None))
    check("续传后无 .part 残留", not os.path.exists(p2 + ".part"))

    # 3) 全长对不上 → 不能拼，重下
    os.remove(p2)
    with open(p2 + ".part", "wb") as f:
        f.write(b"\x00" * 1000)
    with open(p2 + ".part.src", "w", encoding="utf-8") as f:
        f.write("4242\nhttps://example.com/other-file.mp3")
    p3 = music.download("https://music.163.com/song?id=%s" % SID, TMP, prog, "best")
    check("全长对不上就重下、成品正常", os.path.getsize(p3) == len(ref))

    shutil.rmtree(TMP, ignore_errors=True)
    print("\n== 结果：%s ==" % ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
