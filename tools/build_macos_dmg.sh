#!/bin/bash
# 在 macOS 上把源码编成独立 .app，再压成 DMG。
# 只由 GitHub Actions 的 macOS runner 调用（本机 Windows 编不了 macOS app）。
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"
VER="$(tr -d '[:space:]' < version.txt)"
ARCH="$(uname -m)"
if [ "$ARCH" = "arm64" ]; then
    SUFFIX="arm64"
    LABEL="Apple 芯片"
else
    SUFFIX="x86_64"
    LABEL="Intel 芯片"
fi
OUT="$ROOT/VideoDL-${VER}-macOS-${SUFFIX}.dmg"

echo "==> 版本 $VER / $ARCH ($LABEL)"

python3 -m pip install --quiet --upgrade pip
python3 -m pip install --quiet -r requirements.txt pyinstaller imageio-ffmpeg

# 从现成的 512px 图生成 .icns（macOS 自带 sips / iconutil）
echo "==> 生成图标"
ICONS="/tmp/vdl.iconset"
rm -rf "$ICONS" && mkdir -p "$ICONS"
SRC="$ROOT/tools/assets/icon.png"
for pair in "16:icon_16x16.png" "32:icon_16x16@2x.png" \
            "32:icon_32x32.png" "64:icon_32x32@2x.png" \
            "128:icon_128x128.png" "256:icon_128x128@2x.png" \
            "256:icon_256x256.png" "512:icon_256x256@2x.png" \
            "512:icon_512x512.png"; do
    size="${pair%%:*}"; name="${pair#*:}"
    sips -z "$size" "$size" "$SRC" --out "$ICONS/$name" >/dev/null
done
iconutil -c icns "$ICONS" -o "$ROOT/tools/assets/icon.icns"

# imageio-ffmpeg 提供静态二进制，两个架构各有对应 wheel。
# 它自带的文件名带架构后缀（ffmpeg-macos-aarch64-v7.x），必须先重命名成
# 干净的 ffmpeg —— paths.ffmpeg_path() 找的就是 APP_DIR/bin/ffmpeg。
echo "==> 取静态 ffmpeg"
RAW_FF="$(python3 -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')"
echo "    $RAW_FF"
file "$RAW_FF"
FFSTAGE="$(mktemp -d)/ffmpeg"
cp "$RAW_FF" "$FFSTAGE"
chmod +x "$FFSTAGE"
export VDL_FFMPEG="$FFSTAGE" VDL_VERSION="$VER"

echo "==> PyInstaller 打包 .app"
rm -rf "$ROOT/build" "$ROOT/dist" "$ROOT/dmgstage"
pyinstaller --noconfirm --clean tools/macos.spec

APP="$ROOT/dist/音视频下载器.app"
test -d "$APP" || { echo "没生成 .app"; exit 1; }
echo "==> 验证打包内容"
for f in "Contents/MacOS/音视频下载器" "Contents/Info.plist"; do
    test -e "$APP/$f" || { echo "缺 $f"; exit 1; }
done
file "$APP/Contents/MacOS/音视频下载器"
FF_IN_APP="$(find "$APP" -name ffmpeg -type f -print -quit)"
test -n "$FF_IN_APP" || { echo "ffmpeg 没打进 .app"; exit 1; }
chmod +x "$FF_IN_APP"
echo "    随包 ffmpeg: $FF_IN_APP"
"$FF_IN_APP" -version 2>&1 | head -1
WEB_IN_APP="$(find "$APP" -name index.html -path '*/web/*' -print -quit)"
test -n "$WEB_IN_APP" || { echo "web 界面没打进 .app"; exit 1; }
echo "    随包界面: $WEB_IN_APP"

# chmod 之后再签名，否则改了文件会破坏签名
# 无 Apple 开发者证书，做一次 ad-hoc 签名，至少避免「已损坏」；
# Gatekeeper 的首次打开提示仍需用户右键→打开，说明文档里写清楚。
codesign --force --deep --sign - "$APP" 2>/dev/null || echo "    ad-hoc 签名跳过"

# 在真正的 macOS 上把 .app 跑起来问一次接口：能启动 = 依赖齐全、
# web 资源找得到、数据目录可写。没有 Mac 开发机，这一步就是真机验证。
echo "==> 冒烟测试：实际启动一次"
BIN="$APP/Contents/MacOS/音视频下载器"
"$BIN" none >/tmp/vdl_boot.log 2>&1 &
BOOT_PID=$!
OK=0
for _ in $(seq 1 40); do
    if curl -fsS http://127.0.0.1:8765/api/state >/tmp/vdl_state.json 2>/dev/null; then OK=1; break; fi
    sleep 1
done
if [ "$OK" != "1" ]; then
    echo "服务没起来，启动日志："; cat /tmp/vdl_boot.log; kill "$BOOT_PID" 2>/dev/null || true
    exit 1
fi
python3 - "$VER" <<'PYEOF'
import json, os, sys
d = json.load(open('/tmp/vdl_state.json'))
ver = sys.argv[1]
print('    接口版本:', d.get('version'), '/ 期望', ver)
assert str(d.get('version')) == ver, '版本号对不上，跑的不是这份代码'
ff = d.get('ffmpeg') or ''
print('    运行时找到 ffmpeg:', ff)
assert os.path.isfile(ff) and os.access(ff, os.X_OK), '运行时找不到可执行的 ffmpeg'
PYEOF
curl -fsS http://127.0.0.1:8765/ >/tmp/vdl_home.html || { echo "首页没出来"; exit 1; }
grep -q '音视频下载器' /tmp/vdl_home.html || { echo "首页内容不对"; exit 1; }
curl -fsS http://127.0.0.1:8765/app.js >/dev/null || { echo "app.js 没出来"; exit 1; }
DATA="$HOME/Library/Application Support/VideoDL"
test -f "$DATA/logs/boot.log" || { echo "数据目录没重定向到用户目录: $DATA"; ls -la "$DATA" || true; exit 1; }
echo "    数据目录: $DATA/logs/boot.log ✓"
kill "$BOOT_PID" 2>/dev/null || true
wait "$BOOT_PID" 2>/dev/null || true

echo "==> 压 DMG"
STAGE="$ROOT/dmgstage"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
cp "$ROOT/Mac使用说明.txt" "$STAGE/先看我.txt"
hdiutil create -volname "音视频下载器" -srcfolder "$STAGE" -ov -format UDZO "$OUT"
echo "==> 完成: $OUT ($(du -h "$OUT" | cut -f1))"
