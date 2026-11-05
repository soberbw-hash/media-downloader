# -*- coding: utf-8 -*-
"""_py_pull 分片级断点：第一次传到一半断，第二次接着拼，内容必须一致。"""
import http.server
import os
import shutil
import socketserver
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hls

TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hlsstest")
NSEG = 8
SEGS = [bytes([65 + i]) * 50000 for i in range(NSEG)]   # 每片 50KB，内容可辨


class H(http.server.BaseHTTPRequestHandler):
    cut_at = 999                       # 第几片之后硬断

    def log_message(self, *a):
        pass

    def do_GET(self):
        i = int(self.path.rsplit("/", 1)[-1].split(".")[0].replace("seg", ""))
        body = SEGS[i]
        self.send_response(200)
        self.send_header("Content-Type", "video/mp2t")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if i >= self.cut_at:
            self.connection.close()    # 这片直接断
            return
        self.wfile.write(body)


def main():
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP)
    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % port
    text = "#EXTM3U\n#EXT-X-TARGETDURATION:1\n" + "".join(
        "#EXTINF:1.0,\n%s/seg%d.ts\n" % (base, i) for i in range(NSEG)) + "#EXT-X-ENDLIST\n"

    ok = True

    def check(name, cond, extra=""):
        nonlocal ok
        ok = ok and cond
        print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))

    out = os.path.join(TMP, "x.part.ts")
    H.cut_at = 3                       # 传完 seg0..seg3 就断
    calls = []

    def prog(**kw):
        calls.append(kw)

    try:
        hls._py_pull(base, text, out, {"User-Agent": "t"}, "", prog, "下载中")
        check("第一次应中断", False)
    except Exception as e:
        check("第一次中断", True, type(e).__name__)
    n1 = os.path.getsize(out) if os.path.exists(out) else 0
    idx = out + ".idx"
    check("半截文件保留", n1 > 0, "%d bytes" % n1)
    check("检查点 .idx 存在", os.path.exists(idx))

    # 断点续拼
    H.cut_at = 999
    calls.clear()
    hls._py_pull(base, text, out, {"User-Agent": "t"}, "", prog, "下载中")
    data = open(out, "rb").read()
    want = b"".join(SEGS)
    check("续拼后内容一致", data == want, "%d vs %d bytes" % (len(data), len(want)))
    check("完成后清掉 .idx", not os.path.exists(idx))

    # 确认续拼时前面几片没有重下：done 从检查点继续
    first = calls[0] if calls else {}
    check("续拼从检查点继续（done>1）", first.get("done", 0) > 1, str(first.get("done")))

    srv.shutdown()
    print("\n== 结果：%s ==" % ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
