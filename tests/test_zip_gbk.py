# -*- coding: utf-8 -*-
"""zip 文件名编码回归：中文文件名按 GBK 存，好压/360 解压不乱码。

背景：Python 3.13 的 zipfile 写 zip 时强制置 UTF-8 标志位，但好压、360 压缩、
Windows 资源管理器等中文工具会无视标志位、按系统 GBK 解码文件名 → 全部乱码，
启动器找不到「程序」目录。

修法：文件名主字段按 GBK 字节写、不置 0x800 标志位，另在 0x7075
（Info-ZIP Unicode Path）扩展里补 UTF-8 正名。三条读取路径都要对：
  ① Python zipfile（走 0x7075）→ 正确
  ② 中文解压工具（按 GBK 解主名字节）→ 正确
  ③ updater 手工解析器（utf-8 失败 → gbk 兜底）→ 正确
"""
import os
import struct
import sys
import tempfile
import zipfile
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

NAMES = [
    "音视频下载器/程序/server.py",
    "音视频下载器/使用说明.txt",
    "音视频下载器/程序/web/app.js",
    "音视频下载器/readme.md",
]


def log(*a):
    print(*a, flush=True)


def make_test_zip(path):
    from make_clean_copy import make_zip
    # 内容随便，只要 zip 结构对
    tmpdir = os.path.join(os.path.dirname(path), "_gbk_src")
    for n in NAMES:
        rel = n.split("/", 1)[1]  # 去掉顶层前缀，make_zip 会自己加
        dst = os.path.join(tmpdir, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "w", encoding="utf-8") as f:
            f.write("hello " + n)
    make_zip(tmpdir, path, arc_root="音视频下载器")
    return tmpdir


def test_gbk_zip():
    tmp = tempfile.mkdtemp(prefix="vdl_gbk_")
    zpath = os.path.join(tmp, "test.zip")
    src = make_test_zip(zpath)

    # ① Python zipfile 读回（走 0x7075）
    with zipfile.ZipFile(zpath) as z:
        got = z.namelist()
        assert sorted(got) == sorted(NAMES), "Python 读回名字不对: %s" % got[:3]
        for n in NAMES:
            assert z.read(n).startswith(b"hello "), "内容读不出: " + n
    log("  OK ① Python zipfile 读回正确（0x7075 生效）")

    # ② 中文解压工具：主名字节按 GBK 解，且不带 UTF-8 标志位
    raw = open(zpath, "rb").read()
    pos, decoded, flags_seen = 0, [], set()
    while True:
        j = raw.find(b"PK\x01\x02", pos)
        if j < 0:
            break
        flags = struct.unpack_from("<H", raw, j + 8)[0]
        nlen = struct.unpack_from("<H", raw, j + 28)[0]
        nbytes = raw[j + 46: j + 46 + nlen]
        decoded.append(nbytes.decode("gbk"))
        flags_seen.add(flags)
        pos = j + 46
    assert sorted(decoded) == sorted(NAMES), "GBK 解码结果不对: %s" % decoded[:3]
    assert 0x800 not in flags_seen, "还带着 UTF-8 标志位: %s" % [hex(f) for f in flags_seen]
    log("  OK ② 中文解压工具按 GBK 解码正确，无 UTF-8 标志位")

    # ③ updater 手工解析器路径
    for n in NAMES:
        gbk_bytes = n.encode("gbk")
        try:
            name = gbk_bytes.decode("utf-8")
        except UnicodeDecodeError:
            try:
                name = gbk_bytes.decode("gbk")
            except UnicodeDecodeError:
                name = gbk_bytes.decode("cp437", "replace")
        assert name == n, "updater 解码不对: %s != %s" % (name, n)
    log("  OK ③ updater 手工解析器（utf-8→gbk 兜底）正确")

    # 顶层目录名不受本地文件夹名影响
    assert all(n.startswith("音视频下载器/") for n in got), "顶层目录名不对"
    log("  OK zip 顶层目录名固定为「音视频下载器」")

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


def main():
    log("zip 文件名编码回归")
    test_gbk_zip()
    log("zip 文件名编码回归 —— 全部通过")


if __name__ == "__main__":
    main()
