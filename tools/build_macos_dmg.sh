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

# imageio-ffmpeg 提供静态二进制，两个架构各有对应 wheel
echo "==> 取静态 ffmpeg"
FF="$(python3 -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')"
echo "    $FF"
file "$FF"
export VDL_FFMPEG="$FF" VDL_VERSION="$VER"

echo "==> PyInstaller 打包 .app"
rm -rf "$ROOT/build" "$ROOT/dist" "$ROOT/dmgstage"
pyinstaller --noconfirm --clean tools/macos.spec

APP="$ROOT/dist/音视频下载器.app"
test -d "$APP" || { echo "没生成 .app"; exit 1; }
# 无 Apple 开发者证书，做一次 ad-hoc 签名，至少避免「已损坏」；
# Gatekeeper 的首次打开提示仍需用户右键→打开，说明文档里写清楚。
codesign --force --deep --sign - "$APP" 2>/dev/null || echo "    ad-hoc 签名跳过"
chmod +x "$APP/Contents/MacOS/音视频下载器" || true

echo "==> 验证打包内容"
for f in "Contents/MacOS/音视频下载器" "Contents/Info.plist"; do
    test -e "$APP/$f" || { echo "缺 $f"; exit 1; }
done
file "$APP/Contents/MacOS/音视频下载器"
FF_IN_APP="$(find "$APP" -name ffmpeg -type f -print -quit)"
test -n "$FF_IN_APP" || { echo "ffmpeg 没打进 .app"; exit 1; }
chmod +x "$FF_IN_APP"
echo "    随包 ffmpeg: $FF_IN_APP"

echo "==> 压 DMG"
STAGE="$ROOT/dmgstage"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
cp "$ROOT/Mac使用说明.txt" "$STAGE/先看我.txt"
hdiutil create -volname "音视频下载器" -srcfolder "$STAGE" -ov -format UDZO "$OUT"
echo "==> 完成: $OUT ($(du -h "$OUT" | cut -f1))"
