#!/usr/bin/env python3
"""视频音量检测脚本：提取视频的平均音量与最大音量（相对 0 dBFS 满量程基准）

所有数值均为 dBFS（分贝满量程），0 dBFS 为数字音频的最大值，所有视频共用同一基准，
便于横向比较：数值越接近 0 音量越大，越负音量越小。

用法：
    python detect_volume.py <视频文件或目录>
    python detect_volume.py <目录> --threshold -30   # 标记平均音量低于 -30 dB 的视频
    python detect_volume.py <目录> --json out.json    # 同时输出 JSON 结果
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv", ".webm", ".m4v", ".mp3", ".wav", ".aac", ".m4a", ".flac"}

# volumedetect 输出正则（位于 stderr）
MEAN_RE = re.compile(r"mean_volume:\s*(-?\d+\.?\d*)\s*dB")
MAX_RE = re.compile(r"max_volume:\s*(-?\d+\.?\d*)\s*dB")


def find_ffmpeg() -> str:
    """查找可用的 ffmpeg（优先使用 Homebrew 版本）"""
    brew_ffmpeg = "/opt/homebrew/bin/ffmpeg"
    if os.path.isfile(brew_ffmpeg):
        return brew_ffmpeg
    found = shutil.which("ffmpeg")
    if found:
        return found
    raise RuntimeError("未找到 ffmpeg，请先安装: brew install ffmpeg")


def detect_volume(video_path: str, ffmpeg_path: str) -> dict:
    """检测单个视频的音量，返回 mean_volume / max_volume（dBFS）

    使用 ffmpeg 的 volumedetect 滤镜，所有音量均相对于 0 dBFS 满量程基准。
    """
    import time
    t0 = time.time()
    cmd = [
        ffmpeg_path, "-hide_banner", "-i", video_path,
        "-af", "volumedetect",
        "-vn", "-sn", "-dn",
        "-f", "null", "-"
    ]
    # volumedetect 的结果输出在 stderr
    result = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = round(time.time() - t0, 2)
    stderr = result.stderr

    # ffmpeg 对无音频流会返回非零，但仍可能有信息
    mean_match = MEAN_RE.search(stderr)
    max_match = MAX_RE.search(stderr)

    if not mean_match and not max_match:
        return {
            "video": os.path.basename(video_path),
            "path": video_path,
            "mean_volume_db": None,
            "max_volume_db": None,
            "processing_time_sec": elapsed,
            "error": "未检测到音频流或无法解析音量（可能无音轨）"
        }

    return {
        "video": os.path.basename(video_path),
        "path": video_path,
        "mean_volume_db": float(mean_match.group(1)) if mean_match else None,
        "max_volume_db": float(max_match.group(1)) if max_match else None,
        "processing_time_sec": elapsed,
    }


def format_db(value) -> str:
    if value is None:
        return "  N/A"
    return f"{value:7.2f}"


# ===================== 抖音视频支持 =====================

import urllib.request
import urllib.error

# 抖音详情 API
DOUYIN_API_URL = "https://cyanlis.cn/api/plugins/douyin/detail_v4"


def _get_douyin_api_key() -> str:
    """从多个来源读取 API Key，优先级：环境变量 > Streamlit secrets > .streamlit/secrets.toml

    部署到 Streamlit Community Cloud 时，在 App Settings → Secrets 中配置：
    DOUYIN_API_KEY = "your-key-here"
    """
    # 1. 系统环境变量（本地开发、Railway/Render 等部署平台）
    key = os.environ.get("DOUYIN_API_KEY")
    if key:
        return key

    # 2. Streamlit secrets（Community Cloud 部署用）
    try:
        import streamlit as st
        key = st.secrets.get("DOUYIN_API_KEY")
        if key:
            return key
    except Exception:
        pass

    return ""


# 启动时读取一次，后续复用
DOUYIN_API_KEY = _get_douyin_api_key()


def _make_no_proxy_opener(ssl_context=None):
    """创建 urllib opener：优先使用系统代理（如 Clash），代理不可用时自动降级为直连"""
    import socket

    handlers = []

    # 检测系统代理是否可用（如 http://127.0.0.1:7890）
    proxy_url = (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
    )
    proxy_ok = False
    if proxy_url:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(proxy_url)
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or 7890
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex((host, port))
            sock.close()
            proxy_ok = (result == 0)
        except Exception:
            proxy_ok = False

    if proxy_ok:
        # 代理可用，使用系统代理
        proxy_handler = urllib.request.ProxyHandler({
            "http": proxy_url,
            "https": proxy_url,
        })
        handlers.append(proxy_handler)
    else:
        # 代理不可用，直连
        handlers.append(urllib.request.ProxyHandler({}))

    if ssl_context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=ssl_context))

    return urllib.request.build_opener(*handlers)


def fetch_douyin_video_url(share_url: str) -> dict:
    """调用 cyanlis API 获取抖音视频详情，返回包含 video_url 等信息的字典

    返回字段：
        - aweme_id, desc, nickname, video_url, duration_ms, cover, ...
        - 失败时返回 {"error": "..."}
    """
    if not DOUYIN_API_KEY:
        return {"error": "未配置 DOUYIN_API_KEY，请在环境变量或 Streamlit Secrets 中设置"}

    payload = json.dumps({"url": share_url}).encode("utf-8")
    url = f"{DOUYIN_API_URL}?apiKey={DOUYIN_API_KEY}"
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = _make_no_proxy_opener()
    try:
        with opener.open(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.URLError as e:
        return {"error": f"网络请求失败: {e}"}
    except Exception as e:
        return {"error": f"请求异常: {e}"}

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return {"error": f"响应非 JSON: {body[:200]}"}

    if not data.get("success") or data.get("code") != 0:
        return {"error": f"API 返回失败: {data.get('msg', body[:200])}"}

    detail = data.get("data", {})
    if not detail.get("video_url"):
        return {"error": "未获取到 video_url（可能视频已删除或受限）"}

    # 解析 msg 中的资源点消耗信息，格式如：
    # "返回成功 [本次消耗:1 剩余:1348 今日:34 累计:50249]"
    raw_msg = data.get("msg", "")
    cost_match = re.search(r"本次消耗[:：]\s*(\d+)", raw_msg)
    remain_match = re.search(r"剩余[:：]\s*(\d+)", raw_msg)

    return {
        "aweme_id": detail.get("aweme_id"),
        "desc": detail.get("desc") or detail.get("caption") or "",
        "nickname": detail.get("nickname") or detail.get("owner_nickname") or "",
        "video_url": detail["video_url"],
        "duration_ms": detail.get("duration"),
        "cover": detail.get("cover"),
        "digg_count": detail.get("digg_count"),
        "comment_count": detail.get("comment_count"),
        "share_url": detail.get("share_url"),
        "api_cost": int(cost_match.group(1)) if cost_match else None,
        "api_remaining": int(remain_match.group(1)) if remain_match else None,
    }


def detect_volume_online(url: str, ffmpeg_path: str, label: str = None) -> dict:
    """对在线视频 URL 执行：ffmpeg 直接流式读取 → volumedetect

    使用 ffmpeg 直接从 URL 读取音频流，不下载到本地。
    label: 显示用的名称（如抖音视频标题），不传则用 URL
    """
    import ssl
    import time

    t0 = time.time()
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    # 构建 ffmpeg 命令，直接从 URL 流式读取
    cmd = [
        ffmpeg_path,
        "-y",
        "-rw_timeout", "30000000",  # 30 秒读写超时
        "-i", url,
        "-af", "volumedetect",
        "-vn", "-sn", "-dn",
        "-f", "null", "-",
    ]

    try:
        # 清除代理环境变量，避免 ffmpeg 走不可用的代理
        clean_env = {**os.environ}
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            clean_env.pop(k, None)
        clean_env["SSL_CERT_FILE"] = ""
        clean_env["REQUESTS_CA_BUNDLE"] = ""
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            env=clean_env,
        )
    except subprocess.TimeoutExpired:
        return {
            "video": label or url,
            "path": url,
            "mean_volume_db": None,
            "max_volume_db": None,
            "processing_time_sec": round(time.time() - t0, 2),
            "error": "ffmpeg 处理超时"
        }
    except Exception as e:
        return {
            "video": label or url,
            "path": url,
            "mean_volume_db": None,
            "max_volume_db": None,
            "processing_time_sec": round(time.time() - t0, 2),
            "error": f"ffmpeg 执行失败: {e}"
        }

    elapsed = round(time.time() - t0, 2)
    stderr = proc.stderr
    mean_match = MEAN_RE.search(stderr)
    max_match = MAX_RE.search(stderr)
    mean_vol = float(mean_match.group(1)) if mean_match else None
    max_vol = float(max_match.group(1)) if max_match else None

    if mean_vol is None and max_vol is None:
        if "No audio" in stderr or "no audio" in stderr:
            return {
                "video": label or url,
                "path": url,
                "mean_volume_db": None,
                "max_volume_db": None,
                "processing_time_sec": elapsed,
                "error": "无音轨"
            }
        return {
            "video": label or url,
            "path": url,
            "mean_volume_db": None,
            "max_volume_db": None,
            "processing_time_sec": elapsed,
            "error": "未检测到音频流或无法解析音量"
        }

    return {
        "video": label or url,
        "path": url,
        "mean_volume_db": mean_vol,
        "max_volume_db": max_vol,
        "processing_time_sec": elapsed,
    }


def detect_douyin_volume(share_url: str, ffmpeg_path: str) -> dict:
    """完整流程：抖音链接 → API 取 video_url → ffmpeg 在线分析

    返回字典包含：
        - source_url: 原始分享链接
        - aweme_id, desc, nickname, duration_ms: 视频元信息
        - video: 用于展示的名称
        - mean_volume_db, max_volume_db: 音量结果
        - processing_time_sec: 总处理时间（API + ffmpeg）
        - error: 失败时的错误信息（成功时不存在）
    """
    import time
    t0 = time.time()

    info = fetch_douyin_video_url(share_url)
    if "error" in info:
        return {
            "source_url": share_url,
            "video": share_url,
            "mean_volume_db": None,
            "max_volume_db": None,
            "processing_time_sec": round(time.time() - t0, 2),
            **info,
        }

    # 用「昵称 - 描述前 30 字」作为显示名
    desc_short = (info.get("desc") or "")[:30].replace("\n", " ")
    nickname = info.get("nickname") or ""
    label = f"{nickname} - {desc_short}" if nickname or desc_short else share_url

    result = detect_volume_online(info["video_url"], ffmpeg_path, label=label)
    # 用总时间覆盖 detect_volume_online 内部记录的 ffmpeg 时间
    result["processing_time_sec"] = round(time.time() - t0, 2)
    result["source_url"] = share_url
    result["aweme_id"] = info.get("aweme_id")
    result["nickname"] = info.get("nickname")
    result["desc"] = info.get("desc")
    result["duration_ms"] = info.get("duration_ms")
    result["api_cost"] = info.get("api_cost")
    result["api_remaining"] = info.get("api_remaining")
    return result


def collect_videos(input_path: str) -> list:
    """收集输入路径下的所有视频文件"""
    p = Path(input_path)
    if p.is_file():
        return [str(p)]
    if p.is_dir():
        videos = []
        for f in sorted(p.rglob("*")):
            if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS:
                videos.append(str(f))
        return videos
    return []


def main():
    parser = argparse.ArgumentParser(
        description="检测视频音量（平均/最大，相对 0 dBFS 基准，便于横向比较）"
    )
    parser.add_argument("input", help="视频文件或目录路径")
    parser.add_argument(
        "--threshold", type=float, default=None,
        help="平均音量阈值（dBFS，负值，如 -30）。低于该值的视频会被标记"
    )
    parser.add_argument(
        "--json", dest="json_path", default=None,
        help="将结果输出为 JSON 文件"
    )
    args = parser.parse_args()

    if not Path(args.input).exists():
        print(f"错误: 路径不存在 - {args.input}")
        sys.exit(1)

    ffmpeg_path = find_ffmpeg()
    print(f"使用 ffmpeg: {ffmpeg_path}")
    print(f"基准: 0 dBFS（数字满量程），数值越接近 0 音量越大\n")

    videos = collect_videos(args.input)
    if not videos:
        print("未找到任何视频文件")
        sys.exit(1)

    results = []
    for i, v in enumerate(videos, 1):
        print(f"[{i}/{len(videos)}] 检测中: {os.path.basename(v)}")
        info = detect_volume(v, ffmpeg_path)
        results.append(info)

    # 按平均音量从低到高排序（音量小的排在前面，便于优先发现过低的视频）
    sortable = [r for r in results if r["mean_volume_db"] is not None]
    failed = [r for r in results if r["mean_volume_db"] is None]
    sortable.sort(key=lambda r: r["mean_volume_db"])
    sorted_results = sortable + failed

    # 打印汇总表
    print("\n" + "=" * 70)
    print(f"{'视频':<40} {'平均(dBFS)':>12} {'最大(dBFS)':>12}")
    print("-" * 70)
    for r in sorted_results:
        name = r["video"]
        if len(name) > 38:
            name = name[:35] + "..."
        mean_str = format_db(r["mean_volume_db"])
        max_str = format_db(r["max_volume_db"])
        flag = ""
        if (args.threshold is not None and r["mean_volume_db"] is not None
                and r["mean_volume_db"] < args.threshold):
            flag = "  <-- 音量过低"
        elif r["mean_volume_db"] is None:
            flag = f"  <-- {r.get('error', '异常')}"
        print(f"{name:<40} {mean_str:>12} {max_str:>12}{flag}")
    print("=" * 70)

    if args.threshold is not None:
        low = [r for r in sortable if r["mean_volume_db"] < args.threshold]
        print(f"\n平均音量低于 {args.threshold} dBFS 的视频: {len(low)} / {len(sortable)}")
        if low:
            print("建议重新录制或提升音量：")
            for r in low:
                print(f"  - {r['path']}  (mean={r['mean_volume_db']:.2f} dBFS)")

    if args.json_path:
        payload = {
            "benchmark": "0 dBFS (digital full scale)",
            "scan_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "threshold": args.threshold,
            "results": sorted_results,
        }
        with open(args.json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"\nJSON 结果已保存: {args.json_path}")


if __name__ == "__main__":
    main()
