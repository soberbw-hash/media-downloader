# -*- coding: utf-8 -*-
"""关键词意图：模糊词不许硬判成一类（不联网）。

    python tests/test_intent.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import intent  # noqa: E402


def log(*a):
    print(*a, flush=True)


def check(ok, msg):
    if not ok:
        raise AssertionError(msg)
    log("✓", msg)


def test_bare_word_not_video():
    """裸词不再一律判成视频 —— 搜「告白气球」出来一堆 B站 视频，是最难接受的误判。"""
    for q in ("告白气球", "海底", "城市夜景", "火锅"):
        g = intent.guess(q)
        check(g["weak"] is True, "「%s」标成「看不出类型」" % q)
        check("music" in g["mixed"] and "video" in g["mixed"],
              "「%s」两类一起搜：%s" % (q, g["mixed"]))
        check(g["label"] != intent.LABELS["video"] or g["weak"],
              "「%s」不硬说成视频" % q)


def test_strong_hints_kept():
    """有明确提示词的照旧单类，别为了混排把准头弄丢。"""
    cases = [
        ("周杰伦", "music"),
        ("雨声", "sfx"),
        ("城市夜景 4K 空镜", "video"),
        ("三体 电子书", "book"),
        ("哪吒 电影", "movie"),
        ("火烧的音效", "sfx"),
    ]
    for q, want in cases:
        g = intent.guess(q)
        check(not g["weak"], "「%s」判定很明确" % q)
        check(g["kind"] == want, "「%s」→ %s（要 %s）" % (q, g["kind"], want))
        check("mixed" not in g or not g["mixed"], "「%s」不走混排" % q)


def test_clean_and_sources():
    """摘类型词、按类挑源这些老约定没被带坏。"""
    check(intent.clean("三体 电子书", "book") == "三体", "摘掉「电子书」只剩书名")
    check(intent.clean("火烧的音效", "sfx") == "火烧", "摘掉「音效」只剩声音")
    check(intent.clean("音效", "sfx") == "音效", "摘空了就退回原词，别把查询搞没")
    src = [{"id": "b", "kind": "video"}, {"id": "y", "kind": "video"},
           {"id": "s", "kind": "audio"}]
    check(intent.source_ids("video", src) == ["b", "y"], "视频只挑视频源")
    check(intent.source_ids("sfx", src) == ["s"], "音效只挑音效源")
    check(intent.source_ids("music", src) is None, "音乐交给调用方自己处理")


def test_empty():
    g = intent.guess("")
    check(g["score"] == 0 and g["kind"] == "video", "空查询不炸")


if __name__ == "__main__":
    test_bare_word_not_video()
    test_strong_hints_kept()
    test_clean_and_sources()
    test_empty()
    log("\n全部通过")
