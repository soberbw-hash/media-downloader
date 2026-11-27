# -*- coding: utf-8 -*-
"""关键词意图识别：判断用户到底在找什么（音乐 / 音效 / 视频 / 电影 / 电子书）。

只做「够用」的规则判断，不引入模型、不联网：
  · 强提示词（音效 / 电子书 / 歌 / 片名）权重高，命中就基本定了
  · 具体名词（雨、火、脚步、爆炸…）当辅助证据，几个一起出现才生效
  · 判断不出来就返回 video —— B站 / YouTube 几乎什么都能搜到，最不容易错

对外只暴露 guess(q) -> dict：
  {"kind": "sfx", "label": "音效", "score": 5, "hits": ["音效", "火"]}
"""
import re

# 强提示：出现一次就基本能定性
STRONG = {
    "sfx": ["音效", "声效", "拟音", "音效素材", "sound effect", "sound effects",
            "sfx", "foley", "whoosh", "swoosh", "riser"],
    "music": ["歌曲", "歌名", "歌手", "专辑", "翻唱", "纯音乐", "bgm", "配乐",
              "音乐", "伴奏", "remix", "playlist", "歌单"],
    "movie": ["电影", "片名", "预告片", "预告", "影视", "豆瓣", "解说", "影评"],
    "book": ["电子书", "epub", "mobi", "azw3", "pdf", "txt", "小说", "原著",
             "书名", "作者", "古登堡", "ebook"],
    "video": ["视频素材", "素材", "空镜", "运镜", "混剪", "转场", "镜头",
              "实拍", "4k", "8k", "green screen", "绿幕"],
}

# 辅助名词：同类命中两个以上才明显加分，避免「火车」把音乐带偏
NOUNS = {
    "sfx": ["雨声", "雷声", "风声", "水声", "海浪声", "火焰", "爆炸", "枪声",
            "玻璃", "刹车", "脚步", "走路", "掌声", "笑声", "哭声", "心跳",
            "开门", "关门", "门铃", "打字", "键盘", "鼠标", "点击", "铃声",
            "闹钟", "水滴", "海浪", "鸟叫", "虫鸣", "人群", "汽车", "摩托",
            "火车", "飞机", "引擎", "警报",
            # 单字歧义大（火锅 / 火山 / 风衣），只当辅助证据
            "雨", "雷", "风", "火", "烧", "枪", "铃",
            "rain", "thunder", "fire", "wind", "explosion", "gun", "glass",
            "footstep", "applause", "laugh", "doorbell", "typing", "click",
            "engine", "alarm", "water", "ocean"],
    "music": ["周杰伦", "林俊杰", "陈奕迅", "邓紫棋", "薛之谦", "李荣浩"],
    "movie": ["哪吒", "流浪地球", "肖申克", "星际穿越"],
}

LABELS = {"sfx": "音效", "music": "音乐", "movie": "电影", "video": "视频", "book": "电子书"}

# 只有这些类型能直接吃聚合搜索（音乐 / 电影走各自的接口）
AGG_KINDS = ("sfx", "video", "book")


def _hit(text, words):
    return [w for w in words if w and w in text]


def guess(q):
    kw = (q or "").strip()
    text = kw.lower()
    if not text:
        return {"kind": "video", "label": LABELS["video"], "score": 0, "hits": []}

    score = {k: 0 for k in STRONG}
    hits = {k: [] for k in STRONG}

    for kind, words in STRONG.items():
        hs = _hit(text, words)
        if hs:
            # 强提示按「命中一个 +4、多命中再加」算
            score[kind] += 4 + min(3, len(hs) - 1)
            hits[kind] += hs
    for kind, words in NOUNS.items():
        hs = _hit(text, words)
        if not hs:
            continue
        firm = [w for w in hs if len(w) > 1]      # 双字以上：够明确
        weak = [w for w in hs if len(w) == 1]     # 单字：只当辅助
        add = min(4, len(firm) * 2)
        if firm and weak:
            add += min(2, len(weak))
        if add:
            score[kind] += add
            hits[kind] += hs

    best = max(score, key=lambda k: score[k])
    if score[best] <= 0:
        # 一点线索都没有时，别硬判成「视频」。
        # 搜「告白气球」被判成视频，结果只有 B站 / YouTube，歌一条都看不见 ——
        # 这是最难接受的一类误判。这里只说实话：看不出是哪一类，
        # 让调用方把音乐和视频一起搜（前端混排，音乐排前面）。
        return {"kind": "video", "label": LABELS["video"], "score": 0,
                "hits": [], "weak": True, "mixed": ["music", "video"]}
    return {"kind": best, "label": LABELS[best], "score": score[best],
            "hits": hits[best][:4], "weak": False}


# 只起「说明类型」作用的词：搜的时候要摘掉，否则「三体 电子书」
# 会被原样丢给书源，书源当然搜不到《三体》
FILLERS = ["的", "相关", "内容", "素材", "合集", "打包", "下载"]


def clean(q, kind):
    """把类型词从关键词里摘掉，只留真正要找的东西。

    「火烧的音效」→「火烧」，「三体 电子书」→「三体」，
    「周杰伦 歌曲」→「周杰伦」。摘空了就退回原词，别把查询搞没。
    """
    text = (q or "").strip()
    if not text:
        return text
    words = sorted(set(STRONG.get(kind, []) + FILLERS), key=len, reverse=True)
    for w in words:
        if w:
            text = re.sub(re.escape(w), " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" ,，、/|")
    return text or (q or "").strip()


def source_ids(kind, all_sources):
    """按意图挑源。sfx 只挑音效源，book 只挑电子书源，video 挑视频源。

    挑不到时返回 None，交给调用方按默认来（不硬塞一个空列表导致「没有可用的来源」）。
    """
    want = {"video": "video", "sfx": "audio", "book": "book"}.get(kind)
    if not want:
        return None
    ids = [s["id"] for s in all_sources if (s.get("kind") or "video") == want]
    return ids or None
