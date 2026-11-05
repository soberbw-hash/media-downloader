# -*- coding: utf-8 -*-
"""提交后自动同步到 GitHub：先按远端习惯重建 SHA，再走 API 推送。

单独跑也行：python tools/gh_auto_push.py
凭据顺序：环境变量 GH_TOKEN_API → git credential fill（git 存的 GitHub 账号）。
推不上只提示，不影响提交本身。
"""
import os
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def log(msg):
    print("[自动推送] " + msg)


def find_token():
    tok = os.environ.get("GH_TOKEN_API") or ""
    if tok:
        return tok
    try:
        p = subprocess.run(["git", "credential", "fill"], cwd=ROOT,
                           input=b"protocol=https\nhost=github.com\n\n",
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        for line in p.stdout.decode("utf-8", "replace").splitlines():
            if line.startswith("password="):
                return line[len("password="):].strip()
    except Exception:
        pass
    return ""


def net_ok():
    # 先花 3 秒问个路，别在断网时把提交卡在几分钟的重试上
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("https://api.github.com", timeout=3) as r:
            return True
    except Exception:
        return False


def main():
    if not os.path.exists(os.path.join(ROOT, "tools", "gh_push_all.py")):
        return 0
    if not net_ok():
        log("网络到不了 api.github.com，这次先不推（提交不受影响）")
        return 0
    token = find_token()
    if not token:
        log("没找到 GitHub 凭据，跳过。先把 GH_TOKEN_API 放进环境变量，或 git push 登录过一次")
        return 0
    os.environ["GH_TOKEN_API"] = token

    for script in ("gh_rebuild.py", "gh_push_all.py"):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", script)], cwd=ROOT)
        if r.returncode:
            log("%s 没跑完（退出码 %d），提交本身不受影响，稍后可手动跑"
                % (script, r.returncode))
            return 0
    log("已同步到 GitHub")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:          # 钩子里绝不抛，免得吓着提交的人
        log("出错了（提交不受影响）：%s" % e)
        sys.exit(0)
