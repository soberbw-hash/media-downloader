# -*- coding: utf-8 -*-
"""微博解析回归：地址识别、清晰度挑选、无水印图集。

    python tests/test_weibo.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import weibo  # noqa: E402


def log(*a):
    print(*a, flush=True)


def check(ok, msg):
    if not ok:
        raise AssertionError(msg)
    log("✓", msg)


def test_mid():
    cases = {
        "https://m.weibo.cn/detail/5332536008119716": "5332536008119716",
        "https://m.weibo.cn/status/5332536008119716": "5332536008119716",
        "https://weibo.com/6288169783/5344438765488239": "5344438765488239",
        "https://weibo.com/6288169783/5344438765488239?sudaref=weibo.com": "5344438765488239",
        "https://video.weibo.com/show?fid=1034:5336219874426938": "5336219874426938",
        "https://m.weibo.cn/detail/5332536008119716/qq?wm=3333_2001": "5332536008119716",
    }
    for url, want in cases.items():
        got = weibo.mid_of(url)
        check(got == want, "%s → %s" % (url[:52], got))
    check(weibo.mid_of("https://weibo.com/") == "", "首页不算单条微博")


def test_video_page():
    check(weibo.is_video_page("https://video.weibo.com/show?fid=1034:123"), "视频专页认得出来")
    check(weibo.is_video_page("https://weibo.com/tv/show/1034:123"), "/tv/show 也算视频专页")
    check(not weibo.is_video_page("https://weibo.com/6288169783/5344438765488239"),
          "普通博文不算视频专页")


def test_base62():
    check(weibo._b62_to_id("5332536008119716") == "5332536008119716", "纯数字原样返回")
    # 4 个字符一组还原成 7 位十进制；最后一组不补零
    got = weibo._b62_to_id("abcdefgh")
    check(got.isdigit() and len(got) == 14, "8 位短 ID 还原成 14 位数字：%s" % got)
    check(weibo._b62_to_id("") == "", "空字符串不炸")


def test_pick_urls():
    page = {
        "type": "video",
        "media_info": {
            "stream_url": "https://f.video.weibocdn.com/sd.mp4",
            "stream_url_hd": "https://f.video.weibocdn.com/hd.mp4",
            "playback_list": [
                {"url": "https://f.video.weibocdn.com/p1080.mp4", "quality": "1080p"},
            ],
        },
        "urls": {"mp4_ld_mp4": "https://f.video.weibocdn.com/ld.mp4",
                 "mp4_720p_mp4": "https://f.video.weibocdn.com/720.mp4"},
    }
    urls = weibo._pick_urls(page)
    check(urls[0].endswith("p1080.mp4"), "playback_list 里的 1080P 排第一")
    check("https://f.video.weibocdn.com/720.mp4" in urls, "urls 里的 720P 也在候选里")
    check(len(urls) == len(set(urls)), "地址去重")


def test_images_no_watermark():
    data = {"pics": [
        {"url": "https://wx1.sinaimg.cn/orj360/006abc.jpg"},
        {"large": {"url": "https://wx2.sinaimg.cn/mw690/007xyz.jpg"}},
        {"url": "https://wx3.sinaimg.cn/large/008qwe.jpg"},
    ]}
    imgs = weibo._images(data)
    check(all("/osj1080/" in u for u in imgs), "图集全换成无水印的 osj1080：%s" % imgs[0])
    check(len(imgs) == 3, "三张图都在")
    check(weibo._no_watermark("https://cdn.example.com/a.mp4")
          == "https://cdn.example.com/a.mp4", "非新浪图床不动地址")


def test_live_photo():
    data = {"pics": [{"url": "https://wx1.sinaimg.cn/orj360/a.jpg",
                      "videoSrc": "https://video.weibo.com/media/play?livephoto=1"}]}
    lives = weibo._live_photos(data)
    check(len(lives) == 1 and "livephoto" in lives[0], "实况图自带的短片能取到")


if __name__ == "__main__":
    test_mid()
    test_video_page()
    test_base62()
    test_pick_urls()
    test_images_no_watermark()
    test_live_photo()
    log("ALL PASS")
