# -*- coding: utf-8 -*-
"""1.24.3 回归：外发 Windows 包里的相对下载目录，解压到哪都能用。

干净副本的 config.json 写 "out_dir": "下载的文件"（相对包根）。
server.load_cfg() 必须把它解析成包根下的绝对路径，且不能改动
开发机上原本写死的绝对路径配置。
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE = r'''
import json, os, sys
sys.path.insert(0, r"%s")
import server
print(json.dumps(server.load_cfg().get("out_dir", ""), ensure_ascii=False))
'''


def probe(cfg_value):
    with tempfile.TemporaryDirectory(prefix="vdlcfg_") as d:
        with open(os.path.join(d, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"out_dir": cfg_value, "out_dir_set": True}, f,
                      ensure_ascii=False)
        env = os.environ.copy()
        env["VDL_DATA_DIR"] = d
        env["VDL_NO_OPEN"] = "1"
        r = subprocess.run([sys.executable, "-c", PROBE % ROOT], env=env,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
        assert r.returncode == 0, "探针失败: %s" % (r.stderr or "")[-600:]
        return json.loads(r.stdout.strip().splitlines()[-1])


def main():
    got = probe("下载的文件")
    assert os.path.isabs(got), "相对目录没被解析成绝对路径: %s" % got
    assert got.endswith("下载的文件"), "解析到了别处: %s" % got
    # 源码运行时 APP_DIR = 项目根，上一级即项目父目录；
    # 便携包里 APP_DIR = 包/程序，上一级正好是解压出来的包根。
    expect = os.path.abspath(os.path.join(ROOT, "..", "下载的文件"))
    assert got == expect, "没按程序目录的上一级解析: %s != %s" % (got, expect)
    assert got != os.path.abspath("下载的文件"), \
        "按当前工作目录解析了，换目录启动就会跑偏"
    print("  ✓ 相对目录 ->", got)

    absolute = os.path.join(tempfile.gettempdir(), "vdl_abs_downloads")
    assert probe(absolute) == absolute, "绝对路径配置被改坏了"
    print("  ✓ 绝对路径配置原样保留")
    print("全部通过")


if __name__ == "__main__":
    main()
