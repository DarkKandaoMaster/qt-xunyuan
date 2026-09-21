# QT 视频数据生产工具

面向单人、短周期生产的本地 MVP。设计目标是提高单位时间合格视频产出量，同时严格遵守《QT寻源数据规则（供应商用）》：程序只对可确定的硬失败自动淘汰，低置信度视觉判断保持 `UNKNOWN`，PDF 内部冲突保持 `CONFLICT`，最终语义判断由人工完成。

## 已实现

- 单一业务规则源：`rules/qt_rules_v4.yaml`
- 显式冲突库：`rules/conflicts.yaml`
- 47 个详细子单元和搜索模板
- `PASS / FAIL / UNKNOWN / NOT_APPLICABLE / CONFLICT` 五态规则引擎
- R1-R17、规格检查、T6 环视/回访、R10/R13 豁免和边界冲突
- YouTube 扁平元数据寻源、URL 导入、`platform + video_id` 去重
- 元数据 / 480p 代理 / 最终源三级下载
- FFmpeg 镜头检测（默认场景阈值 0.28）、安全边距、少于 5 秒镜头过滤
- T1.1 人物主体连续性检测：先过镜头切换，再把持续离场区间切开，不整条淘汰
- 动作边界辅助：仅在主体持续离场且出现明确、连续的运动上升前转折点时给出建议，最多回退 3.5 秒；建议不会自动生效，人工审核页可采用建议、当前位置设点和保存裁剪
- 代表帧 dHash 二次去重
- ffprobe、静默、黑帧、宽高比、分辨率、帧率、编码和音轨检测
- 人工审核页、键盘快捷键、规则原文/页码抽屉、标签覆盖、硬失败二次确认
- 接受后高质量源下载、精确剪片、独立最终 QA、两级交付目录
- SQLite 持久化、操作追溯、流量统计、配额仪表盘、CSV 交付表
- 生产台各列表独立分页，每页 20 条
- 代理与最终源物理隔离；交付代码拒绝从 `proxy` 目录复制

规则文件使用 JSON 语法（JSON 是 YAML 1.2 的合法子集），因此核心运行时不依赖 PyYAML。

## 快速启动

在 PowerShell 中运行：

```powershell
./start.ps1
```

首次启用 T1.1 主体连续性切分时运行一次：

```powershell
./install-subject-ai.ps1
```

该脚本安装 OpenCV，并把约 22 MB 的离线人物检测模型放入 `tools/models/`；视频帧不会上传到外部服务。

然后打开：

```text
http://127.0.0.1:8765
```

本地开发工作区可在 `tools/` 内放置 FFmpeg、ffprobe 和 yt-dlp，程序会自动发现；该目录包含大型运行时文件，不提交到 Git。克隆仓库后请自行安装这些命令行工具，或复制 `.env.example` 为 `.env` 并配置其路径：

```text
FFMPEG_BIN=C:\path\to\ffmpeg.exe
FFPROBE_BIN=C:\path\to\ffprobe.exe
YTDLP_BIN=C:\path\to\yt-dlp.exe
YTDLP_JS_RUNTIME=node:C:\path\to\node.exe
YTDLP_COOKIES_FROM_BROWSER=firefox
YTDLP_SLEEP_INTERVAL=5
YTDLP_MAX_SLEEP_INTERVAL=10
```

YouTube 的公开素材解析需要 JavaScript 运行时。程序会自动探测 Deno 或 Node.js，也可通过
`YTDLP_JS_RUNTIME` 手工指定。不要在配置文件中填写 YouTube 账号、密码或验证码。
如果公开取流触发登录验证，可通过 `YTDLP_COOKIES_FROM_BROWSER` 使用本机已关闭浏览器的
登录会话；该配置只保存浏览器名称，不保存 Cookie 内容。建议使用专用 Firefox 资料和备用账号。

## 生产流程

1. 在“生产台”输入英文搜索词和目标单元，只拉取元数据。
2. 根据分数和配额缺口选择少量来源，点击“下载代理”。
3. 点击“镜头分析”，系统先检测镜头切换，再对 T1.1 单镜头段检测人物主体连续性；主体持续离场时会在最后安全画面处结束当前段，并把重新出现后的不少于 5 秒片段另建候选，而不是淘汰整条来源。
   分析成功后来源自动进入“已分析来源”，需要重做时可移回候选来源。
4. 进入“人工审核”，先查看动作边界风险；可采用系统建议，或播放到准确位置后用“当前位置设起点/终点”微调并保存，再用 `A / R / E / Space / ← / →` 审核。
5. 接受时必须确认桶、单元、人称；确定性硬失败需要二次确认并记录覆盖。
6. 回到生产台，对已接受候选点击“最终处理”。
7. 最终 QA 为 `PASS` 时，文件进入 `data/deliverable/QT寻源数据/桶/人称/`。
8. 点击“导出交付表”生成 UTF-8 BOM CSV；字段固定为“人称 / OSS路径 / 交付时间 / 统合单元 / 分辨率 / 时长”，未接入 OSS 前路径统一填写 `OSS`。
   最终处理通过的候选在导出后进入“已处理”，也可以移回最终处理列表重新操作。

代理资源只用于切镜与人工预览。R9 分辨率硬判定会延后到最终原视频下载、精确剪片之后执行，
不会再用 480p 代理分辨率淘汰候选。同一来源产生的最终分片按时间顺序命名为
`来源文件名-1.mp4`、`来源文件名-2.mp4`……。

YouTube 若对某条视频要求登录或 Cookie，工具会直接报错，不会绕过登录、验证码、DRM 或平台限制。可换用公开可访问来源，或只登记候选后人工处理合法素材。

## 数据目录

```text
data/
  metadata/
  proxy/          # 仅分析与审核，禁止交付
  original/       # 人工接受后才下载
  clips/          # 精确剪片输出
  rejected/
  deliverable/    # 唯一交付读取目录
  qt_tool.sqlite3
```

## 测试

```powershell
python -m unittest discover -v
```

测试覆盖任务书列出的关键规则样例，包括 4.9 秒失败、三档时长、15/30 秒冲突、素材类型、游戏录制、横竖屏、T6 豁免、T8.4 慢动作豁免和 T8.2 航拍冲突。

## 当前边界

- T1.1 已启用离线“显著人物主体”连续性预检；人物身份和语义仍由人工复核。其他桶的动物、载具、手部或物体主体暂不套用人物模型，以免误切。
- YouTube 可进行不登录的扁平元数据搜索；视频下载是否可用由来源当时的公开访问策略决定。
- 自动缓存清理暂未启用，避免误删；可按 `data/proxy` 与 `data/original` 状态手工清理。
- OSS 上传不在 MVP 内；交付目录和 CSV 已准备好。

## 规则审计入口

- `rules/qt_rules_v4.yaml`：唯一业务规则源
- `rules/conflicts.yaml`：PDF 内部冲突与临时策略
- `rules/search_templates.yaml`：只影响候选搜索，不影响业务判定
- 审核页点击任一规则，可查看 PDF 页码、原文、系统解析和当前检测依据
