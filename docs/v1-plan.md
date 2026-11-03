# EasyLabel v1 实施方案

依据《第一版数据与标注系统设计》。v1 推倒重来：不导入旧数据，不兼容旧结果目录。旧代码打 tag `legacy-v0` 后从工作树删除，需要移植的函数用 `git show legacy-v0:<path>` 取。

## 已定决策

| 议题 | 决定 |
|---|---|
| 核心集 | 跨内容校准子集（约 60 场景 × 约 6 对 ≈ 360 题，约 4 块）所有人都答；答完后有余力的人继续做覆盖 + 主动扩展 |
| 屏幕 | 要求标注者优先使用 4K 屏；像素级呈现见下文“显示” |
| 部署与合规 | 不在本方案范围内；系统只需支持邀请链接和多人同时作答 |
| 候选审核台 review_app | 删除；题材标签写进 `scenes.csv` 导入 |
| 旧数据 | 不导入 |
| 金标题 | 待定：只在评估性问题组计分，还是所有组都计分（见“待定”） |
| FiveK / PPR10K 许可 | 已核查（2026-11-03，网页调研，非法律意见）：两者均仅限非商业研究。FiveK 按照片 ID 分属 LicenseAdobe（2690 张）和 LicenseAdobeMIT（2310 张），文字上允许修改和再分发，但须附对应许可证；PPR10K 没有明确授予再分发权。发布方式默认为“标签 + 确定性再生成脚本 + 图像 ID”，FiveK 派生图可附许可证作为可选附件；深肤色人像需另找来源，并逐张记录许可证 |

## 技术选型

- 后端：Python 3.12 + FastAPI + SQLAlchemy 2.0 Core；SQLite（WAL），表结构写成 Postgres 兼容。
- 前端：Vite + React + TypeScript，评分端和管理端是两个入口；构建产物由 FastAPI 以静态文件提供。
- 分析：独立包 `easylabel_analysis`，只读导出文件；服务端只单向调用它做主动采样用的 BT 估计。

## 目录

```
easylabel/
  core/        db.py events.py experiments.py stimuli.py
  scheduler/   constraints.py planner.py strategies/{coverage,core_fixed,active_bt,followup}.py
  server/      app.py api_rater.py api_admin.py auth.py
  cli.py       import-stimuli | create-experiment | preview | publish | export
easylabel_analysis/  bt.py reliability.py connectivity.py qc.py e0.py regions.py io.py
web/src/rater/  shell/{Consent,Profile,DisplayCheck,Practice,Block} templates/{Grid,Pair,Followup,Region}
web/src/admin/  Experiments Import Preview Progress RaterQC Export
stimgen/        离线：合成阶梯、局部编辑、分割预计算
```

## 从 legacy-v0 移植

| 来源 | 去向 |
|---|---|
| `scaling.fit / connectivity / reliability / wilson` 及测试 | `easylabel_analysis/` |
| `pair_lists.read_pairs` 的校验规则 | 清单导入 |
| `schedule_list` 的场景间隔、隐藏重复、左右互换 | `scheduler/constraints.py` |
| `protocol.pixel_features` 的 sRGB/ICC 校验 | `core/stimuli.py` |
| `pair_protocol.load_events`、撤销记为事件 | `core/events.py`（改为数据库只追加表） |
| `measurement_static` 的呈现原则 | `web/src/rater/templates/Pair` |

不移植：三轮协议、单图分类、候选审核、DeepSeek、休息提醒。

## 数据表

| 表 | 关键字段 |
|---|---|
| scenes | scene_id, tone_key, dynamic_range, subject_tags, chroma_level, split, e2_class |
| stimuli | sha256, scene_id, version_label, source_type, is_real, operator, direction, strength, parent_sha, derivatives, origin_dataset, origin_id, license_id, regen_recipe |
| segmentations | sha256, label_map_png, regions[{id, area, centroid, coarse}] |
| experiments | id, protocol_version, config_json, config_hash, code_git_sha, status(draft/frozen/live/closed) |
| items | experiment_id, kind(pair/grid/single/gold/practice), stimuli, scene_id, is_core |
| raters | anon_id, profile, created_via, external_pid |
| display_checks | rater, session, black_steps_seen, white_steps_seen, pixel_test, screen, dpr, viewport, visual_viewport_scale, passed |
| enrollments | rater × experiment, tier, arm, practice_passed |
| assignments | rater, item, block_id, position, left_right, planned_role, reserved_until |
| events | 只追加：response / undo / note / pause / zoom / display_check / consent |
| sessions, blocks | 起止时间、完成码 |

## 刺激导入

1. 元数据必填：source_type ∈ {expert, camera_default, retouch_clear, retouch_flat, synth_ladder, local_edit}；合成版本必须填 operator / direction / strength / parent；is_real 由 source_type 推出。
2. 组成约束：每场景 12 个版本、真实版本 ≥ 4。预实验配置中为警告，正式配置中为错误。
3. 系统统一出图：从主图生成嵌入 sRGB ICC 的 JPEG（q95）、去 EXIF、哈希命名，按配置的 CSS 长边生成 1×、1.5×、2× 三份。
4. split 只写在场景表里，版本自动继承；留出算子在 `splits.yaml` 声明。

## 显示（4K 屏）

浏览器 CSS 像素 × devicePixelRatio = 设备像素。4K 屏的常见设置：

| 设置 | dpr | CSS 宽 | 两张横图按 1024 并排 | 像素级准确 |
|---|---|---|---|---|
| Windows 100% | 1 | 3840 | 放得下 | 是（1× 图） |
| Windows 150% | 1.5 | 2560 | 放得下 | 是（1.5× 图） |
| macOS 默认（看起来像 1920×1080） | 2 | 1920 | **放不下** | 是（2× 图） |
| macOS 缩放（看起来像 2560×1440） | 报 2 | 2560 | 放得下 | **否**：系统先按 2× 渲染再缩到面板 |

处理方式：

- `allowed_dpr: [1, 1.5, 2]`，按 dpr 选对应倍率的图；
- 显示检查里加一项设备像素条纹测试：显示 1 设备像素宽的黑白竖条，被重采样时会变成灰色或摩尔纹，让标注者报告看到的是细条纹还是均匀灰，以此排除 macOS 缩放模式（浏览器本身检测不到）；
- 窗口放不下当前题目就不出题；
- 长边的具体数值仍由预实验决定，需要和 macOS 默认档的 1920 CSS 宽一起考虑。

## 实验配置示例

```yaml
protocol_version: "1.0"
template: pair                      # grid | pair | region
question: "哪一张不同明暗、不同物体之间的差异更能被区分开？"
response_scale: 3                   # 3 | 5
items: lists/e1_pilot.csv
target_per_item: 5
arms: []                            # E2 组间设计
block_size: 100
practice: practice/anchors_v1.csv
followup: {enabled: true, rate: 0.2, non_tie_only: true}
qc: {hidden_repeat_rate: 0.05, swap_rate: 0.05, gold: gold/e1.csv, gold_rate: 0.05}
scheduler: {strategy: core_then_active, scene_gap: 3, core: lists/calibration_core.csv}
display: {pair_css_long_edge: 1024, grid_css_long_edge: 480, allowed_dpr: [1, 1.5, 2]}
tiers: [lab, remote]
exclusion: {gold_accuracy_min: 0.80, rt_fast_ms: 300}
```

## 调度器

- 评分者开始一个块时，服务器一次规划 100 题：核心集 → 覆盖 / 主动扩展，同时插入隐藏重复、互换、金标题，块末尾接 B+ 追问；规划和种子写入 assignments。
- 预留加过期：名额按“已完成 + 未过期的预留”计算，防止多人同时作答时超发。
- 策略接口 `propose(state, rater, n)`：core_fixed、coverage、active_bt（后台每 K 条作答按场景重拟合 Davidson，选信息量最大的对）、followup。
- 同一人不重复看同一对（计划内的隐藏重复除外）。

## 里程碑

| 里程碑 | 内容 | 验收 |
|---|---|---|
| M0 | tag `legacy-v0`，删除旧代码，新包骨架，CI | CI 通过 |
| M1 | schema、刺激导入与出图、配置冻结、事件表、邀请与会话、coverage 调度、导出、CLI | 238 场景清单能导入；模拟评分者能跑完一个实验并导出 |
| M2 | 评分者流程、显示门槛与像素测试、Grid、Pair（3/5 档） | Playwright 端到端测试 |
| M3 | 分析包移植、QC 指标、E0 汇总、最小管理网页 | 在实验室档跑 E0 |
| M4 | 金标题、隐藏重复/互换、B+、E2 分组、active_bt、众包参数与完成码 | 用已知真值的模拟评分者验证主动采样 |
| M5 | stimgen：合成阶梯、局部编辑；每个版本输出可复现的生成配方（regen_recipe），固定版本号和参数 | 产出的清单能通过导入校验；从原始数据集重新生成的图与库内 sha256 一致 |
| M6 | 分割预计算、Region 模板、块级与粗级一致性 | E3 预实验 |

## 待定

- 金标题：设计 4.3 写的是“新增金标题，例如重度压缩对比参照这种明显对；金标题正确率 < 80% 就排除”。E2 的“喜好”组没有客观正确答案，需要定这条排除规则是否也用在该组。
- 4K + macOS 默认档下的并排长边。

## 实施进度（2026-11-03，分支 v1）

| 里程碑 | 状态 | 说明 |
|---|---|---|
| M0 | 完成 | tag `legacy-v0`；旧代码已删除；新包、CI |
| M1 | 完成；真实数据待验 | 导入与出图、配置冻结、事件表、邀请与会话、调度、导出、CLI。真实 238 场景清单需在挂载 ResearchData 后验证；238 个合成场景 × 12 版本已导入通过 |
| M2 | 完成 | 评分者流程、显示检查（台阶 + 像素条纹 + dpr）、窗口与缩放拦截、Grid / Pair（3/5 档）；Playwright 覆盖 |
| M3 | 完成 | 分析包（Davidson/BT、连通性、质控、E0 汇总）、`python -m easylabel_analysis` 报告、管理网页 |
| M4 | 完成；主动采样收益未证实 | 金标题（可按组启用：`qc.gold_arms`）、隐藏重复/互换、B+ 追问、E2 分组、active_bt、众包参数与完成码。模拟中主动采样与覆盖式差别在噪声内，需在预实验数据结构确定后再评估 |
| M5 | 框架完成 | `easylabel stimgen` / `stimgen-verify`；算子参数待 E0 后确定 |
| M6 | 部分完成 | 分割图导入、Region 模板、块级与粗级一致性；自动分割（超像素 + 合并到 8–20 块）尚未实现 |

与方案的出入：导出只有 CSV（没做 Parquet）；策略放在同一个模块；刺激生成放在 `easylabel/stimgen.py`；导入实验配置只能用命令行，管理网页负责冻结、发布、关闭、邀请、进度、质控、预览和导出。
