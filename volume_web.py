#!/usr/bin/env python3
"""视频音量检测 Web 工具（Streamlit）

启动：
    streamlit run volume_web.py
访问：
    http://localhost:8501  （同局域网同事访问 http://你的IP:8501）
"""

import json
import os
import tempfile
import glob
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from detect_volume import detect_volume, detect_douyin_volume, find_ffmpeg

# 历史记录配置
HISTORY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".volume_history")
HISTORY_MAX_RECORDS = 20
HISTORY_MAX_DAYS = 7

# 支持的文件类型
VIDEO_EXTENSIONS = {
    ".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv", ".webm", ".m4v",
    ".mp3", ".wav", ".aac", ".m4a", ".flac"
}

# session_state 初始化
if "results" not in st.session_state:
    st.session_state.results = None
if "threshold" not in st.session_state:
    st.session_state.threshold = -25.0
if "uploaded_file_ids" not in st.session_state:
    st.session_state.uploaded_file_ids = []
if "douyin_links" not in st.session_state:
    st.session_state.douyin_links = ""
if "scroll_to_results" not in st.session_state:
    st.session_state.scroll_to_results = False


def ensure_history_dir():
    os.makedirs(HISTORY_DIR, exist_ok=True)


def cleanup_history():
    """清理超出数量或时间限制的历史记录"""
    ensure_history_dir()
    files = sorted(glob.glob(os.path.join(HISTORY_DIR, "*.json")))
    now = datetime.now()
    kept = []
    for f in files:
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(f))
            if now - mtime > timedelta(days=HISTORY_MAX_DAYS):
                os.remove(f)
                continue
        except OSError:
            continue
        kept.append(f)
    while len(kept) > HISTORY_MAX_RECORDS:
        oldest = kept.pop(0)
        try:
            os.remove(oldest)
        except OSError:
            pass


def save_to_history(results: list, threshold: float, source_type: str = "mixed"):
    """保存一次检测结果到历史记录"""
    cleanup_history()
    ensure_history_dir()
    ts = datetime.now()
    record = {
        "id": ts.strftime("%Y%m%d_%H%M%S"),
        "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
        "threshold": threshold,
        "source_type": source_type,
        "count": len(results),
        "results": results,
    }
    fname = f"record_{record['id']}.json"
    fpath = os.path.join(HISTORY_DIR, fname)
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)


def load_history_list() -> list:
    """加载所有历史记录元信息（不包含完整 results），按时间倒序"""
    cleanup_history()
    ensure_history_dir()
    files = sorted(glob.glob(os.path.join(HISTORY_DIR, "record_*.json")), reverse=True)
    records = []
    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            records.append({
                "id": data.get("id"),
                "timestamp": data.get("timestamp"),
                "threshold": data.get("threshold"),
                "source_type": data.get("source_type", "unknown"),
                "count": data.get("count", 0),
                "file_path": f,
            })
        except (json.JSONDecodeError, OSError):
            continue
    return records


def load_history_record(file_path: str) -> dict:
    """加载单条完整历史记录"""
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def delete_history_record(file_path: str):
    try:
        os.remove(file_path)
    except OSError:
        pass


def clear_all_history():
    ensure_history_dir()
    for f in glob.glob(os.path.join(HISTORY_DIR, "*.json")):
        try:
            os.remove(f)
        except OSError:
            pass


def file_signature(files):
    """根据文件名+大小生成签名，用于检测文件是否变化"""
    return [(f.name, f.size) for f in files]


def main():
    st.set_page_config(
        page_title="视频音量检测工具",
        page_icon="📊",
        layout="wide"
    )

    st.title("📊 视频音量检测工具")
    st.caption("基准：0 dBFS（数字满量程）· 数值越接近 0 音量越大 · 越负音量越小")

    # ---------- 使用说明与限制（固定展示） ----------
    with st.expander("📌 使用说明与限制", expanded=False):
        st.markdown("**两种输入方式**：上传本地文件 / 粘贴抖音链接（无需下载）")
        col_limit1, col_limit2, col_limit3 = st.columns(3)
        with col_limit1:
            st.info("**单次数量**\n本地文件 ≤ 20-30 个\n抖音链接 ≤ 10-20 条")
        with col_limit2:
            st.info("**本地文件大小**\n建议 ≤ 100MB\n默认上限 200MB")
        with col_limit3:
            st.info("**支持格式**\n本地：mp4/mov/mkv/...\n抖音：直接粘贴链接")
        st.markdown("""
**指标说明**：
- **平均音量（mean）**：RMS 平均，反映整体响度，用于判断"音量过低"
- **最大音量（max）**：峰值，若为 0.00 表示已削波失真

**抖音链接示例**：
- `https://www.douyin.com/video/7646284426151141541`
- `https://v.douyin.com/xxxxxxx/`（短链也支持）
""")

    # ---------- 阈值参考（固定展示） ----------
    with st.expander("📐 阈值参考", expanded=True):
        col_t1, col_t2, col_t3 = st.columns(3)
        col_t1.success("**✅ 正常**\n> -25 dB\n音量合适")
        col_t2.info("**⚠️ 偏轻柔**\n-25 ~ -16 dB\n可接受，无需重录")
        col_t3.error("**❌ 过低**\n< -25 dB\n建议重新录制")

    # ---------- 输入区（Tab 切换） ----------
    st.subheader("📥 选择输入方式")
    tab_upload, tab_douyin = st.tabs(["📁 上传本地文件", "🔗 抖音链接"])

    # --- Tab 1: 本地文件上传 ---
    with tab_upload:
        uploaded_files = st.file_uploader(
            "拖拽视频文件到这里，或点击选择（支持多文件）",
            type=list(VIDEO_EXTENSIONS),
            accept_multiple_files=True,
            key="file_uploader"
        )

        # 文件变化时清空旧结果
        current_sig = file_signature(uploaded_files) if uploaded_files else []
        if current_sig != st.session_state.uploaded_file_ids:
            st.session_state.results = None
            st.session_state.uploaded_file_ids = current_sig

        if uploaded_files:
            st.success(f"已选择 {len(uploaded_files)} 个文件")

    # --- Tab 2: 抖音链接 ---
    with tab_douyin:
        douyin_links_text = st.text_area(
            "粘贴抖音视频链接（每行一个）",
            value=st.session_state.douyin_links,
            height=150,
            placeholder="https://www.douyin.com/video/7646284426151141541\nhttps://v.douyin.com/xxxxxxx/",
            key="douyin_links_input"
        )
        st.session_state.douyin_links = douyin_links_text

        # 链接变化时清空旧结果
        links = parse_douyin_links(douyin_links_text)
        if links:
            st.success(f"识别到 {len(links)} 条抖音链接")

    # ---------- 检测设置 ----------
    st.subheader("⚙️ 检测设置")
    threshold = st.number_input(
        "音量阈值（dBFS）",
        value=-25.0,
        step=1.0,
        help="低于此值的视频会被标记为「过低」，建议重新录制"
    )
    st.session_state.threshold = threshold

    # 判断当前 tab 是否有可用输入（简单依据：哪个有内容就检测哪个）
    has_files = uploaded_files if uploaded_files else []
    has_links = parse_douyin_links(douyin_links_text)

    # 根据输入类型显示不同按钮
    if has_files and not has_links:
        if st.button("🚀 开始检测（本地文件）", type="primary", use_container_width=True):
            run_detection_files(has_files, threshold)
    elif has_links and not has_files:
        if st.button("🚀 开始检测（抖音链接）", type="primary", use_container_width=True):
            run_detection_douyin(has_links, threshold)
    elif has_files and has_links:
        st.warning("⚠️ 同时检测本地文件和抖音链接，将合并结果")
        if st.button("🚀 开始检测（全部）", type="primary", use_container_width=True):
            combined = []
            run_detection_files(has_files, threshold, results_sink=combined)
            run_detection_douyin(has_links, threshold, results_sink=combined)
            st.session_state.results = combined
            save_to_history(combined, threshold, source_type="mixed")
    else:
        st.info("👆 请先上传文件或粘贴抖音链接")

    # ---------- 结果区（每次 rerun 都从 session_state 渲染，不会消失） ----------
    if st.session_state.results is not None:
        # 持久展示 API 资源点消耗信息（不会被 rerun 清除）
        usage_msg = st.session_state.get("api_usage_msg")
        if usage_msg:
            st.info(f"💡 {usage_msg}")
        render_results(st.session_state.results, st.session_state.threshold)

    # ---------- 历史记录 ----------
    render_history_panel()


def render_history_panel():
    """渲染历史记录面板"""
    st.divider()
    with st.expander("📜 历史记录", expanded=False):
        st.caption(f"最多保留最近 {HISTORY_MAX_RECORDS} 条记录，自动清理 {HISTORY_MAX_DAYS} 天前的历史")
        records = load_history_list()

        if not records:
            st.info("暂无历史记录")
            return

        # 清空按钮
        col_h1, col_h2 = st.columns([6, 1])
        with col_h2:
            if st.button("🗑️ 清空全部", key="clear_history_btn"):
                clear_all_history()
                st.toast("已清空全部历史记录", icon="🗑️")
                st.rerun()

        # 来源类型图标映射
        source_icon = {
            "local": "📁",
            "douyin": "🔗",
            "mixed": "🔀",
            "unknown": "❓",
        }

        for idx, rec in enumerate(records):
            icon = source_icon.get(rec["source_type"], "❓")
            ts_display = rec["timestamp"].replace("-", "/")
            label = f"{icon} {ts_display} · 共 {rec['count']} 项 · 阈值 {rec['threshold']} dBFS"

            # 预先生成下载数据，避免按钮与下载按钮在同一交互周期互相干扰
            try:
                full = load_history_record(rec["file_path"])
                rec_results = full.get("results", [])
                rec_threshold = full.get("threshold", -25.0)
                rec_id = full.get("id", rec["id"])
                df_rec = build_dataframe(rec_results, rec_threshold)
                csv_df = df_rec.map(sanitize_for_csv)
                csv_bytes = csv_df.to_csv(index=False).encode("utf-8-sig")
                json_bytes = json.dumps(
                    {
                        "benchmark": "0 dBFS (digital full scale)",
                        "scan_time": full.get("timestamp", ""),
                        "threshold_dbfs": rec_threshold,
                        "results": rec_results,
                    },
                    ensure_ascii=False,
                    indent=2,
                ).encode("utf-8")
                has_data = True
            except Exception as e:
                csv_bytes = b""
                json_bytes = b""
                has_data = False
                rec_results = None
                rec_id = rec["id"]
                rec_threshold = -25.0

            with st.container():
                col_label, col_view, col_csv, col_json, col_del = st.columns([5, 1, 1, 1, 1])
                with col_label:
                    st.markdown(f"**{label}**")
                    if not has_data:
                        st.caption(f"⚠️ 无法读取：{e}")
                with col_view:
                    if st.button("👁️", key=f"view_{rec['id']}", help="加载到上方结果区查看", use_container_width=True, disabled=not has_data):
                        st.session_state.results = rec_results
                        st.session_state.threshold = rec_threshold
                        st.session_state.scroll_to_results = True
                        st.toast(f"已加载 {ts_display} 的结果", icon="📜")
                        st.rerun()
                with col_csv:
                    st.download_button(
                        label="CSV",
                        data=csv_bytes,
                        file_name=f"volume_report_{rec_id}.csv",
                        mime="text/csv",
                        use_container_width=True,
                        key=f"csv_{rec['id']}",
                        disabled=(not has_data),
                    )
                with col_json:
                    st.download_button(
                        label="JSON",
                        data=json_bytes,
                        file_name=f"volume_report_{rec_id}.json",
                        mime="application/json",
                        use_container_width=True,
                        key=f"json_{rec['id']}",
                        disabled=(not has_data),
                    )
                with col_del:
                    if st.button("🗑️", key=f"del_{rec['id']}", help="删除此条记录", use_container_width=True):
                        delete_history_record(rec["file_path"])
                        st.toast("已删除", icon="🗑️")
                        st.rerun()


def parse_douyin_links(text: str) -> list:
    """从文本中解析抖音链接（每行一个，支持短链/长链）"""
    if not text:
        return []
    links = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if line and ("douyin.com" in line or "iesdouyin.com" in line):
            links.append(line)
    return links


def run_detection_files(uploaded_files, threshold: float, results_sink: list = None):
    """本地文件检测流程，结果存入 st.session_state"""
    try:
        ffmpeg_path = find_ffmpeg()
    except RuntimeError as e:
        st.error(str(e))
        return

    results = [] if results_sink is None else results_sink
    progress = st.progress(0.0, text="准备检测...")

    # 保存上传文件到临时目录并检测
    with tempfile.TemporaryDirectory() as tmp_dir:
        for i, f in enumerate(uploaded_files):
            progress.progress(
                (i / len(uploaded_files)),
                text=f"检测中 ({i+1}/{len(uploaded_files)}): {f.name}"
            )

            # 写入临时文件
            tmp_path = os.path.join(tmp_dir, f.name)
            with open(tmp_path, "wb") as buf:
                buf.write(f.getbuffer())

            info = detect_volume(tmp_path, ffmpeg_path)
            info["size_mb"] = round(len(f.getbuffer()) / 1024 / 1024, 2)
            info["source"] = "本地文件"
            results.append(info)

        progress.progress(1.0, text="检测完成！")

    if results_sink is None:
        st.session_state.results = results
        save_to_history(results, threshold, source_type="local")


def run_detection_douyin(links: list, threshold: float, results_sink: list = None):
    """抖音链接检测流程，结果存入 st.session_state"""
    try:
        ffmpeg_path = find_ffmpeg()
    except RuntimeError as e:
        st.error(str(e))
        return

    results = [] if results_sink is None else results_sink
    progress = st.progress(0.0, text="准备检测抖音视频...")

    total_cost = 0
    latest_remaining = None

    for i, link in enumerate(links):
        progress.progress(
            (i / len(links)),
            text=f"检测中 ({i+1}/{len(links)}): {link[:60]}..."
        )
        info = detect_douyin_volume(link, ffmpeg_path)
        info["source"] = "抖音链接"
        results.append(info)

        cost = info.get("api_cost")
        remaining = info.get("api_remaining")
        if cost is not None:
            total_cost += cost
        if remaining is not None:
            latest_remaining = remaining

    progress.progress(1.0, text="检测完成！")

    # 弹窗提示 API 资源点消耗
    if total_cost > 0 or latest_remaining is not None:
        cost_str = f"本次消耗 **{total_cost}** 点" if total_cost > 0 else ""
        remain_str = f"当前剩余 **{latest_remaining}** 点" if latest_remaining is not None else ""
        parts = [p for p in [cost_str, remain_str] if p]
        msg = " | ".join(parts)
        st.toast(msg, icon="💡")
        st.session_state["api_usage_msg"] = msg

    if results_sink is None:
        st.session_state.results = results
        save_to_history(results, threshold, source_type="douyin")


def build_dataframe(results: list, threshold: float) -> pd.DataFrame:
    """根据 results 构建 DataFrame"""
    rows = []
    for r in results:
        mean = r["mean_volume_db"]
        max_v = r["max_volume_db"]

        # 错误项单独标记
        if r.get("error"):
            status = "⚠️ 失败"
        elif mean is None:
            status = "⚠️ 无音轨"
        elif mean < threshold:
            status = "❌ 过低"
        else:
            status = "✅ 正常"

        clipping = "⚠️ 是" if (max_v is not None and max_v >= -0.01) else "否"

        # 抖音视频展示作者昵称，本地文件展示大小
        nickname = r.get("nickname", "")
        size_mb = r.get("size_mb", "")

        # 来源：本地文件直接显示，抖音链接展示为超链接文本
        source = r.get("source", "")
        source_url = r.get("source_url", "")
        if source == "抖音链接" and source_url:
            source_display = source_url
        else:
            source_display = source

        rows.append({
            "名称": r["video"],
            "来源": source_display,
            "作者": nickname if nickname else "",
            "大小(MB)": size_mb if size_mb else "",
            "平均音量(dBFS)": mean if mean is not None else "N/A",
            "最大音量(dBFS)": max_v if max_v is not None else "N/A",
            "削波": clipping,
            "状态": status,
            "处理时间(秒)": r.get("processing_time_sec", ""),
            "错误信息": r.get("error", ""),
        })

    df = pd.DataFrame(rows)

    def sort_key(v):
        if isinstance(v, (int, float)):
            return v
        return 999
    df["_sort"] = df["平均音量(dBFS)"].apply(sort_key)
    df = df.sort_values("_sort", ascending=True).drop(columns=["_sort"]).reset_index(drop=True)
    return df


def render_results(results: list, threshold: float, record_ts: str = None, title: str = "📋 检测结果"):
    """渲染结果（从 session_state 调用，rerun 不丢失）

    record_ts: 历史记录的时间戳（如 "20260715_143025"），用于下载文件名；None 表示当前结果
    """
    # 从 session_state 触发自动滚动到结果区（查看历史记录时使用）
    if st.session_state.get("scroll_to_results"):
        st.session_state.scroll_to_results = False
        st.html(
            """
            <script>
                (function() {
                    const header = document.querySelector('[data-testid="stHeadingWithActionElements"]');
                    if (header) header.scrollIntoView({behavior: 'smooth', block: 'start'});
                })();
            </script>
            """
        )

    st.subheader(title)

    df = build_dataframe(results, threshold)

    total = len(df)
    normal = len(df[df["状态"] == "✅ 正常"])
    low = len(df[df["状态"] == "❌ 过低"])
    failed = len(df[df["状态"].isin(["⚠️ 无音轨", "⚠️ 失败"])])

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("总计", total)
    col2.metric("✅ 正常", normal)
    col3.metric("❌ 过低", low)
    col4.metric("⚠️ 异常", failed)

    st.write("")

    styled = df.style.map(color_status, subset=["状态"])
    st.dataframe(
        styled,
        column_config={
            "来源": st.column_config.LinkColumn(
                "来源",
                display_text="🔗 打开链接",
                help="点击跳转到原视频页面"
            )
        },
        use_container_width=True,
        hide_index=True
    )

    low_df = df[df["状态"] == "❌ 过低"]
    if len(low_df) > 0:
        st.warning(f"发现 {len(low_df)} 个音量过低的视频，建议重新录制：")
        for _, row in low_df.iterrows():
            st.write(f"  - {row['名称']}  (平均 {row['平均音量(dBFS)']} dBFS)")

    fail_df = df[df["状态"] == "⚠️ 失败"]
    if len(fail_df) > 0:
        st.error(f"检测失败 {len(fail_df)} 个：")
        for _, row in fail_df.iterrows():
            st.write(f"  - {row['名称']}  → {row['错误信息']}")

    # 下载按钮（数据每次 rerun 重新生成，不依赖临时文件）
    st.subheader("💾 导出结果")
    col_csv, col_json = st.columns(2)

    ts = record_ts if record_ts else datetime.now().strftime('%Y%m%d_%H%M%S')
    scan_time_str = record_ts.replace("_", " ") if record_ts else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if record_ts and len(record_ts) == 15:
        scan_time_str = f"{record_ts[:4]}-{record_ts[4:6]}-{record_ts[6:8]} {record_ts[9:11]}:{record_ts[11:13]}:{record_ts[13:15]}"

    with col_csv:
        csv_df = df.map(sanitize_for_csv)
        csv = csv_df.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "📥 下载 CSV",
            data=csv,
            file_name=f"volume_report_{ts}.csv",
            mime="text/csv",
            use_container_width=True
        )

    with col_json:
        payload = {
            "benchmark": "0 dBFS (digital full scale)",
            "scan_time": scan_time_str,
            "threshold_dbfs": threshold,
            "results": results,
        }
        st.download_button(
            "📥 下载 JSON",
            data=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            file_name=f"volume_report_{ts}.json",
            mime="application/json",
            use_container_width=True
        )


def color_status(val):
    """根据状态返回颜色样式"""
    if val == "❌ 过低":
        return "color: red; font-weight: bold"
    elif val == "✅ 正常":
        return "color: green"
    elif val == "⚠️ 无音轨":
        return "color: orange; font-weight: bold"
    elif val == "⚠️ 失败":
        return "color: gray; font-weight: bold"
    return ""


def sanitize_for_csv(val):
    """防止 CSV 被 Excel 误解析为公式

    当单元格以 = + - @ 开头时，Excel 会将其识别为公式，导致 #NAME? 错误。
    在前面加单引号可强制作为纯文本显示。
    """
    if not isinstance(val, str):
        return val
    if val and val[0] in ('=', '+', '-', '@'):
        return "'" + val
    return val


if __name__ == "__main__":
    main()
