# -*- coding: utf-8 -*-
"""断点续传的单元实测：本地起一个支持 Range 的 HTTP 服务，
模拟中断、换地址、服务器不认 Range、416 等情况。"""
import http.server
import os
import shutil
import socketserver
import sys
import threading
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core

TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rtest")
DATA = bytes(range(256)) * 20000        # 5,120,000 字节，内容可校验
DATA2 = bytes(range(256)) * 12345       # 另一个长度的文件，用来测「全长变了」
FILE = "blob.bin"


class H(http.server.BaseHTTPRequestHandler):
    ignore_range = False                # 模拟「不认 Range」的服务器
    cut_after = None                    # 模拟传一半就断

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/dead":
            self.send_error(404)
            return
        data = DATA if not self.path.startswith("/blob2") else DATA2
        rng = self.headers.get("Range") or ""
        start = 0
        if rng.startswith("bytes=") and not self.ignore_range:
            start = int(rng[6:].split("-")[0])
            if start >= len(data):
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % len(data))
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range",
                             "bytes %d-%d/%d" % (start, len(data) - 1, len(data)))
            body = data[start:]
        else:
            self.send_response(200)
            body = data
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.cut_after is not None:
            self.wfile.write(body[:self.cut_after])
            self.wfile.flush()
            self.connection.close()      # 硬断
            return
        self.wfile.write(body)


def fresh():
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP)


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))
    return cond


def main():
    fresh()
    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/%s" % (port, FILE)
    ok = True
    calls = []

    def prog(**kw):
        calls.append(kw)

    # 1) 完整下一遍
    out = os.path.join(TMP, "a.mp4")
    p = core.resumable_download(url, out, prog)
    ok &= check("完整下载", p == out and open(out, "rb").read() == DATA,
                "%d bytes" % os.path.getsize(out))
    ok &= check("完成后无 .part 残留",
                not os.path.exists(out + ".part") and not os.path.exists(out + ".part.src"))

    # 2) 中断 → 保留 .part → 续传
    out = os.path.join(TMP, "b.mp4")
    H.cut_after = 2 * 1024 * 1024
    try:
        core.resumable_download(url, out, prog)
        ok &= check("中断应抛错", False)
    except Exception:
        H.cut_after = None
        part = out + ".part"
        ok &= check("中断后保留 .part", os.path.exists(part),
                    "%d bytes" % (os.path.getsize(part) if os.path.exists(part) else 0))
        ok &= check("半截文件没顶正式名", not os.path.exists(out))
        # 续传
        grew = []
        p = core.resumable_download(url, out, prog)
        got = open(out, "rb").read()
        ok &= check("续传后内容一致", got == DATA, "%d bytes" % len(got))
        # 进度里 total 应是全长
        last = [c for c in calls if c.get("total")][-1:]
        ok &= check("续传进度 total=全长", bool(last) and last[0]["total"] == len(DATA),
                    str(last))
        ok &= check("续传后无 .part 残留", not os.path.exists(out + ".part"))

    # 3) 换了签名地址但全长一致 → 当同一个文件接着续（网易云就是这种）
    out = os.path.join(TMP, "c.mp4")
    H.cut_after = 1024 * 1024
    try:
        core.resumable_download(url, out, prog)
    except Exception:
        pass
    H.cut_after = None
    marks = []
    p = core.resumable_download(url + "?sign=another", out,
                                lambda **kw: marks.append(kw))
    first = [m for m in marks if m.get("done")]
    ok &= check("换地址但全长一致仍续传",
                open(out, "rb").read() == DATA and first
                and first[0]["done"] > 1024 * 1024,
                "首条 done=%s" % (first[0]["done"] if first else None))

    # 3b) 换了地址且全长不一样 → 重头下，不能拼接
    out = os.path.join(TMP, "c2.mp4")
    H.cut_after = 1024 * 1024
    try:
        core.resumable_download(url, out, prog)
    except Exception:
        pass
    H.cut_after = None
    p = core.resumable_download("http://127.0.0.1:%d/blob2.bin" % port, out, prog)
    ok &= check("全长变了就重下", open(out, "rb").read() == DATA2)

    # 4) 服务器不认 Range（200 重发整份）→ 也得是完整文件
    out = os.path.join(TMP, "d.mp4")
    H.cut_after = 1024 * 1024
    try:
        core.resumable_download(url, out, prog)
    except Exception:
        pass
    H.cut_after = None
    H.ignore_range = True
    p = core.resumable_download(url, out, prog)
    H.ignore_range = False
    ok &= check("服务器不认 Range 也完整", open(out, "rb").read() == DATA)

    # 5) 已经下完再请求 → 416 → 直接改名
    out = os.path.join(TMP, "e.mp4")
    H.cut_after = 4 * 1024 * 1024      # 只断掉最后一小截
    try:
        core.resumable_download(url, out, prog)
    except Exception:
        pass
    H.cut_after = None
    # 手动把 .part 补成全长，再请求 → 416
    with open(out + ".part", "ab") as f:
        f.write(DATA[4 * 1024 * 1024:])
    with open(out + ".part.src", "w", encoding="utf-8") as f:
        f.write("%d\n%s" % (len(DATA), url))
    p = core.resumable_download(url, out, prog)
    ok &= check("416 视为已下完", open(out, "rb").read() == DATA)

    # 6) stream_download：第一条 404 → 第二条成功
    out = os.path.join(TMP, "f.mp4")
    p, used = core.stream_download(["http://127.0.0.1:%d/dead" % port, url],
                                   out, prog, min_bytes=1000)
    ok &= check("候选地址依次试", os.path.exists(out) and open(out, "rb").read() == DATA)

    # 7) 全部失败时不留 .part
    out = os.path.join(TMP, "g.mp4")
    try:
        core.stream_download(["http://127.0.0.1:%d/dead" % port], out, prog, min_bytes=1000)
        ok &= check("全失败应抛错", False)
    except Exception:
        ok &= check("全失败不留垃圾",
                    not os.path.exists(out) and not os.path.exists(out + ".part"))

    srv.shutdown()
    print("\n== 结果：%s ==" % ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
