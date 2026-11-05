# -*- coding: utf-8 -*-
"""把本地所有未推的提交按顺序推到 GitHub（github.com:443 不通，只能走 api.github.com）。

对链上每个提交：
  1. 相对父提交算出改动文件，上传 blob
  2. 以远端当前 tree 作 base_tree 建新 tree
  3. 建 commit（author=committer，取本地提交身份）
  4. 校验返回的 SHA 与本地提交 SHA 完全一致

全程校验 SHA，一旦某个提交对不上就停下，不破坏本地历史。
"""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = "soberbw-hash/media-downloader"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN = os.environ.get("GH_TOKEN_API") or ""
API = "https://api.github.com"
OWNER, NAME = REPO.split("/")


def git(*args, binary=False):
    p = subprocess.run(["git", "-c", "core.quotepath=false"] + list(args), cwd=ROOT,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode:
        raise SystemExit("git %s failed: %s" % (" ".join(args),
                                                p.stderr.decode("utf-8", "replace")))
    return p.stdout if binary else p.stdout.decode("utf-8", "replace")


def api(method, path, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    last = ""
    # api.github.com 偶尔回 500（空 body，像中间设备抽风），原地重试几次比直接放弃好
    for attempt in range(4):
        req = urllib.request.Request(API + path, data=data, method=method)
        req.add_header("Authorization", "Bearer " + TOKEN)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if data:
            req.add_header("Content-Type", "application/json")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=90) as r:
                return json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            last = "API %s %s -> %s %s" % (method, path, e.code,
                                           e.read().decode("utf-8", "replace"))
            if e.code < 500 or attempt == 3:
                raise SystemExit(last)
            print("   …%s 重试 %d" % (e.code, attempt + 1))
            time.sleep(2 + attempt * 2)
        except Exception as e:
            last = "API %s %s -> %s" % (method, path, e)
            if attempt == 3:
                raise SystemExit(last)
            print("   …网络错误重试 %d" % (attempt + 1))
            time.sleep(2 + attempt * 2)
    raise SystemExit(last)


def push_one(local_sha, remote_sha, remote_tree, dry=False):
    parent = git("rev-parse", local_sha + "^").strip()
    if parent != remote_sha:
        raise SystemExit("链断了：%s 的父提交是 %s，远端却是 %s"
                         % (local_sha[:7], parent[:7], remote_sha[:7]))
    msg = git("log", "-1", "--format=%B", local_sha).rstrip("\n")

    lines = [ln for ln in git("diff", "--name-status", "-M", parent, local_sha).splitlines()
             if ln.strip()]
    entries = []
    for ln in lines:
        parts = ln.split("\t")
        st = parts[0]
        # 重命名/复制是 "R100\t旧路径\t新路径" 三段，旧路径必须显式删掉，
        # 否则远端 tree 里会残留旧文件
        if st[:1] in ("R", "C") and len(parts) == 3:
            entries.append({"path": parts[1], "mode": "100644", "type": "blob", "sha": None})
            print("    - 移除旧路径 %s" % parts[1])
            path = parts[2]
        elif st.startswith("D"):
            entries.append({"path": parts[-1], "mode": "100644", "type": "blob", "sha": None})
            print("    - 删除 %s" % parts[-1])
            continue
        else:
            path = parts[-1]
        content = git("cat-file", "blob", "%s:%s" % (local_sha, path), binary=True)
        blob = api("POST", "/repos/%s/%s/git/blobs" % (OWNER, NAME),
                   {"content": base64.b64encode(content).decode("ascii"),
                    "encoding": "base64"})
        mode = git("ls-tree", local_sha, "--", path).split()[0]
        entries.append({"path": path, "mode": mode, "type": "blob", "sha": blob["sha"]})
        print("    + %s %s %d 字节" % (path, mode, len(content)))

    tree = api("POST", "/repos/%s/%s/git/trees" % (OWNER, NAME),
               {"base_tree": remote_tree, "tree": entries})
    local_tree = git("rev-parse", local_sha + "^{tree}").strip()
    if tree["sha"] != local_tree:
        raise SystemExit("tree 不一致：远端 %s / 本地 %s" % (tree["sha"][:10], local_tree[:10]))

    ident = git("log", "-1", "--format=%an%n%ae%n%aI", local_sha).splitlines()
    who = {"name": ident[0], "email": ident[1], "date": ident[2]}
    if dry:
        print("    (dry-run，跳过建 commit)")
        return local_sha, tree["sha"]

    commit = api("POST", "/repos/%s/%s/git/commits" % (OWNER, NAME),
                 {"message": msg, "tree": tree["sha"], "parents": [remote_sha],
                  "author": who, "committer": who})
    if commit["sha"] != local_sha:
        raise SystemExit("commit SHA 不一致：远端 %s / 本地 %s"
                         % (commit["sha"], local_sha))
    print("    = commit %s 一致" % local_sha[:7])
    return commit["sha"], tree["sha"]


def main():
    if not TOKEN:
        raise SystemExit("缺少 GH_TOKEN_API")
    # 只看已跟踪文件：测试残留（未跟踪）不影响推送
    dirty = git("status", "--porcelain", "--untracked-files=no").strip()
    if dirty:
        raise SystemExit("工作区不干净，先提交：\n" + dirty)

    head = git("rev-parse", "HEAD").strip()
    ref = api("GET", "/repos/%s/%s/git/ref/heads/main" % (OWNER, NAME))
    remote_sha = ref["object"]["sha"]
    pending = [c for c in git("rev-list", "--reverse", remote_sha + ".." + head).split()]
    if not pending:
        print("远端已经是最新，无需推送")
        return 0

    print("远端 main : %s" % remote_sha[:7])
    print("待推提交  : %d 个" % len(pending))

    remote_tree = api("GET", "/repos/%s/%s/git/commits/%s"
                      % (OWNER, NAME, remote_sha))["tree"]["sha"]
    cur = remote_sha
    for i, c in enumerate(pending, 1):
        subject = git("log", "-1", "--format=%s", c).strip()
        print("\n[%d/%d] %s  %s" % (i, len(pending), c[:7], subject))
        cur, remote_tree = push_one(c, cur, remote_tree)

    api("PATCH", "/repos/%s/%s/git/refs/heads/main" % (OWNER, NAME),
        {"sha": cur, "force": False})
    print("\nmain 已更新 -> %s" % cur[:7])
    git("update-ref", "refs/remotes/origin/main", cur)
    print("本地 origin/main 引用已同步")
    return 0


if __name__ == "__main__":
    sys.exit(main())
