# -*- coding: utf-8 -*-
"""附件归拢：封面/字幕/弹幕/简介收进同名文件夹，文件夹跟着命名模板走。不走网络。"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core


def make_task():
    t = core.Task(1, "https://x/y", "bilibili", "哔哩哔哩")
    t.title = "测试标题"
    t.author = "测试作者"
    t.duration = 30
    t.desc = "简介内容"
    return t


def main():
    fails = []

    # —— 1. 命名模板生效：字幕是改名前(旧名)落盘的，简介是改名后写的
    tmp = tempfile.mkdtemp(prefix="sidecar1_")
    try:
        media = os.path.join(tmp, "旧名字.mp4")
        open(media, "wb").write(b"0" * 100)
        open(os.path.join(tmp, "旧名字.zh-Hans.srt"), "w", encoding="utf-8").write("1\n")
        cfg = {"out_dir": tmp, "name_tpl": "{date}_{platform}_{author}_{title}",
               "save_desc": True, "save_danmaku": False, "save_cover": False}
        m = core.Manager(cfg)
        t = make_task()
        m._post_process(t, media)
        stem = core.render_name(cfg["name_tpl"], t)
        if not os.path.isfile(os.path.join(tmp, stem + ".mp4")):
            fails.append("视频没按模板改名: %s" % sorted(os.listdir(tmp)))
        folder = os.path.join(tmp, stem)
        if not os.path.isdir(folder):
            fails.append("没建同名文件夹: %s" % sorted(os.listdir(tmp)))
        else:
            inner = sorted(os.listdir(folder))
            if stem + ".txt" not in inner:
                fails.append("简介没进文件夹: %s" % inner)
            if stem + ".zh-Hans.srt" not in inner:
                fails.append("字幕没换新名进文件夹: %s" % inner)
            if "旧名字.zh-Hans.srt" in inner:
                fails.append("字幕还是旧名: %s" % inner)
        outside = [f for f in os.listdir(tmp) if f.endswith((".txt", ".srt"))]
        if outside:
            fails.append("附件还散在外面: %s" % outside)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # —— 2. 什么附件都没开 → 不建文件夹
    tmp = tempfile.mkdtemp(prefix="sidecar2_")
    try:
        media = os.path.join(tmp, "clip.mp4")
        open(media, "wb").write(b"0" * 100)
        cfg = {"out_dir": tmp, "name_tpl": "", "save_desc": False,
               "save_danmaku": False, "save_cover": False}
        core.Manager(cfg)._post_process(make_task(), media)
        if os.path.isdir(os.path.join(tmp, "clip")):
            fails.append("没附件却建了文件夹")
        if not os.path.isfile(media):
            fails.append("视频被动了")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # —— 3. 不用命名模板：附件也进同名文件夹（跟原标题一致）
    tmp = tempfile.mkdtemp(prefix="sidecar3_")
    try:
        media = os.path.join(tmp, "原标题.mp4")
        open(media, "wb").write(b"0" * 100)
        open(os.path.join(tmp, "原标题.cover.jpg"), "wb").write(b"1" * 100)
        cfg = {"out_dir": tmp, "name_tpl": "", "save_desc": False,
               "save_danmaku": False, "save_cover": False}
        core.Manager(cfg)._post_process(make_task(), media)
        folder = os.path.join(tmp, "原标题")
        if not os.path.isdir(folder):
            fails.append("没建文件夹: %s" % sorted(os.listdir(tmp)))
        elif "原标题.cover.jpg" not in os.listdir(folder):
            fails.append("封面没进文件夹: %s" % sorted(os.listdir(folder)))
        if not os.path.isfile(media):
            fails.append("视频被动了")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if fails:
        print("失败：")
        for f in fails:
            print(" -", f)
        print("\n== 失败 ==")
        return 1
    print("模板改名+旧名字幕换新名、无附件不建夹、无模板跟原标题 —— 全对")
    print("\n== 通过 ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
