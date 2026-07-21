#!/usr/bin/env python3
"""视频转文稿脚本：提取视频中的语音文本及时间轴，输出JSON格式文稿"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import whisper


def find_ffmpeg() -> str:
    """查找可用的 ffmpeg（优先使用 Homebrew 版本）"""
    # 优先查找 Homebrew 版本
    brew_ffmpeg = "/opt/homebrew/bin/ffmpeg"
    if os.path.isfile(brew_ffmpeg):
        return brew_ffmpeg
    # 回退到系统 PATH 中的 ffmpeg
    found = shutil.which("ffmpeg")
    if found:
        return found
    raise RuntimeError("未找到 ffmpeg，请先安装: brew install ffmpeg")


def extract_audio(video_path: str, audio_path: str, ffmpeg_path: str) -> None:
    """使用 ffmpeg 从视频中提取音频为 16kHz 单声道 WAV"""
    cmd = [
        ffmpeg_path, "-i", video_path,
        "-vn", "-acodec", "pcm_s16le",
        "-ar", "16000", "-ac", "1",
        "-y", audio_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 提取音频失败: {result.stderr}")


def format_timestamp(seconds: float) -> str:
    """将秒数格式化为 HH:MM:SS.mmm"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def transcribe_video(video_path: str, model_name: str, ffmpeg_path: str) -> dict:
    """转写单个视频，返回包含文本和时间轴的字典"""
    print(f"正在处理: {os.path.basename(video_path)}")

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        audio_path = tmp.name

    try:
        print("  提取音频...")
        extract_audio(video_path, audio_path, ffmpeg_path)

        print(f"  加载 Whisper 模型 ({model_name})...")
        model = whisper.load_model(model_name)

        print("  正在转写...")
        result = whisper.transcribe(model, audio_path, language="zh", verbose=False)

        segments = []
        for seg in result["segments"]:
            segments.append({
                "start": format_timestamp(seg["start"]),
                "end": format_timestamp(seg["end"]),
                "start_seconds": round(seg["start"], 3),
                "end_seconds": round(seg["end"], 3),
                "text": seg["text"].strip()
            })

        return {
            "video_filename": os.path.basename(video_path),
            "output_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "full_text": result["text"].strip(),
            "segments": segments
        }
    finally:
        if os.path.exists(audio_path):
            os.unlink(audio_path)


VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv", ".webm", ".m4v"}


def process_path(input_path: str, model_name: str = "base", output_dir: str = None) -> None:
    """处理单个视频文件或文件夹中的所有视频"""
    p = Path(input_path)

    if not p.exists():
        print(f"错误: 路径不存在 - {input_path}")
        sys.exit(1)

    ffmpeg_path = find_ffmpeg()
    print(f"使用 ffmpeg: {ffmpeg_path}")

    if p.is_file():
        if p.suffix.lower() not in VIDEO_EXTENSIONS:
            print(f"错误: 不支持的视频格式 - {p.suffix}")
            sys.exit(1)
        videos = [p]
    else:
        videos = sorted([
            f for f in p.iterdir()
            if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
        ])
        if not videos:
            print("未找到视频文件")
            return

    print(f"找到 {len(videos)} 个视频文件\n")

    if output_dir is None:
        output_dir = p.parent / "output" if p.is_file() else p / "output"
    os.makedirs(output_dir, exist_ok=True)

    for i, video in enumerate(videos, 1):
        print(f"[{i}/{len(videos)}] {video.name}")
        try:
            result = transcribe_video(str(video), model_name, ffmpeg_path)
            output_file = Path(output_dir) / f"{video.stem}_文稿.json"
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print(f"  已保存: {output_file}\n")
        except Exception as e:
            print(f"  处理失败: {e}\n")

    print("全部处理完成！")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="视频转文稿：提取视频语音文本及时间轴")
    parser.add_argument("input", help="视频文件或文件夹路径")
    parser.add_argument("--model", default="base",
                        choices=["tiny", "base", "small", "medium", "large"],
                        help="Whisper 模型大小 (默认: base，越大越准但越慢)")
    parser.add_argument("--output", default=None,
                        help="输出目录 (默认: 视频文件夹下的 output 子目录)")
    args = parser.parse_args()

    process_path(args.input, args.model, args.output)
