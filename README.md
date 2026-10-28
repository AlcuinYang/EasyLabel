# EasyLabel

从 TongTou 独立拆出的本地图像标注台，可单独维护、测试和迭代。当前保留通透度研究的中文任务与协议；尚未改造成任意标签的通用平台。

## 安装与打开

支持 macOS / Linux，Python 3.10+（以下示例使用 3.12）。成对比较服务使用 Unix 文件锁，暂不支持原生 Windows。

```bash
git clone https://github.com/AlcuinYang/EasyLabel.git
cd EasyLabel
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m annotation_mvp.demo
```

安装后，macOS 也可双击根目录 `启动标注台.command`。浏览器自动打开 http://127.0.0.1:8765 ，终端须保持运行；Ctrl+C 停止。再次运行继续演示进度。换端口：`python -m annotation_mvp.demo --port 8870`。

演示自动生成 6 张合成图片和 15 个原始图对，另有 4 道隐藏质控题，保存在本仓库 `demo/`。这些是流程试用数据，不是人类研究数据；真实图片、旧标注与数据库不包含在仓库内。

## 正式成对比较

```bash
python -m annotation_mvp.measurement_app \
  --input /absolute/path/images \
  --results /absolute/path/experiment-results \
  --pairs /absolute/path/pairs.csv \
  --question "哪张更通透？" --seed 20261028 --port 8765 --open
```

清单格式如下。图片使用相对于 input 的路径，必须为 RGB JPEG；scene_id 表示同一场景的不同版本。单场景实验追加 `--scene-gap 0`，多场景默认两次同场景呈现间至少隔 3 题；无法满足约束会报错。

```csv
image_a,image_b,scene_id,pair_id,question_id
scene01/v1.jpg,scene01/v2.jpg,scene01,p01,limpidity
scene02/v1.jpg,scene02/v2.jpg,scene02,p02,limpidity
```

数字键 1/2/3 选择左/平局/右，Backspace 撤销，N 写备注，Esc 暂停。结果和图片快照写入指定 results；恢复时使用相同 input、results 和实验配置。完成后在网页分析，或用相同 input/results 追加 `--analyze`。模型分数仅在场景内可比；拟合结果不能证明通透度构念已经有效。

不传 `--pairs` 可运行保留的三轮测量流程，仍须显式指定 input/results。[完整协议](docs/protocols.md)说明原因归属、重测、隐藏重复、互换一致性与导出字段。

## 其他入口

- 单图分类：`python -m annotation_mvp.app --data ./data/round-01 --port 8766 --open`。支持图片导入、通透/不通透/无法判断、备注、SQLite 记录和 CSV 导出。
- 候选审核：`python -m annotation_mvp.review_app --batch /absolute/path/batch --port 8768`，随后打开 http://127.0.0.1:8768 。需自行准备原审核工具约定的清单；参数见 `--help`。可选的 DeepSeek 建议会发送图片到第三方服务，需先运行 `python -m pip install -e ".[review-ai]"`，并设置 `DEEPSEEK_API_KEY`。
- 安装同时提供 `easylabel`、`easylabel-classify`、`easylabel-review`、`easylabel-demo` 命令。

## 开发与验证

```bash
python -m unittest discover -s annotation_mvp -t . -v
node --check annotation_mvp/static/app.js
node --check annotation_mvp/measurement_static/app.js
```

核心模块位于 `annotation_mvp/`，`tools/` 含合成演示和重测工具；`docs/` 保存协议和迁移边界。仅保留旧模块名，不依赖 TongTou 仓库。`.gitignore` 排除默认数据目录、日志、数据库、密钥和构建产物；放在其他目录的自有数据仍需自行避免提交。

[迁移说明](docs/migration.md)。原项目的数据和历史记录保持原样，继续旧实验前应完整备份并确认路径。当前工具面向本机可信使用，绑定 127.0.0.1。
