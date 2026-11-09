# -*- coding: utf-8 -*-
"""听歌识曲：丢一段音频进去，拿回歌名和歌手。

用 Shazam 认。为什么是它 —— 免 key、免登录，中文 locale 还能直接返回
中文名（「告白气球 / 周杰伦」），拿到就能丢给国内平台搜下载。
别的几家（ACRCloud、AudD）都要注册拿 key，用户机器上没法免配置跑起来。

识别这件事本身的边界（别承诺做不到的事）：
- 认得出：有正式发行的歌，哪怕只录到 5 秒副歌
- 认不出：纯音乐 / 翻唱 / 现场改编得太狠 / 录音环境太吵

因为浏览器录的是 webm/opus、剪映导的是 mp4，格式五花八门，
统一先用 ffmpeg 转成一小段 mp3 再交给 Shazam。
"""

import asyncio
import os
import re
import subprocess
import tempfile

import paths

BASE = os.path.dirname(os.path.abspath(__file__))

# 取多长去识别。12 秒够 Shazam 认了，再长只是白传。
CLIP_SECONDS = 12
# 认两次：先挑一段，不行再换一段。都是为了躲开前奏和间奏。
MAX_TRIES = 2
# 太短了认不出来，别浪费一次请求
MIN_BYTES = 3 * 1024


def ffmpeg_path():
    """随包 bin/ → PATH → imageio-ffmpeg。Mac 版随包带，不用装 Homebrew。"""
    return paths.ffmpeg_path()


def _duration(src):
    """拿音频时长（秒）。拿不到就返回 0，后面按「从头取」处理。

    不依赖 ffprobe：Mac 随包的静态 ffmpeg 没带 ffprobe，直接从
    `ffmpeg -i` 的 stderr 里读 Duration 就行，Windows 也一样能用。
    """
    ff = ffmpeg_path()
    if not ff:
        return 0.0
    try:
        r = subprocess.run([ff, "-hide_banner", "-i", src],
                           capture_output=True, text=True, timeout=30)
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",
                      (r.stderr or "") + (r.stdout or ""))
        if m:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    except Exception:
        pass
    return 0.0


def _cut(src, dst, start):
    """截一段单声道 mp3 出来。Shazam 只认常见音频格式，
    浏览器录的 webm/opus、视频文件都得在这儿归一。"""
    ff = ffmpeg_path()
    if not ff:
        raise RuntimeError("找不到 ffmpeg，识曲用不了")
    cmd = [ff, "-y", "-v", "quiet", "-ss", "%.2f" % max(0.0, start),
           "-t", str(CLIP_SECONDS), "-i", src, "-vn",
           "-ac", "1", "-ar", "44100", "-b:a", "96k", dst]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=90)
    except subprocess.TimeoutExpired:
        raise RuntimeError("音频处理超时了，换个短一点的片段试试")
    if r.returncode != 0 or not os.path.isfile(dst) or os.path.getsize(dst) < 1024:
        raise RuntimeError("这段音频读不出来，换个文件试试")


def _ask_shazam(path):
    """问 Shazam。返回 track 字典，没认出来返回空字典。"""
    try:
        from shazamio import Shazam
    except Exception:
        raise RuntimeError("识曲组件没装上，这个功能暂时用不了（重装一下软件就好）")

    async def run():
        # 中文 locale：不然《告白气球》会返回 "Love Confession"，
        # 拿这个去国内平台搜是一条都搜不到的
        sh = Shazam(language="zh-CN")
        return await sh.recognize(path)

    try:
        out = asyncio.run(run())
    except Exception as e:
        msg = str(e)[:80]
        raise RuntimeError("连不上识曲服务（%s），检查一下网络" % msg)
    return (out or {}).get("track") or {}


def _apple_url(tr):
    try:
        for opt in (tr.get("hub") or {}).get("options") or []:
            for act in opt.get("actions") or []:
                if act.get("uri"):
                    return act["uri"]
    except Exception:
        pass
    return ""


def recognize(path):
    """识别一段音频（可以是视频文件）。

    成功返回：
        {"title": 歌名, "artist": 歌手, "query": 拿去找下载的搜索词,
         "cover": 封面, "genre": 流派, "apple": Apple Music 链接}
    认不出抛 RuntimeError，消息是给人看的。
    """
    if not path or not os.path.isfile(path):
        raise RuntimeError("没拿到音频")
    if os.path.getsize(path) < MIN_BYTES:
        raise RuntimeError("这段太短了，认不出来。至少录 5 秒")

    dur = _duration(path)
    # 从哪儿开始截：躲开前奏。歌太长就往后跳一点，短的就从头来。
    if dur >= 45:
        starts = [dur * 0.25, dur * 0.6]
    elif dur >= 15:
        starts = [0.0, dur * 0.5]
    else:
        starts = [0.0]
    starts = starts[:MAX_TRIES]

    tmpdir = tempfile.mkdtemp(prefix="sr_")
    try:
        last_err = ""
        for i, start in enumerate(starts):
            clip = os.path.join(tmpdir, "c%d.mp3" % i)
            try:
                _cut(path, clip, start)
            except RuntimeError as e:
                last_err = str(e)
                continue
            tr = _ask_shazam(clip)
            if tr.get("title"):
                artist = tr.get("subtitle") or ""
                title = tr.get("title")
                return {
                    "title": title,
                    "artist": artist,
                    # 搜索词给全一点，国内平台对「歌名 歌手」的命中率更高
                    "query": ("%s %s" % (title, artist)).strip(),
                    "cover": (tr.get("images") or {}).get("coverart") or "",
                    "genre": (tr.get("genres") or {}).get("primary") or "",
                    "apple": _apple_url(tr),
                }
        raise RuntimeError(last_err or "没认出来。可能声音太小、环境太吵，"
                                       "或者这歌本身没在库里")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
