# -*- coding: utf-8 -*-
"""Mac QuickTime 兼容性回归：1.24.3 之后修的「不兼容的部分媒体」弹窗。

跑法：python tests/test_mac_compat.py
没装 ffmpeg 时只跑纯逻辑断言，真机转码部分自动跳过。
"""
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core  # noqa: E402

FF = shutil.which("ffmpeg")
TMP = tempfile.mkdtemp(prefix="qtcompat_")
ok = 0


def log(*a):
    print(*a, flush=True)


def check(cond, msg):
    global ok
    assert cond, msg
    ok += 1
    log("  OK", msg)


def make(name, vargs, aargs=("-c:a", "aac", "-b:a", "96k")):
    p = os.path.join(TMP, name)
    cmd = [FF, "-y", "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-i", "testsrc=size=320x180:rate=15:duration=1",
           "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
           "-shortest"] + list(vargs) + list(aargs) + [p]
    r = subprocess.run(cmd, capture_output=True, timeout=90)
    if r.returncode != 0 or not os.path.isfile(p):
        raise RuntimeError("造样本失败 %s: %s" % (name, (r.stderr or b"")[:200]))
    return p


log("=== 1. 选流表达式 ===")
check(core.format_for("best") == "bestvideo*+bestaudio/best", "Windows 保持原 best，画质优先")
check(core.format_for("1080") == "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
      "Windows 1080 表达式没变")
check(core.format_for("720") == "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
      "Windows 720 表达式没变")
for q in ("best", "1080", "720"):
    f = core.format_for(q, mac=True)
    check(f.startswith("bestvideo[vcodec^=avc1]"), "Mac %s 优先 H.264" % q)
    check("acodec^=mp4a" in f, "Mac %s 优先 AAC" % q)
    check(f.endswith("/best"), "Mac %s 有兜底，不会为了兼容下不了" % q)
check("height<=1080" in core.format_for("1080", mac=True), "Mac 1080 仍限清晰度")
check(core.format_for("audio", mac=True) == "bestaudio/best", "纯音频不受影响")

log("=== 2. 兼容判定（不依赖真 ffmpeg） ===")
CASES = [
    ("a.mp4", {"vcodec": "h264", "vrest": " yuv420p(progressive), 1920x1080", "acodec": "aac"}, True),
    ("a.mov", {"vcodec": "h264", "vrest": " yuv420p", "acodec": "mp3"}, True),
    ("a.mp4", {"vcodec": "h264", "vrest": " yuv420p", "acodec": ""}, True),
    ("a.mp4", {"vcodec": "vp9", "vrest": " yuv420p", "acodec": "opus"}, False),
    ("a.mp4", {"vcodec": "av01", "vrest": " yuv420p", "acodec": "aac"}, False),
    ("a.mp4", {"vcodec": "hevc", "vrest": " yuv420p10le", "acodec": "aac"}, False),
    ("a.mp4", {"vcodec": "h264", "vrest": " yuv420p10le", "acodec": "aac"}, False),
    ("a.mp4", {"vcodec": "h264", "vrest": " yuv422p10le", "acodec": "aac"}, False),
    ("a.mp4", {"vcodec": "h264", "vrest": " yuv422p", "acodec": "aac"}, False),
    ("a.mp4", {"vcodec": "h264", "vrest": " yuv420p", "acodec": "opus"}, False),
    ("a.mkv", {"vcodec": "h264", "vrest": " yuv420p", "acodec": "aac"}, False),
    ("a.webm", {"vcodec": "vp9", "vrest": " yuv420p", "acodec": "opus"}, False),
]
for name, info, want in CASES:
    got = core.quicktime_compatible(name, info)
    check(got is want, "%s / %s / %s -> %s" % (name, info["vcodec"],
                                               info["vrest"].strip() or "-", want))
check(core.quicktime_compatible("a.mp3", {"vcodec": "png", "vrest": "", "acodec": "mp3"}) is True,
      "音频文件不参与视频兼容处理")
check(core.quicktime_compatible("a.mp4", None) in (True, False), "探不到流时不抛异常")

log("=== 3. 真机转码 ===")
if not FF:
    log("  跳过（本机没有 ffmpeg）")
else:
    real_ff = core.paths.ffmpeg_path
    core.paths.ffmpeg_path = lambda: FF

    try:
        src = make("cam_422.mp4", ["-c:v", "libx264", "-pix_fmt", "yuv422p"])
        info = core._probe_streams(src)
        check(info is not None and info["vcodec"] == "h264", "能从 ffmpeg -i 读到视频流（不靠 ffprobe）")
        check("yuv422p" in info["vrest"], "样本确实是 4:2:2")
        check(not core.quicktime_compatible(src), "4:2:2 判为 QuickTime 不兼容")
        out, note = core.ensure_mac_playable(src)
        check(out.endswith(".mp4") and os.path.isfile(out), "转码产出了 mp4")
        check(out == src and os.path.isfile(src), "同名 mp4 被原子替换，没有多出一份文件")
        check(core.quicktime_compatible(out), "转完能被 QuickTime 播")
        after = core._probe_streams(out)
        check("yuv420p10le" not in after["vrest"], "转完是 8bit，不是 10bit")
        check(after["acodec"] == "aac", "转完音频是 AAC")
        check(note == "", "转码成功不显示报错文案")

        mkv = make("container.mkv", ["-c:v", "libx264", "-pix_fmt", "yuv420p"])
        check(not core.quicktime_compatible(mkv), "mkv 容器判为不兼容")
        out2, note2 = core.ensure_mac_playable(mkv)
        check(out2.endswith(".mp4") and os.path.isfile(out2), "mkv 换成了 mp4")
        check(not os.path.exists(mkv), "原 mkv 已清理")
        check(core.quicktime_compatible(out2), "换容器后能播")

        good = make("already_ok.mp4", ["-c:v", "libx264", "-pix_fmt", "yuv420p",
                                       "-movflags", "+faststart"])
        check(core.quicktime_compatible(good), "本来就能播的 mp4 判为兼容")
        before = (os.path.getsize(good), os.path.getmtime(good))
        out3, note3 = core.ensure_mac_playable(good)
        check(out3 == good, "能播的文件路径不变")
        check((os.path.getsize(good), os.path.getmtime(good)) == before,
              "能播的文件一个字节都没动")
        check(note3 == "", "能播的文件没有多余提示")

        bad = make("fail_case.mp4", ["-c:v", "libx264", "-pix_fmt", "yuv422p"])
        orig_args = core._h264_encoder_args
        core._h264_encoder_args = lambda ff: ["-c:v", "encoder_that_does_not_exist"]
        out4, note4 = core.ensure_mac_playable(bad)
        core._h264_encoder_args = orig_args
        check(out4 == bad and os.path.isfile(bad), "转码失败时原文件还在")
        check("IINA" in note4, "转码失败给出路，而不是英文异常")
        check(not [x for x in os.listdir(TMP) if x.endswith(".compat.mp4")],
              "失败没留下临时文件")

        core.paths.ffmpeg_path = lambda: ""
        no_ff, note5 = core.ensure_mac_playable(bad)
        check(no_ff == bad and os.path.isfile(bad), "没有 ffmpeg 时原文件还在")
        check("IINA" in note5, "没有 ffmpeg 也给得出路")
    finally:
        core.paths.ffmpeg_path = real_ff

log("=== 4. 用 yt-dlp 真解析，确认选得出流 ===")
try:
    import yt_dlp
except ImportError:
    yt_dlp = None
if not yt_dlp:
    log("  跳过（没装 yt-dlp）")
else:
    ydl = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True})

    def mk(fid, vcodec, acodec, ext, height=0):
        return {"format_id": fid, "vcodec": vcodec, "acodec": acodec, "ext": ext,
                "height": height, "protocol": "https", "filesize": 1000000,
                "url": "https://example.invalid/" + fid}

    for q in ("best", "1080", "720"):
        ydl.build_format_selector(core.format_for(q, mac=True))
    check(True, "三档画质的 Mac 表达式 yt-dlp 都能解析")

    sel = ydl.build_format_selector(core.format_for("1080", mac=True))
    mixed = [mk("av1-1080", "av01.0.05M.08", "none", "mp4", 1080),
             mk("h264-720", "avc1.64001f", "none", "mp4", 720),
             mk("aac", "none", "mp4a.40.2", "m4a"),
             mk("opus", "none", "opus", "webm")]
    got = [x["format_id"] for x in ydl._select_formats(mixed, sel)]
    check(len(got) == 1 and "h264-720" in got[0] and "aac" in got[0],
          "有 H.264 时选 H.264 + AAC，不挑 AV1")

    only_av1 = [mk("av1-1080", "av01.0.05M.08", "none", "mp4", 1080),
                mk("opus", "none", "opus", "webm")]
    fallback = [x["format_id"] for x in ydl._select_formats(only_av1, sel)]
    check(bool(fallback), "只有 AV1 分离流也能选到（转码兜底），不会下不了")

    for q in ("best", "1080", "720"):
        s = ydl.build_format_selector(core.format_for(q))
        check(bool(ydl._select_formats(mixed, s)), "Windows %s 选流没被改坏" % q)

log("=== 5. 只有 Mac 走兼容处理 ===")
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core.py"),
           encoding="utf-8").read()
check("mac=(sys.platform == \"darwin\")" in src, "选流按平台区分")
check("if sys.platform == \"darwin\" and not imgs:" in src, "转码只在 Mac、只针对视频")

shutil.rmtree(TMP, ignore_errors=True)
log("\n全部通过：%d 项" % ok)
