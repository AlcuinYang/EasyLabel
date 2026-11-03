# EasyLabel

面向“同场景、多版本”知觉研究的多人标注服务。数据的基本单位是“场景 × 版本”，主标签来自同一场景内的比较。一个系统，三种任务模板（外加成对比较末尾的差别来源追问块）：

| 模板 | 呈现 | 作答 | 服务 |
|---|---|---|---|
| A 多选网格 | 同场景 6 个版本，3×2，可放大 | 多选“已达到应有的样子”或“都没有”，再挑最好的一张 | E0 |
| B 成对比较 | 同场景两版本并排 | 左 / 差不多 / 右（或五档） | E1、E2、E4、正式采集 |
| B+ 追问 | 重新展示本人刚判过的非平局对 | 影调 / 色彩 / 两者 / 其他 / 看不出 | 修正 #4 |
| C 单图区域点选 | 单图叠预计算分割块 | 整图 5 档，再点 ≤ 3 块并标类型和方向 | E3 |

设计依据见 [docs/v1-plan.md](docs/v1-plan.md)。旧版（单人三轮协议、单图分类、候选审核）保存在 tag `legacy-v0`。

## 安装

需要 Python 3.10+ 和 Node 20（仅构建前端时需要）。

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cd web && npm ci && npm run build && cd ..     # 前端构建到 easylabel/server/static/
```

## 先试一下

```bash
easylabel --data ./demo-data demo     # 合成 6 个场景 × 12 版本，发布 E0 / E1 / E3 三个演示实验，打印邀请链接
easylabel --data ./demo-data serve    # 打印管理端登录链接
```

用浏览器打开任一邀请链接即可按评分者流程走一遍。演示图片是程序合成的，不是研究数据。

## 一次实验的流程

1. **导入刺激**。`scenes.csv` 描述场景（基调、明暗跨度、题材、色彩强度、划分），`stimuli.csv` 描述版本（来源类型、算子、方向、强度、母版本、来源数据集与许可）。整批校验，任何一处不过就全部不写入。系统从主图统一出图：sRGB ICC、JPEG q95 4:4:4、去 EXIF、哈希命名，每个 CSS 长边各生成 1×/1.5×/2×，不放大。
   ```bash
   easylabel --data DATA import-stimuli --scenes scenes.csv --stimuli stimuli.csv --root SRC \
     --css-long-edge 1024 --css-long-edge 480 [--strict-composition]
   easylabel --data DATA derive --css-long-edge 900          # 之后补充新尺寸
   easylabel --data DATA import-segmentation seg.csv --root SRC   # 模板 C
   ```
2. **写配置并创建草稿**：见带注释的 [docs/examples/experiment.yaml](docs/examples/experiment.yaml)。`easylabel --data DATA create-experiment config.yaml`（草稿可 `--replace`）。
3. **冻结并发布**：`freeze` 计算配置哈希（含题目、派生图与分割图的哈希），之后不可改；`publish` 开放作答。也可在管理端操作。
4. **邀请**：`easylabel --data DATA invite ID --tier lab|remote --count 20`，或在管理端生成。链接决定档位；组别按人自动均衡，也可指定。众包平台可在链接后加 `?src=…&pid=…`，评分者完成一块后显示完成码。
5. **采集**：评分者流程为 邀请 → 知情同意 → 基本信息 → 显示检查（近黑 / 近白台阶、像素条纹、设备像素比）→ 练习（只有练习有反馈）→ 题块。服务器按块规划题目：核心集优先，再按覆盖或主动策略扩展；同一人不重复看同一对；同场景至少间隔 `scene_gap` 题；按配置插入隐藏重复、左右互换和金标题。
6. **看进度和质控**：管理端显示场景 × 人次热图，以及每位评分者的自一致率、互换一致率、金标题正确率、反应时和显示检查结果。
7. **导出与分析**：
   ```bash
   easylabel --data DATA export ID OUT/
   python -m easylabel_analysis OUT/        # 生成 OUT-analysis.json 与 OUT-report.md
   ```
   导出包含长格式作答表、完整事件日志、刺激、评分者、冻结配置、按许可分组的来源 ID，以及带哈希的 `manifest.json`。排除规则只做标记，不删除数据。

## 评分端的呈现约束

- 固定 128 中性灰底，界面全部无彩色，没有主题切换。
- 每张图按服务器给出的 CSS 尺寸原样绘制，并选用与设备像素比相符的文件，不做 CSS 缩放。
- 窗口放不下、浏览器缩放改变时不显示题目。
- 前端只拿到不透明的题目令牌，拿不到文件名、哈希或元数据。
- 键位：数字键作答，Backspace 撤回上一题，Esc 暂停（暂停期间不计时），N 写备注。

## 合成刺激（离线）

```bash
easylabel stimgen --spec docs/examples/stimgen-spec.yaml --jobs jobs.csv --root SRC --out SRC/generated
easylabel stimgen-verify --stimuli stimuli.csv --root SRC      # 按 regen_recipe 重新生成并逐字节比对
```

各算子在 OKLab 或线性光空间实现（影调、灰罩、色度、联合、局部编辑），参数写在规格文件里。每个输出都带可复现配方，因此数据集可以只发布“标签 + 再生成脚本 + 来源 ID”。

## 开发

```bash
python -m pytest -q                             # 后端与分析
cd web && npm run typecheck && npm test         # 前端单元测试
cd web && npm run e2e                           # Playwright 端到端（首次会生成演示数据）
cd web && SHOTS_DIR=/tmp/shots npx playwright test screens   # 把评分端和管理端每个页面截图，供界面检查
python tools/simulate.py --scenes 238 --raters 10   # 规模与采样策略模拟（已知真值）
```

目录：`easylabel/core`（数据库、导入、配置、评分者流程、导出），`easylabel/scheduler`（约束排序、块规划、策略），`easylabel/server`（FastAPI），`easylabel_analysis`（离线分析，不依赖服务），`web/`（React 前端，评分端和管理端两个入口）。

部署为单进程（调度用进程内锁保证名额不超发），数据库为 SQLite（WAL）。对外服务时在前面放一个负责 HTTPS 的反向代理，并设置 `EASYLABEL_ADMIN_TOKEN`。
