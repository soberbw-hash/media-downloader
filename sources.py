# -*- coding: utf-8 -*-
"""聚合搜索的「来源插件表」。

学的是网上那些资源搜索插件 / 元搜索引擎的做法：
一个来源 = 一个独立适配器，只管「给我关键词，吐出统一格式的卡片」；
谁快谁慢、谁挂了、结果怎么合并去重排序，全交给 aggregate.py，适配器不操心。

统一卡片字段：
  platform / id / kind / title / author / cover / duration / desc
  url       详情页（给人看、可溯源）
  download  真正丢给下载管线的地址（直链或页面链接）
  source / source_name   来自哪个源

只接公开、可合法访问的搜索接口与免费素材站；不碰登录墙、不解析付费内容。
"""
import concurrent.futures as cf
import html as html_mod
import json
import re
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


class SkipSource(Exception):
    """这个源本次不适用（不是故障），说人话告诉用户就行。"""


# ---------- 内置来源 ----------
# priority 越小越靠前；timeout 是单源最长等待时间（秒）
BUILTIN = [
    {"id": "bili", "name": "B站", "kind": "video", "priority": 1, "timeout": 12,
     "group": "中文视频", "note": "中文关键词最准"},
    {"id": "mixkit", "name": "Mixkit 视频", "kind": "video", "priority": 2, "timeout": 14,
     "group": "免费素材", "note": "免费可商用视频，直链可下；只认英文关键词"},
    {"id": "mixkit_sfx", "name": "Mixkit 音效", "kind": "audio", "priority": 3, "timeout": 12,
     "group": "免费音效", "note": "免费可商用，能直接下 wav；中文词会自动翻成英文"},
    {"id": "youtube", "name": "YouTube", "kind": "video", "priority": 4, "timeout": 12,
     "group": "海外视频", "note": "需要网络能到 Google"},
    {"id": "freesound", "name": "Freesound", "kind": "audio", "priority": 6, "timeout": 25,
     "group": "音效", "note": "几十万条环境声，能下预览 mp3（商用要署名）", "slow": True},
]


# 中文关键词 → Mixkit 能认的英文分类词。
# 素材站是英文站，中文丢过去只会得到空页，所以先做个常用词映射；
# 映射不出来的中文词就跳过这个源，并提示用户换英文，别假装搜过。
EN_WORDS = [
    ("城市夜景", "city"), ("延时", "timelapse"), ("航拍", "aerial"), ("慢动作", "slow-motion"),
    ("夜景", "night"), ("城市", "city"), ("日落", "sunset"), ("日出", "sunrise"),
    ("星空", "stars"), ("天空", "sky"), ("云", "cloud"), ("雨", "rain"),
    ("雪", "snow"), ("海", "ocean"), ("沙滩", "beach"), ("山", "mountain"),
    ("森林", "forest"), ("花", "flower"), ("水", "water"), ("火", "fire"),
    ("光", "light"), ("霓虹", "neon"), ("烟花", "fireworks"), ("街", "street"),
    ("车", "car"), ("交通", "traffic"), ("人", "people"), ("人群", "crowd"),
    ("猫", "cat"), ("狗", "dog"), ("鸟", "bird"), ("咖啡", "coffee"),
    ("美食", "food"), ("办公室", "office"), ("科技", "technology"),
    ("旅行", "travel"), ("春天", "spring"), ("夏天", "summer"),
    ("秋天", "autumn"), ("冬天", "winter"), ("风", "wind"), ("雾", "fog"),
    ("城市夜景", "city"), ("建筑", "architecture"), ("跑步", "running"),
    ("舞蹈", "dance"), ("运动", "sport"), ("婴儿", "baby"), ("家庭", "family"),
]

# 音效场景的词。混在同一个表里，因为视频源（Mixkit）和音效源吃的是同一套英文。
# Mixkit 音效是「分类页」不是关键词搜索，词要尽量是它真有的单数分类词。
EN_WORDS += [
    ("雨滴", "raindrops"), ("雨声", "rain"), ("下雨", "rain"), ("暴雨", "rain"),
    ("雷声", "thunder"), ("打雷", "thunder"), ("雷电", "thunder"),
    ("海浪", "ocean"), ("浪声", "waves"), ("水声", "water"), ("水流", "water"),
    ("溪流", "stream"), ("瀑布", "waterfall"), ("泡泡", "bubbles"),
    ("脚步", "footsteps"), ("走路", "footsteps"), ("跑步声", "footsteps"),
    ("掌声", "applause"), ("鼓掌", "applause"), ("欢呼", "cheering"),
    ("笑声", "laughter"), ("大笑", "laughter"), ("哭声", "crying"),
    ("婴儿哭", "baby"), ("尖叫", "scream"), ("叹气", "sigh"),
    ("心跳", "heartbeat"), ("呼吸", "breathing"), ("打呼", "snoring"),
    ("亲吻", "kiss"), ("口哨", "whistle"), ("咳嗽", "cough"),
    ("键盘", "keyboard"), ("打字", "typing"), ("鼠标", "mouse"),
    ("点击", "click"), ("快门", "camera"), ("相机", "camera"),
    ("相机快门", "camera"), ("拍照", "camera"),
    ("敲门", "knock"), ("关门", "door"), ("开门", "door"), ("门铃", "doorbell"),
    ("关门声", "door"), ("开门声", "door"),
    ("汽车", "car"), ("喇叭", "horn"), ("引擎", "engine"), ("发动机", "engine"),
    ("摩托", "motorcycle"), ("自行车", "bicycle"), ("火车", "train"),
    ("地铁", "subway"), ("飞机", "airplane"), ("直升机", "helicopter"),
    ("船", "boat"), ("警笛", "siren"), ("警报", "alarm"), ("救护车", "ambulance"),
    ("钟声", "clock"), ("铃声", "bell"), ("上课铃", "bell"),
    ("玻璃", "glass"), ("碎玻璃", "glass"), ("破碎", "crash"), ("摔", "crash"),
    ("爆炸", "explosion"), ("枪声", "gun"), ("开枪", "gun"), ("拳", "punch"),
    ("打击", "punch"), ("金属", "metal"), ("木头", "wood"), ("纸张", "paper"),
    ("翻书", "paper"), ("翻页", "paper"), ("泡泡纸", "bubbles"),
    ("转场", "whoosh"), ("嗖", "whoosh"), ("风切", "whoosh"),
    ("上升音", "riser"), ("下降音", "downlifter"), ("重低音", "bass"),
    ("鼓", "drum"), ("鼓点", "drum"), ("节奏", "beat"),
    ("提示音", "notification"), ("消息", "message"), ("错误", "error"),
    ("成功", "success"), ("金币", "coin"), ("升级", "level up"),
    ("开关", "switch"), ("切换", "switch"), ("开机", "startup"),
    ("电话", "phone"), ("振动", "vibrate"), ("闹钟", "alarm"),
    ("烟花", "fireworks"), ("篝火", "fire"), ("火焰", "fire"),
    ("夜晚", "night"), ("森林", "forest"), ("鸟叫", "birds"), ("虫鸣", "crickets"),
    ("猫叫", "cat"), ("狗叫", "dog"), ("狼嚎", "wolf"), ("马", "horse"),
    ("雨林", "jungle"), ("雪", "snow"), ("冰", "ice"),
]

# 日常剪辑真会用到的词，再补一轮。
# 宗旨是「中文随手打，英文音效也能搜到」，所以宁可多收，认错一次总比搜不到强。
EN_WORDS += [
    # 天气 / 自然
    ("小雨", "rain"), ("大雨", "rain"), ("毛毛雨", "rain"), ("滴答", "raindrops"),
    ("水滴", "water drop"), ("河流", "river"), ("海浪声", "waves"), ("涨潮", "waves"),
    ("风吹", "wind"), ("刮风", "wind"), ("龙卷风", "wind"), ("沙尘", "wind"),
    ("蝉鸣", "cicada"), ("蟋蟀", "crickets"), ("蛙叫", "frog"), ("海浪拍打", "waves"),
    # 人声 / 情绪
    ("吵架", "arguing"), ("打架", "fight"), ("人群嘈杂", "crowd"), ("交谈", "talking"),
    ("低语", "whisper"), ("耳语", "whisper"), ("喊叫", "scream"), ("怒吼", "scream"),
    ("打喷嚏", "sneeze"), ("打嗝", "burp"), ("咀嚼", "chewing"), ("喝水", "drinking"),
    ("掌声雷动", "applause"), ("嘘声", "crowd"), ("叹气声", "sigh"), ("笑声一片", "laughter"),
    ("小孩", "kids"), ("儿童", "kids"), ("男人", "man"), ("女人", "woman"),
    # 家居 / 日常
    ("水龙头", "faucet"), ("流水声", "water"), ("冲水", "toilet"), ("马桶", "toilet"),
    ("洗衣机", "washing machine"), ("微波炉", "microwave"), ("吸尘器", "vacuum"),
    ("冰箱", "fridge"), ("空调", "air conditioner"), ("电风扇", "fan"), ("风扇", "fan"),
    ("钟表", "clock"), ("秒针", "clock"), ("挂钟", "clock"), ("闹铃声", "alarm"),
    ("倒水", "pouring water"), ("倒茶", "pouring water"), ("洗碗", "dishes"),
    ("切菜", "chopping"), ("炒菜", "cooking"), ("煮", "boiling"), ("油炸", "frying"),
    ("开门声", "door"), ("锁门", "lock"), ("钥匙", "keys"), ("拉门", "door"),
    ("推门", "door"), ("铁门", "door"), ("柜门", "drawer"), ("抽屉", "drawer"),
    ("拉链", "zipper"), ("剪刀", "scissors"), ("绳子", "rope"), ("布料", "cloth"),
    # 电子 / 办公
    ("键盘声", "keyboard"), ("机械键盘", "keyboard"), ("敲键盘", "typing"),
    ("鼠标点击", "mouse click"), ("按钮", "button"), ("提示", "notification"),
    ("叮", "ding"), ("叮咚", "ding dong"), ("解锁", "unlock"), ("密码", "password"),
    ("开机声", "startup"), ("关机声", "shutdown"), ("充电", "charging"),
    ("服务器", "server"), ("硬盘", "hard disk"), ("风扇转", "fan"),
    ("打印", "printer"), ("复印", "printer"), ("扫描仪", "scanner"),
    ("相机声", "camera"), ("录像", "camera"), ("拍照声", "camera"),
    # 交通 / 机械
    ("轮胎", "tire"), ("刹车", "brake"), ("加速", "engine"), ("排气管", "engine"),
    ("卡车", "truck"), ("公交", "bus"), ("大巴", "bus"), ("货车", "truck"),
    ("电动车", "scooter"), ("滑板", "skateboard"), ("轮子", "wheel"),
    ("电梯", "elevator"), ("扶梯", "escalator"), ("机器", "machine"),
    ("齿轮", "gear"), ("机械", "machine"), ("液压", "hydraulic"),
    ("火车鸣笛", "train"), ("轮船", "boat"), ("汽笛", "whistle"),
    # 打斗 / 特效
    ("巴掌", "slap"), ("扇耳光", "slap"), ("揍", "punch"), ("踢", "kick"),
    ("摔倒", "fall"), ("撞击", "impact"), ("碰撞", "impact"), ("砸", "impact"),
    ("碎裂", "crash"), ("裂开", "crack"), ("折断", "crack"), ("木头断裂", "wood"),
    ("剑", "sword"), ("刀", "knife"), ("弓箭", "arrow"), ("魔法", "magic"),
    ("施法", "magic"), ("闪光", "sparkle"), ("粒子", "sparkle"), ("能量", "energy"),
    ("科幻", "sci-fi"), ("太空", "space"), ("飞船", "spaceship"), ("机器人", "robot"),
    ("悬疑", "suspense"), ("紧张", "tense"), ("恐怖", "horror"), ("惊悚", "horror"),
    ("史诗", "epic"), ("悲壮", "epic"), ("温馨", "warm"), ("搞笑", "funny"),
    ("卡通", "cartoon"), ("弹跳", "boing"), ("弹簧", "boing"), ("泡泡破", "bubbles"),
    # 转场 / UI 音
    ("嗖一下", "whoosh"), ("嗖嗖", "whoosh"), ("滑过", "swish"), ("擦过", "swish"),
    ("冲击", "impact"), ("重击", "impact"), ("低频", "bass"), ("低音", "bass"),
    ("上升", "riser"), ("下降", "downlifter"), ("气氛", "ambience"), ("氛围", "ambience"),
    ("环境声", "ambience"), ("背景音", "background"), ("白噪音", "white noise"),
    ("噪音", "noise"), ("静电", "static"), ("干扰", "glitch"), ("故障", "glitch"),
    ("故障音", "glitch"), ("数据", "data"), ("加载", "loading"), ("进度", "loading"),
    ("得分", "score"), ("胜利", "victory"), ("失败", "fail"), ("游戏", "game"),
    ("按钮声", "button"), ("确认", "confirm"), ("取消", "cancel"), ("警告", "warning"),
    # 动物 / 其他
    ("公鸡", "rooster"), ("母鸡", "chicken"), ("鸭子", "duck"), ("牛叫", "cow"),
    ("羊叫", "sheep"), ("猪叫", "pig"), ("狮子", "lion"), ("老虎", "tiger"),
    ("大象", "elephant"), ("猴子", "monkey"), ("海鸥", "seagull"), ("蜜蜂", "bee"),
    ("蚊子", "mosquito"), ("苍蝇", "fly"), ("老鼠", "mouse"), ("蛇", "snake"),
    ("恐龙", "dinosaur"), ("鲸鱼", "whale"), ("海豚", "dolphin"),
    ("足球", "football"), ("篮球", "basketball"), ("球", "ball"), ("球场", "stadium"),
    ("水花", "splash"), ("跳水", "splash"), ("游泳", "swimming"), ("划水", "splash"),
]

# 长词先匹配：否则「火车」会被「火」抢先映射成 fire，「城市夜景」会被「城市」吃掉
_EN_ORDER = sorted(EN_WORDS, key=lambda x: -len(x[0]))


def search_terms(kw):
    """把用户输入拆成几个能拿去搜的英文词，按可信度排。

    英文原样排第一（用户自己写的英文最准）；中文按最长匹配收集所有命中的词，
    一次给几个候选——音效库的分类词很死，「掌声」映射成 applause 没搜到时，
    还能拿 clapping 之类的下一个候选再试一遍，比一次定生死强。
    """
    kw = (kw or "").strip()
    if not kw:
        return []
    if all(ord(c) < 128 for c in kw):
        out = [kw]
        first = kw.split()[0]
        if len(kw.split()) > 1 and first not in out:
            out.append(first)
        return out
    out = []
    # 「雨声 rain」这种混着写的，英文部分是用户自己挑的词，优先
    for w in re.findall(r"[A-Za-z][A-Za-z\-']+", kw):
        if w.lower() not in out:
            out.append(w.lower())
    for zh, en in _EN_ORDER:
        if zh in kw and en not in out:
            out.append(en)
    return out[:4]


def en_keyword(kw):
    """给站点直达用的单值版：英文原样；中文取最长的那个映射；认不出返回 None。"""
    terms = search_terms(kw)
    return terms[0] if terms else None


# ---------- 站点直达（不抓内容，只帮你一键在各站搜同一个词） ----------
# 都是公开可访问的搜索页，不涉及登录墙、付费内容或盗版站点。
SITE_LINKS = [
    {"id": "aigei", "name": "爱给网", "url": "https://www.aigei.com/s?q={q}&type=video",
     "tip": "国内视频 / 音效素材"},
    {"id": "pexels", "name": "Pexels", "url": "https://www.pexels.com/search/videos/{slug}/",
     "tip": "免费可商用视频"},
    {"id": "pixabay", "name": "Pixabay", "url": "https://pixabay.com/videos/search/{slug}/",
     "tip": "免费可商用视频"},
    {"id": "mixkit_site", "name": "Mixkit", "url": "https://mixkit.co/free-stock-video/{slug}/",
     "tip": "免费可商用视频"},
    {"id": "coverr", "name": "Coverr", "url": "https://coverr.co/search?q={q}",
     "tip": "免费短视频素材"},
    {"id": "vimeo", "name": "Vimeo", "url": "https://vimeo.com/search?q={q}",
     "tip": "创作者视频"},
    {"id": "freesound_site", "name": "Freesound", "url": "https://freesound.org/search/?q={q}",
     "tip": "音效 / 环境声", "kind": "audio"},
    {"id": "archive_site", "name": "Internet Archive", "url": "https://archive.org/search?query={q}",
     "tip": "公共领域影像"},
    {"id": "douban_site", "name": "豆瓣", "url": "https://movie.douban.com/subject_search?search_text={q}",
     "tip": "影视资料"},
    {"id": "bili_site", "name": "B站", "url": "https://search.bilibili.com/all?keyword={q}",
     "tip": "站内搜索"},
    {"id": "youtube_site", "name": "YouTube", "url": "https://www.youtube.com/results?search_query={q}",
     "tip": "站内搜索"},
    {"id": "bing_site", "name": "必应", "url": "https://cn.bing.com/search?q={q}",
     "tip": "全网找"},
    # —— 音效站直搜：这些站要么要登录才给下载、要么上了反爬，抓不了内容；
    #    按名字点开在浏览器里搜，比自己记住网址强。 ——
    {"id": "aigei_sfx", "name": "爱给网", "url": "https://www.aigei.com/s?q={q}&type=sound",
     "tip": "国内最大音效库，下载要登录", "kind": "audio"},
    {"id": "ear0_sfx", "name": "耳聆网", "url": "https://www.ear0.com/search?keyword={q}",
     "tip": "中文音效社区，下载要登录", "kind": "audio"},
    {"id": "chinaz_sfx", "name": "站长素材", "url": "https://sc.chinaz.com/yinxiao/?keyword={q}",
     "tip": "国内老牌音效站", "kind": "audio"},
    {"id": "pixabay_sfx", "name": "Pixabay", "url": "https://pixabay.com/zh/audio/search/{slug}/",
     "tip": "免费可商用音效", "kind": "audio"},
    {"id": "mixkit_sfx_site", "name": "Mixkit", "url": "https://mixkit.co/free-sound-effects/{slug}/",
     "tip": "免费可商用音效", "kind": "audio"},
]


def site_url(tpl, kw):
    """把 {q} / {slug} 填进模板。{q} 做完整 URL 编码，{slug} 做路径化。
    英文站的路径用中文映射后的词，别把中文硬塞进 URL 变成「all」。"""
    q = urllib.parse.quote((kw or "").strip(), safe="")
    raw = en_keyword(kw) or kw or ""
    slug = re.sub(r"[^a-z0-9\-]+", "-", raw.lower()).strip("-") or "all"
    return (tpl or "").replace("{q}", q).replace("{slug}", slug)


def site_links(cfg):
    """内置站点 + 用户自己加的站点。"""
    out = [dict(x) for x in SITE_LINKS]
    for s in (cfg or {}).get("custom_sites") or []:
        if not isinstance(s, dict):
            continue
        name = str(s.get("name") or "").strip()
        url = str(s.get("url") or "").strip()
        if not name or not url.startswith("https://") or "{q}" not in url:
            continue
        out.append({"id": "u_" + re.sub(r"\W+", "", name)[:16], "name": name,
                    "url": url, "tip": str(s.get("tip") or "自己加的站点"),
                    "custom": True})
    return out


def allowed_hosts(cfg):
    """给 /api/open_url 用：只允许打开名单里的站点，别变成任意网址跳板。"""
    hosts = set()
    for s in site_links(cfg):
        try:
            h = urllib.parse.urlsplit(s["url"]).hostname or ""
            if h:
                hosts.add(h.lower())
        except Exception:
            pass
    return hosts


# ---------- 来源清单 ----------
def custom_sources(cfg):
    """用户接进来的第三方 JSON 接口。

    配置形如：
      {"id":"my", "name":"我的资源站", "url":"https://x.com/api/s?q={q}&n={limit}",
       "root":"data.list", "fields":{"title":"title","url":"link","cover":"pic"}}
    """
    out = []
    for s in (cfg or {}).get("custom_sources") or []:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id") or "").strip()
        url = str(s.get("url") or "").strip()
        if not sid or not url.startswith("https://") or "{q}" not in url:
            continue
        out.append({
            "id": "c_" + re.sub(r"\W+", "", sid)[:20],
            "name": str(s.get("name") or sid)[:20],
            "kind": "video", "priority": 5, "timeout": 12,
            "group": "我加的接口", "note": "自己接的第三方接口",
            "custom": True, "url": url, "root": str(s.get("root") or ""),
            "fields": s.get("fields") if isinstance(s.get("fields"), dict) else {},
        })
    return out


def all_sources(cfg):
    """内置 + 自定义，按优先级排好。"""
    return sorted([dict(x) for x in BUILTIN] + custom_sources(cfg),
                  key=lambda x: x["priority"])


def sources_for_ui(cfg):
    return [{"id": s["id"], "name": s["name"], "group": s["group"],
             "note": s.get("note", ""), "kind": s["kind"],
             "slow": bool(s.get("slow")),
             "custom": bool(s.get("custom"))} for s in all_sources(cfg)]


# ---------- 通用小工具 ----------
def _fetch(url, timeout=10, referer=None):
    headers = {"User-Agent": UA, "Accept": "*/*",
               "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _txt(s):
    return html_mod.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def _attr(block, key):
    m = re.search(r'data-%s="([^"]*)"' % re.escape(key), block or "")
    return html_mod.unescape(m.group(1)) if m else ""


def _card(**kw):
    base = {"platform": "", "id": "", "kind": "video", "title": "", "author": "",
            "cover": "", "duration": 0, "desc": "", "url": "", "download": "",
            "page": "", "source": "", "source_name": ""}
    base.update(kw)
    return base


# ---------- 适配器 1：B站 / YouTube（复用原来的解析器） ----------
def _bili(kw, limit, cfg, proxy):
    import search
    items = search.search_bili(kw, limit)
    for x in items:
        x.update(source="bili", source_name="B站", download=x.get("url", ""),
                 page=x.get("url", ""))
    return items


def _youtube(kw, limit, cfg, proxy):
    import search
    items = search.search_youtube(kw, limit, proxy)
    for x in items:
        x.update(source="youtube", source_name="YouTube",
                 download=x.get("url", ""), page=x.get("url", ""))
    return items


# ---------- 适配器 2：Mixkit（免费可商用视频，直链可下） ----------
def _mixkit_slug(kw):
    s = re.sub(r"[^a-z0-9]+", "-", kw.lower()).strip("-")
    return s or "all"


def _mixkit_hq(vid, timeout=8):
    """去下载弹窗里把 1080p 的真实直链抠出来；拿不到就返回空，用预览直链兜底。"""
    u = "https://mixkit.co/free-stock-video/download/%s/?context=sidebar&type=1080p" % vid
    try:
        t = _fetch(u, timeout, referer="https://mixkit.co/")
        m = re.search(r'data-download--modal-url-value="([^"]+)"', t)
        return html_mod.unescape(m.group(1)) if m else ""
    except Exception:
        return ""


def _mixkit(kw, limit, cfg, proxy):
    en = en_keyword(kw)
    if not en:
        raise SkipSource("Mixkit 只认英文关键词，试试 city / rain / night（已跳过）")
    url = "https://mixkit.co/free-stock-video/%s/" % _mixkit_slug(en)
    t = _fetch(url, 12, referer="https://mixkit.co/")
    blocks = re.split(r'class="item-grid-card"', t)[1:limit + 8]
    cards = []
    for b in blocks:
        vid = _attr(b, "item-grid--video-player-item-id-value")
        if not vid:
            m = re.search(r'data-algolia-analytics-object-id="video-(\d+)"', b)
            vid = m.group(1) if m else ""
        if not vid:
            continue
        m = re.search(r'<video\s+src="([^"]+)"', b)
        preview = html_mod.unescape(m.group(1)) if m else ""
        if not preview:
            continue
        mt = re.search(r'item-grid-video-player__overlay-video-title">\s*(.*?)\s*</span>', b, re.S)
        md = re.search(r'item-grid-video-player__overlay-video-description">\s*(.*?)\s*</span>', b, re.S)
        mh = re.search(r'href="(/free-stock-video/[^"]+)"', b)
        mi = re.search(r'<img\s+src="([^"]+)"', b)
        page = "https://mixkit.co" + mh.group(1) if mh else url
        cards.append(_card(
            platform="mixkit", id="mk" + vid, kind="video",
            title=_txt(mt.group(1)) if mt else "Mixkit 素材 " + vid,
            author="Mixkit", cover=html_mod.unescape(mi.group(1)) if mi else "",
            desc=_txt(md.group(1))[:120] if md else "",
            url=page, page=page, download=preview,
            source="mixkit", source_name="Mixkit"))
        if len(cards) >= limit:
            break
    if not cards:
        raise SkipSource("Mixkit 没找到「%s」相关素材，换个英文词试试" % en)
    # 前 10 条并发换成 1080p 直链。失败就留着页面给的预览直链，
    # 至少还能下，不会出现「点了下载却什么都没有」。
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        hqs = list(ex.map(lambda c: _mixkit_hq(c["id"][2:]), cards[:10]))
    for c, hq in zip(cards[:10], hqs):
        if hq:
            c["download"] = hq
            c["hq"] = True
    return cards


# ---------- 适配器 3：Mixkit 音效（免费可商用，wav 直链） ----------
def _mixkit_sfx_hq(vid, timeout=8):
    """下载弹窗里给的是 wav 原文件，比页面上的 mp3 预览好。"""
    u = "https://mixkit.co/free-sound-effects/download/%s/?context=item+grid" % vid
    try:
        t = _fetch(u, timeout, referer="https://mixkit.co/")
        m = re.search(r'data-download--modal-url-value="([^"]+)"', t)
        return html_mod.unescape(m.group(1)) if m else ""
    except Exception:
        return ""


def _mixkit_sfx(kw, limit, cfg, proxy):
    terms = search_terms(kw)
    if not terms:
        raise SkipSource("这个词还没收进词库，换英文更快（rain / whoosh / applause）")
    cards, seen, errs = [], set(), []
    for en in terms:
        url = "https://mixkit.co/free-sound-effects/%s/" % _mixkit_slug(en)
        try:
            t = _fetch(url, 12, referer="https://mixkit.co/")
        except Exception as e:
            errs.append(e)
            continue
        # 只按卡片开头切，别把卡片里的 item-grid-card__meta / __title 也切碎
        for b in re.split(r'class="item-grid-card(?=[\s"])', t)[1:limit + 8]:
            m = re.search(r'data-audio-player-preview-url-value="([^"]+)"', b)
            preview = html_mod.unescape(m.group(1)) if m else ""
            mid = re.search(r'data-audio-player-item-id-value="(\d+)"', b)
            if not preview or not mid:
                continue
            vid = mid.group(1)
            if vid in seen:
                continue
            seen.add(vid)
            mt = re.search(r'class="item-grid-card__title">\s*(.*?)\s*</h2>', b, re.S)
            mh = re.search(r'href="(/free-sound-effects/[^"]*-%s/)"' % re.escape(vid), b)
            page = "https://mixkit.co" + mh.group(1) if mh else url
            cards.append(_card(
                platform="mixkit_sfx", id="ms" + vid, kind="audio",
                title=_txt(mt.group(1)) if mt else "音效 " + vid,
                author="Mixkit", cover="", duration=0,
                desc="可商用 · wav 原文件",
                url=page, page=page, download=preview,
                source="mixkit_sfx", source_name="Mixkit 音效"))
            if len(cards) >= limit:
                break
        # 第一个候选就够吃了就别再多打一个请求
        if len(cards) >= min(limit, 8):
            break
    if not cards:
        # 全都被拒（多半是 429 限流）：别报成「没找到」，那会让人以为词写错了
        if errs and len(errs) == len(terms):
            raise RuntimeError(str(errs[0]) or type(errs[0]).__name__)
        raise SkipSource("没找到「%s」的音效，换个说法试试" % kw)
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        hqs = list(ex.map(lambda c: _mixkit_sfx_hq(c["id"][2:]), cards[:10]))
    for c, hq in zip(cards[:10], hqs):
        if hq:
            c["download"] = hq
            c["hq"] = True
    return cards


# ---------- 适配器 4：Freesound（音效 / 环境声，比较慢） ----------
def _freesound(kw, limit, cfg, proxy):
    # 这站的内容基本是英文标题，中文词先映射成英文，命中率高得多。
    # 一个候选词搜空了就换下一个，别让一次映射失误把整站废掉。
    terms = search_terms(kw) or [kw]
    out, seen, errs = [], set(), []
    for q in terms[:2]:
        u = "https://freesound.org/search/?q=" + urllib.parse.quote(q)
        try:
            t = _fetch(u, 12, referer="https://freesound.org/")
        except Exception as e:
            errs.append(e)
            continue
        for m in re.finditer(r'<div\s+class="bw-player"(.*?)>', t, re.S):
            b = m.group(1)
            sid = _attr(b, "sound-id")
            mp3 = _attr(b, "mp3")
            if not sid or not mp3 or sid in seen:
                continue
            seen.add(sid)
            user = _attr(b, "username") or "Freesound"
            try:
                dur = int(float(_attr(b, "duration") or 0))
            except (TypeError, ValueError):
                dur = 0
            page = "https://freesound.org/people/%s/sounds/%s/" % (user, sid)
            out.append(_card(
                platform="freesound", id="fs" + sid, kind="audio",
                title=_attr(b, "title") or "音效 " + sid,
                author=user, cover=_attr(b, "waveform"),
                duration=dur, desc="需署名 · mp3 预览",
                url=page, page=page, download=mp3,
                source="freesound", source_name="Freesound"))
            if len(out) >= limit:
                return out
    if not out and errs and len(errs) == len(terms[:2]):
        raise RuntimeError(str(errs[0]) or type(errs[0]).__name__)
    return out


# ---------- 适配器 4：用户自己接的 JSON 接口 ----------
def _dig(obj, path):
    cur = obj
    for p in (path or "").split("."):
        if not p:
            continue
        if isinstance(cur, dict):
            cur = cur.get(p)
        elif isinstance(cur, list) and p.isdigit() and int(p) < len(cur):
            cur = cur[int(p)]
        else:
            return None
        if cur is None:
            return None
    return cur


def _custom_json(src, kw, limit, cfg, proxy):
    u = site_url(src["url"], kw).replace("{limit}", str(limit))
    t = _fetch(u, src.get("timeout") or 12)
    try:
        d = json.loads(t)
    except Exception:
        raise RuntimeError("返回的不是 JSON，检查接口地址")
    rows = _dig(d, src.get("root")) if src.get("root") else d
    if rows is None:
        rows = d.get("data") if isinstance(d, dict) else d
    if not isinstance(rows, list):
        rows = []
    f = src.get("fields") or {}
    out = []
    for i, row in enumerate(rows[:limit]):
        if not isinstance(row, dict):
            continue
        get = lambda key, default="": str(_dig(row, f.get(key) or key) or default)
        link = get("url") or get("download")
        if not link:
            continue
        if link.startswith("//"):
            link = "https:" + link
        out.append(_card(
            platform=src["id"], id="%s%d" % (src["id"], i), kind="video",
            title=get("title", "未命名"), author=get("author"), cover=get("cover"),
            desc=get("desc")[:120], url=link, page=link, download=get("download") or link,
            source=src["id"], source_name=src["name"]))
    return out


_ADAPTERS = {"bili": _bili, "youtube": _youtube,
             "mixkit": _mixkit, "mixkit_sfx": _mixkit_sfx,
             "freesound": _freesound}


def run_source(src, kw, limit, cfg=None, proxy=None):
    """跑一个来源。失败抛异常，调度器负责转成一句人话。"""
    sid = src["id"]
    if src.get("custom"):
        return _custom_json(src, kw, limit, cfg, proxy)
    fn = _ADAPTERS.get(sid)
    if not fn:
        raise RuntimeError("不认识的来源 " + sid)
    return fn(kw, limit, cfg, proxy)
