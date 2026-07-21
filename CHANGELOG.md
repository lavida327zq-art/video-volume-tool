# 视频音量检测工具 - 开发日志

## 2026-07-15

### 创建核心检测脚本 `detect_volume.py`

- 使用 FFmpeg 的 `volumedetect` 滤镜提取视频音频的平均音量（RMS）和最大音量（峰值）
- 所有数值基于 **0 dBFS（数字满量程）** 统一基准，可横向比较不同视频
- 支持单文件检测和目录批量扫描
- 支持 `--threshold` 参数自动标记音量过低的视频
- 支持 `--json` 参数导出 JSON 结果
- 优先使用 `/opt/homebrew/bin/ffmpeg`，回退到 PATH 中的 ffmpeg

**相关文件**：`detect_volume.py`

---

### 创建 Streamlit Web 界面 `volume_web.py`

- 拖拽/点击上传，支持多文件批量
- 可调节音量阈值（默认 -30 dBFS）
- 结果表格颜色高亮（正常/过低/无音轨/削波警告）
- 按平均音量从低到高排序，问题视频优先展示
- 汇总卡片（总数/正常/过低/无音轨）
- CSV / JSON 一键导出

**相关文件**：`volume_web.py`

---

### 新增：抖音视频链接解析与在线音量检测

- **需求**：从本地视频扩展到抖音已发布视频，用户直接粘贴抖音链接即可检测
- **实现方案**：
  - 调用 cyanlis 抖音详情 API：`POST https://cyanlis.cn/api/plugins/douyin/detail_v4?apiKey={API_KEY}`
  - API 文档地址：https://cyanlis.cn/frontend/docs/douyin_detail_v4.html
  - 请求参数：`url`（抖音分享链接）
  - 响应字段：`data.video_url` 为无水印视频播放链接
  - API Key 通过环境变量 `DOUYIN_API_KEY` 或 Streamlit Secrets 注入，不硬编码在源码中
- **在线分析**：
  - 使用 ffmpeg 直接读取 `data.video_url` 进行 `volumedetect`
  - 视频无需下载到本地磁盘，流式处理，速度更快
- **核心新增函数**：
  - `fetch_douyin_video_url()`：调用 API 获取 video_url 及作者、描述、时长等元信息
  - `detect_volume_online()`：对任意在线视频 URL 直接执行 ffmpeg 音量分析
  - `detect_douyin_volume()`：抖音链接 → API → ffmpeg 在线分析的完整流程
- **Web 界面改造**：
  - 新增「🔗 抖音链接」Tab，支持每行一个链接批量粘贴
  - 支持长链（`https://www.douyin.com/video/xxx`）和短链（`https://v.douyin.com/xxx`）
  - 结果表格新增「来源」「作者」列，支持本地文件和抖音链接合并检测

**相关文件**：`detect_volume.py`、`volume_web.py`

---

### Bug 修复：Pandas Styling 报错

- **问题**：`df.style.apply(color_status, axis=1, subset=["状态"])` 导致 `ValueError: The truth value of a Series is ambiguous`
- **原因**：`apply(axis=1)` 传入整行 Series，`val == "❌ 过低"` 返回 Series 而非布尔值，`if` 判断报错
- **修复**：改用 `df.style.map(color_status, subset=["状态"])`，`map` 对单个单元格逐个应用，传入值为普通字符串

---

### Bug 修复：点击下载后结果消失

- **问题**：点击下载按钮触发 Streamlit rerun，检测结果（局部变量）丢失，结果区消失
- **原因**：Streamlit 的核心机制是"每次交互重跑整个脚本"，局部变量在 rerun 后不保留
- **修复**：引入 `st.session_state` 持久化检测结果
  - 检测完成后存入 `st.session_state.results`
  - 主流程每次都从 `session_state` 读取并渲染结果
  - 文件变化（增删换文件）时自动清空旧结果，避免数据不一致

---

### **界面优化：参考信息固定展示**

- **问题**：阈值参考和使用说明放在 `if not uploaded_files` 分支内，上传文件后才可见，逻辑反直觉
- **调整**：
  - 将「📐 阈值参考」移至页面顶部，默认展开，三列彩色卡片（绿色正常/蓝色偏轻柔/红色过低）始终可见
  - 新增「📌 使用说明与限制」折叠面板，固定展示单次上传数量建议（≤20-30个）、单文件大小建议（≤100MB）、支持格式、指标说明
  - 检测设置区简化为仅保留阈值输入框，不再重复展示参考信息

### 阈值校准与体验优化

- **默认阈值调整为 -25 dBFS**：基于人工标注样本比对（7 条抖音视频，-25 阈值与人工判断完全一致），从 -30 改为 -25，更适合口播/轻声/孕期陪伴类内容
- **抖音链接来源展示为超链接**：结果表格「来源」列对抖音视频显示原链接，并配置 `LinkColumn`，点击可直接跳转
- **CSV 导出防 Excel 公式注入**：对作者名、视频名等字段中以 `=`、`+`、`-`、`@` 开头的内容自动加单引号转义，避免 Excel 解析为公式出现 `#NAME?` 错误
- **CSV 导出兼容新版 pandas**：`df.applymap()` 在 pandas 2.1+ 已移除，改为 `df.map()`

---

### Bug 修复：抖音链接第二次检测失败（CDN 拦截 + SSL 证书问题）

- **问题**：同一抖音链接第一次检测正常，第二次提示「未检测到音频流或无法解析音量」
- **根因分析**：
  1. **CDN UA 拦截（HTTP 460）**：原方案用 ffmpeg 直接流式读取 `video_url`，ffmpeg 默认 UA 为 `Lavf/x.x.x`，抖音 CDN 识别后直接返回 460 拒绝。第一次能成功是签名刚好在有效期窗口内，签名过期或被风控后即失败
  2. **SSL 证书主机名不匹配**：抖音部分 CDN 节点的证书 hostname 校验失败（如 `*.vegslb.com`），Python `urllib` 默认严格校验导致连接被拒
  3. **签名时效性**：`video_url` 中的 `sign=xxx` 参数有时效，过期后链接失效
- **修复方案**：将「ffmpeg 直接在线读取」改为「Python 下载到临时文件 → ffmpeg 分析本地文件 → 自动删除」三步模式
  - 新增 `_download_video()` 函数，下载时携带浏览器 UA（Chrome 126）和 `Referer: https://www.douyin.com/`，绕过 CDN 460 拦截
  - 下载请求禁用 SSL 主机名校验（`ctx.check_hostname = False`），解决 CDN 证书 hostname mismatch
  - 使用 `tempfile.mkstemp(suffix=".mp4")` 创建临时文件，分析完成后在 `finally` 块中删除，不污染磁盘
  - `detect_volume_online()` 改为调用 `detect_volume()` 分析本地临时文件，复用已有逻辑
- **验证**：对 `https://www.douyin.com/video/7657202522629761512` 连续测试多次均稳定返回 `mean=-13.9, max=0.0`

**相关文件**：`detect_volume.py`（`_download_video()`、`detect_volume_online()`）

---

### 新增：API 资源点消耗弹窗提示

- **需求**：抖音解析 API 每次调用消耗资源点，需在检测完成后告知用户消耗数和剩余数
- **实现**：
  - `fetch_douyin_video_url()` 从 API 返回的 `msg` 字段（格式如 `"返回成功 [本次消耗:1 剩余:1348 今日:34 累计:50249]"`）中用正则提取「本次消耗」和「剩余」数值
  - `detect_douyin_volume()` 将 `api_cost` / `api_remaining` 带到返回结果
  - Web 界面 `run_detection_douyin()` 检测完成后累加所有链接的消耗总数，取最后一次 API 返回的剩余值
  - 使用 `st.toast()` 弹窗显示，例如：`💡 本次消耗 3 点 | 当前剩余 1345 点`
  - 多链接批量检测时累计消耗，剩余数取最新值
  - API 未返回 msg 或解析失败时不弹窗，不影响正常使用

**相关文件**：`detect_volume.py`（`fetch_douyin_video_url()`、`detect_douyin_volume()`）、`volume_web.py`（`run_detection_douyin()`）

---

### Bug 修复：系统代理导致网络请求 Connection refused

- **问题**：检测抖音链接时报错 `网络请求失败: <urlopen error [Errno 61] Connection refused>`
- **原因**：本地系统代理环境变量 `HTTP_PROXY`/`HTTPS_PROXY` 设置为 `http://127.0.0.1:7890`（如 Clash/Charles 等代理软件配置），但该端口无代理服务运行。Python `urllib` 默认自动读取系统代理，所有外发请求（API 调用、视频下载）都被转发到不存在的代理端口，直接被拒绝
- **修复**：
  - 新增 `_make_no_proxy_opener()` 函数，使用 `urllib.request.ProxyHandler({})` 创建完全禁用系统代理的 opener
  - `fetch_douyin_video_url()` 和 `_download_video()` 中的所有 `urlopen` 调用改用此 opener 直连，绕过系统代理
  - SSL 上下文通过 `HTTPSHandler(context=ctx)` 集成到 opener 中，避免参数传递问题

**相关文件**：`detect_volume.py`（`_make_no_proxy_opener()`）

---

### 新增：检测结果历史记录功能

- **功能**：每次检测完成后自动保存结果，后续可在「📜 历史记录」面板查看、重新加载、导出旧结果
- **存储策略**：
  - 保存位置：`.volume_history/` 隐藏目录（与 `volume_web.py` 同级）
  - 保留上限：最多保存最近 20 条记录
  - 过期清理：自动删除超过 7 天的历史记录
- **界面交互**：
  - 历史列表按时间倒序显示，带来源图标（📁 本地 / 🔗 抖音 / 🔀 混合）
  - 点击某条记录可加载到当前结果区查看完整表格
  - 支持删除单条记录或一键清空全部
  - 从历史记录导出 CSV/JSON 时，文件名会保留原始时间戳
- **实现要点**：
  - 自动保存：本地文件、抖音链接、混合检测三种场景都保存
  - 每次访问历史时自动执行清理，防止目录膨胀

**相关文件**：`volume_web.py`（历史记录相关函数）

---

### Bug 修复：抖音链接检测提示「未检测到音频流」（代理干扰 ffmpeg）

- **问题**：抖音链接之前能正常检测，突然全部提示「未检测到音频流或无法解析音量」
- **根因**：系统代理环境变量 `HTTP_PROXY`/`HTTPS_PROXY` 指向 `127.0.0.1:7890`，ffmpeg 子进程继承后访问抖音 CDN 走了不可用的代理，连接失败导致拿不到音频流
- **修复**：`detect_volume_online()` 构建 ffmpeg 子进程环境时清除 `HTTP_PROXY`、`HTTPS_PROXY`、`http_proxy`、`https_proxy` 四个变量，让 ffmpeg 直连 CDN
- **验证**：清除代理后同一链接成功返回 `mean=-13.7 dB, max=0.0 dB`

**相关文件**：`detect_volume.py`（`detect_volume_online()`）

---

### Bug 修复：`_parse_volume` 函数不存在导致 NameError

- **问题**：抖音链接检测报错 `NameError: name '_parse_volume' is not defined`
- **根因**：`detect_volume_online()` 中调用了不存在的 `_parse_volume()` 函数，应为直接使用 `MEAN_RE`/`MAX_RE` 正则匹配
- **修复**：改为 `MEAN_RE.search(stderr)` / `MAX_RE.search(stderr)` 直接匹配，与 `detect_volume()` 保持一致
- **注意**：修改后需清理 `__pycache__` 目录并重启服务，否则 Streamlit 加载旧 `.pyc` 缓存仍报错

**相关文件**：`detect_volume.py`（`detect_volume_online()`）

---

### 优化：API 资源点消耗提示持久展示

- **问题**：`st.toast()` 弹窗 4 秒后消失，页面 rerun 后也丢失，用户看不到资源点消耗信息
- **修复**：在 `session_state` 中存储消耗信息，结果区顶部用 `st.info()` 持久展示蓝色提示框（如 `💡 本次消耗 1 点 | 当前剩余 79079 点`），toast 弹窗保留作为即时反馈

**相关文件**：`volume_web.py`（`run_detection_douyin()`、结果渲染区）

---

### 新增：检测结果增加「处理时间」字段

- **需求**：在检测结果中展示每个视频的处理耗时，单位为秒
- **实现**：
  - `detect_volume()`：记录 ffmpeg 分析时间
  - `detect_volume_online()`：记录 ffmpeg 流式读取 + 分析时间
  - `detect_douyin_volume()`：记录 API 调用 + ffmpeg 分析的总时间（覆盖内层时间）
  - 失败的检测也会记录处理时间
- **展示**：结果表格新增「处理时间(秒)」列，CSV 导出自动包含；JSON 导出和历史记录使用原始 results 列表，天然包含该字段

**相关文件**：`detect_volume.py`（三个检测函数）、`volume_web.py`（`build_dataframe()`）

---
