# -*- coding: utf-8 -*-
"""电子书：书目页 → 公开文件的解析（不联网，全部打桩）。

    python tests/test_books.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import books  # noqa: E402
import engines  # noqa: E402


def log(*a):
    print(*a, flush=True)


def check(ok, msg):
    if not ok:
        raise AssertionError(msg)
    log("✓", msg)


def raises(fn, needle):
    try:
        fn()
    except Exception as e:                       # noqa: BLE001
        check(needle in str(e), "报错说人话：%s" % str(e)[:60])
        return str(e)
    raise AssertionError("本该失败却没失败")


FILES = [
    {"name": "book_chocr.html.gz", "format": "ChOCR", "size": "1"},
    {"name": "book_djvu.xml", "format": "Djvu XML", "size": "9"},
    {"name": "book_lcp.epub", "format": "EPUB", "size": "99", "private": "true"},
    {"name": "book_jp2.zip", "format": "Single Page Processed JP2 ZIP", "size": "9"},
    {"name": "book.pdf", "format": "Text PDF", "size": "3000"},
    {"name": "book.epub", "format": "EPUB", "size": "800"},
    {"name": "book_djvu.txt", "format": "DjVuTXT", "size": "200"},
]

META_OPEN = {
    "metadata": {"title": "三体", "identifier": "abc", "collection": ["opensource"]},
    "files": FILES,
}
META_LEND = {
    "metadata": {"title": "三体", "identifier": "lend",
                 "access-restricted-item": "true", "collection": ["inlibrary"]},
    "files": [{"name": "lend_lcp.epub", "format": "EPUB", "size": "9", "private": "true"}],
}
META_EMPTY = {"metadata": {"title": "空条目"}, "files": [{"name": "x_chocr.html.gz"}]}


def test_is_book():
    for u in ("https://openlibrary.org/works/OL1W",
              "https://archive.org/details/abc",
              "https://www.gutenberg.org/ebooks/1342",
              "https://example.com/a/book.epub"):
        check(books.is_book(u), "认成电子书：%s" % u)
    for u in ("https://www.bilibili.com/video/BV1xx411c7mD",
              "https://pan.baidu.com/s/1abc", "https://example.com/v.mp4"):
        check(not books.is_book(u), "不当成电子书：%s" % u)


def test_pick():
    f = books.pick_file(FILES)
    check(f and f["name"] == "book.epub", "EPUB 优先，不被 chocr / DRM / pdf 抢走")
    f2 = books.pick_file([x for x in FILES if not x["name"].endswith(".epub")])
    check(f2 and f2["name"] == "book.pdf", "没有 EPUB 时选 PDF 而不是 txt")
    check(books.pick_file([{"name": "a_chocr.html.gz"}]) is None, "垃圾文件不算可下")


def test_archive():
    books._archive_meta = lambda ident, proxy="", timeout=25: META_OPEN
    info = books.resolve("https://archive.org/details/abc")
    check(info["url"].endswith("/download/abc/book.epub"), "details 页翻到 epub 直链")
    check(info["ext"] == ".epub" and info["title"] == "三体", "标题与后缀都对")
    direct = books.resolve("https://archive.org/download/abc/book.pdf")
    check(direct["url"].endswith("/download/abc/book.pdf"), "已是文件链接就原样用")


def test_blocked():
    books._archive_meta = lambda ident, proxy="", timeout=25: META_LEND
    msg = raises(lambda: books.resolve("https://archive.org/details/lend"), "只能在线借阅")
    check("下不了" in msg, "借阅书立刻判死，不再换引擎空跑")
    check(not engines.is_fatal(RuntimeError("网络超时")), "网络类失败仍会换引擎")
    check(engines.is_fatal(RuntimeError(msg)), "借阅书是终止条件")

    books._archive_meta = lambda ident, proxy="", timeout=25: META_EMPTY
    raises(lambda: books.resolve("https://archive.org/details/empty"), "没有 epub / pdf")

    # 电影条目：不能把 yt-dlp 的路堵死
    books._archive_meta = lambda ident, proxy="", timeout=25: {
        "metadata": {"title": "老电影"},
        "files": [{"name": "movie.mp4", "format": "h.264", "size": "999"}]}
    msg = raises(lambda: books.resolve("https://archive.org/details/movie"), "换视频引擎")
    check(not engines.is_fatal(RuntimeError(msg)), "只有音视频的条目仍换引擎，不空停")
    check(not books.is_book("https://archive.org/download/movie/movie.mp4"),
          "archive 上的 mp4 直链留给视频引擎")


def test_openlibrary():
    books._json = lambda url, proxy="", timeout=25: {
        "entries": [{"key": "/books/OL1M", "ocaid": ""},
                    {"key": "/books/OL2M", "ocaid": "ia123"}]}
    books._archive_meta = lambda ident, proxy="", timeout=25: META_OPEN
    info = books.resolve("https://openlibrary.org/works/OL1W")
    check(info["url"].endswith("/download/ia123/book.epub"),
          "作品页 → editions 的 ocaid → 文件（用 ocaid 拼地址，不是作品号）")

    books._json = lambda url, proxy="", timeout=25: {"entries": [{"ocaid": ""}]}
    raises(lambda: books.resolve("https://openlibrary.org/works/OL9W"), "没有公开的电子书文件")


def test_gutenberg():
    html = ('<a href="/cache/epub/1342/pg1342-images.epub">x</a>'
            '<a href="/cache/epub/1342/pg1342.epub.noimages">y</a>')
    books._get = lambda url, proxy="", timeout=25, binary=False: html
    info = books.resolve("https://www.gutenberg.org/ebooks/1342")
    check(info["url"] == "https://www.gutenberg.org/cache/epub/1342/pg1342.epub.noimages",
          "古登堡优先取无图 EPUB")
    check(info["ext"] == ".epub", "后缀取自链接")


def test_availability():
    books._archive_meta = lambda ident, proxy="", timeout=25: META_OPEN
    check(books.availability("https://archive.org/details/abc") == "可直下", "公开书标「可直下」")
    books._archive_meta = lambda ident, proxy="", timeout=25: META_LEND
    check(books.availability("https://archive.org/details/lend") == "仅在线借阅",
          "借阅书标「仅在线借阅」")
    books._archive_meta = lambda ident, proxy="", timeout=25: META_EMPTY
    check(books.availability("https://archive.org/details/empty") == "无公开文件",
          "只有垃圾文件标「无公开文件」")
    books._archive_meta = lambda ident, proxy="", timeout=25: (_ for _ in ()).throw(
        RuntimeError("连不上 archive.org"))
    check(books.availability("https://archive.org/details/x") == "", "探测失败就不标，别误导")


def test_search_labels():
    """搜索结果要把「能不能直下」写在脸上，别让用户连点三次才发现下不了。"""
    import sources
    fake = {"docs": [
        {"key": "/works/OL1W", "title": "三体", "author_name": ["刘慈欣"],
         "first_publish_year": 2008, "ia": ["abc"]},
        {"key": "/works/OL2W", "title": "三体 II", "first_publish_year": 2008,
         "ia": ["lend"]},
        {"key": "/works/OL3W", "title": "三体 III", "first_publish_year": 2010},
    ]}
    sources._fetch_json = lambda url, proxy=None, timeout=14: fake
    books.availability = lambda url, proxy="", timeout=6: (
        "可直下" if "/abc" in url else "仅在线借阅" if "/lend" in url else "")
    out = sources._openlibrary("三体", 5, None, None)
    check(out[0]["desc"].endswith("可直下") and out[0]["can_download"] is True,
          "公开书标「可直下」")
    check(out[1]["desc"].endswith("仅在线借阅") and out[1]["can_download"] is False,
          "借阅书标「仅在线借阅」，不会让人白点")
    check("直下" not in out[2]["desc"] and "借阅" not in out[2]["desc"],
          "没有 IA 编号的作品页不乱标")

    def boom(*a, **kw):
        raise RuntimeError("探测接口挂了")
    books.availability = boom
    out2 = sources._openlibrary("三体", 5, None, None)
    check(len(out2) == 3, "探测全挂时搜索结果照常返回，不耽误用")


def test_chain():
    names = [e.name for e in engines.chain_for("book")]
    check(names == ["book", "ytdlp"], "电子书链：公开文件 → yt-dlp 兜底")
    import core
    pid, pname, level = core.detect_platform("https://archive.org/details/abc")
    check((pid, pname) == ("book", "电子书"), "archive 链接识别成电子书，不是「其它站点」")
    pid2, pname2, _ = core.detect_platform("https://openlibrary.org/works/OL1W")
    check((pid2, pname2) == ("book", "电子书"), "openlibrary 同样识别成电子书")
    check(core.video_key("https://archive.org/details/abc").startswith("book:"),
          "电子书的去重 key 单独一档")
    # 回归：普通 403 / verify 失败不许再说成「触发了风控」——
    # 这正是用户截图里那句莫名其妙的提示
    for raw in ("HTTP Error 403: Forbidden", "verify failed", "risk of something"):
        title, _ = core.explain_error(raw)
        check(title != "触发了风控", "不再把 %r 说成风控" % raw)
    check(core.explain_error("HTTP Error 412: Precondition Failed")[0] == "触发了风控",
          "真正的 412 仍然识别为风控")


if __name__ == "__main__":
    test_is_book()
    test_pick()
    test_archive()
    test_blocked()
    test_openlibrary()
    test_gutenberg()
    test_availability()
    test_search_labels()
    test_chain()
    print("\n全部通过")
