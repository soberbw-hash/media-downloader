# -*- coding: utf-8 -*-
"""把本地未推的提交重建成「和 GitHub 存储习惯完全一致」的对象。

差异只有一处：GitHub 创建 commit 时 message 不留结尾换行，而 git commit 一定会留。
所以本地 SHA 和远端 SHA 永远差一位。这里把本地对象按远端习惯重建一遍
（tree 不变、author/committer 不变、message 去掉结尾换行），
重建后本地 SHA 与远端一致，推送时就能全程校验。

只动本地对象，不联网，不改工作区文件。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def git(*args):
    p = subprocess.run(["git", "-c", "core.quotepath=false"] + list(args), cwd=ROOT,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode:
        raise SystemExit("git %s failed: %s" % (" ".join(args), p.stderr.decode("utf-8", "replace")))
    return p.stdout.decode("utf-8", "replace")


def main():
    if git("status", "--porcelain").strip():
        raise SystemExit("工作区不干净，先提交")

    remote = git("rev-parse", "origin/main").strip()
    head = git("rev-parse", "HEAD").strip()
    pending = git("rev-list", "--reverse", remote + ".." + head).split()
    if not pending:
        print("没有待推提交")
        return 0

    print("基线 origin/main: %s" % remote[:7])
    new_parent = remote
    for c in pending:
        raw = git("cat-file", "-p", c)
        block, _, msg = raw.partition("\n\n")
        lines = block.split("\n")
        tree = [l for l in lines if l.startswith("tree ")][0]
        author = [l for l in lines if l.startswith("author ")][0]
        committer = [l for l in lines if l.startswith("committer ")][0]

        body = "%s\nparent %s\n%s\n%s\n\n%s" % (tree, new_parent, author, committer,
                                               msg.rstrip("\n"))
        p = subprocess.run(["git", "hash-object", "-t", "commit", "-w", "--stdin"],
                           cwd=ROOT, input=body.encode("utf-8"),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if p.returncode:
            raise SystemExit(p.stderr.decode("utf-8", "replace"))
        new_sha = p.stdout.decode().strip()
        subject = git("log", "-1", "--format=%s", c).strip()
        print("  %s -> %s  %s" % (c[:7], new_sha[:7], subject))
        new_parent = new_sha

    git("reset", "--hard", new_parent)
    git("update-ref", "refs/remotes/origin/main", remote)
    print("\nHEAD 已重建到 %s（工作区文件未变）" % new_parent[:7])
    print("现在可以跑 python tools/gh_push_all.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
