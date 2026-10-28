# v0 成对比较试标（measurement_app.py）

本手册保留迁出前的实验协议说明，运行路径已适配 EasyLabel。所有成对比较实验均须显式指定 `--input` 与 `--results`。新安装不会包含旧题库或历史标注。

## 导入图对清单

CSV 必须有 `image_a,image_b,scene_id`；可选 `pair_id,question_id`，后者默认为 `limpidity`。图像须为可解码的三通道 JPEG；不允许自比、同一问题下重复无序对、重复 pair_id、空场景和越级路径。同一版本只能归属一个场景。`scene_id` 指同一场景的多个版本，不是“室内”等题材类别。整个清单验证通过、调度完成后才创建队列；不可满足约束时明确报错。

```csv
image_a,image_b,scene_id,pair_id,question_id
scene01/v1.jpg,scene01/v2.jpg,scene01,p01,limpidity
scene02/v1.jpg,scene02/v2.jpg,scene02,p02,limpidity
```

完整的单场景六版本示例（15个原始图对，另加2个隐藏重复、2个左右互换，共19题）：

```bash
cd /path/to/EasyLabel
python annotation_mvp/tools/make_v0_example.py --out /path/to/EasyLabel/demo
python -m annotation_mvp.measurement_app \
  --input /path/to/EasyLabel/demo/input \
  --results /path/to/EasyLabel/demo/comparison-results \
  --pairs /path/to/EasyLabel/demo/pairs.csv \
  --question "哪张更通透？" --scene-gap 0 --seed 20261008 --port 8870 --open
```

若示例目录已经生成，跳过 make_v0_example.py 命令，直接运行启动命令；生成工具不会覆盖已有目录。

生成的是合成演示图，仅验证工作流程，不能当作研究证据。正式试标替换为研究者准备的图片与清单。新清单模式默认场景间隔为3（两次同场景之间至少3道其他题），替代旧的同图5题规则。单场景必须显式指定 `--scene-gap 0`。隐藏重复和互换各取原始清单的10%向上取整，且来源图对互不重叠；两次呈现间至少 `min(100, floor(含质控的总题数/3))` 道其他题。40对产生48题，300对产生360题。约束不会自动放宽。旧正式题库继续调用原来的 schedule 分支，40个隐藏重复、至少100个互换与原有间隔/随机序列不变。

数字键 1/2/3 = 左/平局/右，空格也可选平局；方向键浏览，N 写备注，Enter 保存输入，Backspace 撤销，Esc 退出输入或暂停。保留原休息提醒。不显示文件名、场景编号、历史答案或分数。`--question` 是整个实验的问题文本，清单 `question_id` 是其记录标识；二者冻结后不能在原结果目录修改。

作答完关闭该服务，然后按场景分析：

```bash
python -m annotation_mvp.measurement_app \
  --input /path/to/EasyLabel/demo/input \
  --results /path/to/EasyLabel/demo/comparison-results --analyze
```

也可使用网页的分析按钮。导入清单实验不运行粗分、make_graph 或自动第三轮。分析按 `(question_id, scene_id)` 分组拟合 Davidson 与平局各计半胜的 BT，绝不连接不同场景为一个全局量表。`scene_scores.csv` 记录分数、标准误、模型与拟合状态；不可辨识/未收敛时留空并解释，不能保证15次判断一定能估计出有限标准误。`analysis.json`/`report.md` 记录分场景隐藏重复自一致率、左右互换一致率、连通性和两种模型秩相关。没有重复样本的场景不伪造一致率。`bt_scores.csv` 是分场景的 Davidson 结果；`pairs_human.csv` 排除平局，排除数量写入报告；`ties.csv` 保留平局记录。

每个场景的分数各自满足 $\sum_i s_i=0$，相当于每个场景各用一把独立归零的尺子；跨场景的数值大小没有共同参照。Davidson 显式建模平局；这里的 BT 对照把平局各计半胜。标准误是拟合模型内的参数不确定性，不等于标注者可靠性，也不是通透度构念已获验证的证据。拟合沿用原流程使用全部有效判断（含质控呈现），标准误未按重复图对聚类校正。自一致率与互换一致率另行统计：两次选中同一图片或两次均平局才算严格一致；互换后不能直接比较“左／右”按钮值。

## 原因归属

来源须为已完成的 v0 清单比较实验（有冻结的 scene_id）。读取追加日志中的最终有效答案，选取所有非平局原始图对；追加的隐藏重复、互换呈现不再次导入。原题恰好属于互换子集不影响其入选。新实验重新随机左右，不追加质控重复，不展示来源胜者，并核验来源图像哈希。

```bash
cd /path/to/EasyLabel
cp -R /path/to/EasyLabel/demo/input /path/to/EasyLabel/demo/attribution-input
python -m annotation_mvp.measurement_app \
  --input /path/to/EasyLabel/demo/attribution-input \
  --results /path/to/EasyLabel/demo/attribution-results \
  --attribution --source-results /path/to/EasyLabel/demo/comparison-results \
  --scene-gap 0 --seed 20261009 --port 8771 --open
```

固定问题为“这两张在通透上的差别主要来自哪里？”，不问哪张更好。键盘1至5依次为影调、色彩、两者都有、其他、看不出差别；选其他后填写文字，Enter提交。其他文字必须填写，后端也校验；备注仍可选。选项记录值依次为 `tone/color/both/other/no_difference`。结果写入 `events.jsonl`，投影导出 `attributions.csv`（包含题号、两图、scene_id、question_id、question_text、experiment_mode、选择、attribution_text、反应时间、时间戳、会话和备注），不把归属选项伪装为胜负。可暂停、恢复、撤销。完成后可按同样 `--analyze` 命令导出归属计数，不拟合通透标度。

完成页可直接下载 `attributions.csv`；后来通过“保存备注”修改的文字也会纳入该导出，历史答案和备注修改仍保留在追加日志中。如果来源实验所有原题均为平局，则没有符合条件的归属题，系统明确报错。

## 重测

从已完成的 v0 清单实验抽原始图对；保留原题中的平局，剔除追加重复呈现。抽取数量为 `ceil(原始题数 × fraction)`，默认一半，使用固定种子重新随机左右。输出不能覆盖已有清单或写回来源结果目录。下例产生8个原始重测对，导入时再次随机左右并生成自己的质控子集。

```bash
cd /path/to/EasyLabel
python annotation_mvp/tools/make_retest_list.py \
  --results /path/to/EasyLabel/demo/comparison-results \
  --fraction 0.5 --seed 20261011 \
  --out /path/to/EasyLabel/demo/retest.csv
cp -R /path/to/EasyLabel/demo/input /path/to/EasyLabel/demo/retest-input
# 三天后手动启动，不会由脚本自动等待或调度。
python -m annotation_mvp.measurement_app \
  --input /path/to/EasyLabel/demo/retest-input \
  --results /path/to/EasyLabel/demo/retest-results \
  --pairs /path/to/EasyLabel/demo/retest.csv \
  --question "哪张更通透？" --scene-gap 0 --seed 20261011 --port 8772 --open
```

---

# 通透标注台 · 本地 MVP

用于摄影图片的“通透 / 不通透”二分类标注，附加拿不准、跳过、独立标注者、隐藏重复题、修订历史与数据导出。Python 3.9+，无第三方依赖。仅运行于本机 127.0.0.1，不向网络上传图片。

## 启动

单图分类请在终端运行（根目录双击脚本启动的是成对比较合成演示）：

```bash
cd /path/to/EasyLabel
python -m annotation_mvp.app --open
```

浏览器地址为 http://127.0.0.1:8765 。保持终端运行，Ctrl+C 停止。标签逐条写入 SQLite，重启后输入同一代号继续。若端口已被当前标注台占用，直接打开已有地址；另开项目使用不同端口。

## 第一次使用

1. 准备 JPG/JPEG、PNG 或 WebP 图片文件夹；RAW、TIFF、HEIC 请事先导出适合浏览器显示的版本。工具不改变色彩、锐度或图像内容。应在正式导入前人工检查图片能否正常显示，以及色彩管理和显示条件是否适合研究。
2. 图片准备页输入完整路径与来源。Finder 选中文件夹后按 Option+Command+C 可复制路径。工具递归读取并按 SHA-256 完全去重，保存导入时的字节副本。原文件不被修改。
3. 若每个文件都是独立照片，选择每张独立成组。若同一母图有多个版本，将每个母图的版本放入一个独立子文件夹并选择子文件夹分组，或提供 CSV。近重复检测和人工视觉审核尚未实现，分组需要研究者确认。
4. 下载图片清单，检查 group_id 和 split；确认后锁定题库。锁定不可在 UI 中撤回，可使用新目录重建题库。正式导入前可先用临时目录试用。
5. 每位标注者输入独立代号。标注页不显示原文件名、来源、划分、重复标识、其他人的标签或模型预测。代号是本机进度标识，不是认证账户；适用于可信研究团队轮流使用同一台电脑。
6. 快捷键：1 通透，2 不通透，U 拿不准，S 跳过，Backspace 撤销上一条。拿不准需与 1/2 一起提交；跳过的标签为空。点击后待保存成功才进入下一张。双击图片可全屏；退出全屏后按钮可见。
7. 下载数据；完整备份应复制整个数据目录。页面可单独下载 SQLite 一致性快照，但快照不含图片。

## CSV 导入

见 `manifest.example.csv`，示例中的路径必须替换为真实路径。必须有 `path,group_id`，可选 `source,split`。相对图片路径相对于 CSV 所在目录。明确的 group_id 应包含来源命名空间，例如 `fivek:0001`，防止不同来源 ID 碰撞。

`split` 为 `train`、`validation` 或 `test`。空值按母图组 SHA-256 确定性分配，期望 60%/15%/25%；小样本比例不是精确配额。需要严格的 600/200 或跨来源测试设计时，先在 CSV 中指定。组内 split 冲突会拒绝整批数据库导入。完全重复的图片复用已有划分；重复文件引入的别名组也保存固定划分。导入失败可能留下未登记的图片副本，不会形成标注题或覆盖原图。

## 隐藏重复与顺序

每个标注者的顺序独立生成并持久保存。至少 20 张图片时添加约 10% 隐藏重复，两个相同题之间至少隔 10 道题。小于 20 张不自动添加重复。隐藏重复保留独立答案与 repeat_of 关联。它仅用于后续稳定性分析，汇总时不增加该标注者的票数；目前未自动计算一致性系数或安排跨日重测。

## 导出约定

- `manifest.csv`：图片 id 即 SHA-256；保留原路径、快照文件名、母图组、来源、划分。
- `labels.csv`：每道题当前状态及原始独立判断，包括 pending/answered/skipped；同一代号的一道题不会因重试增加标签。
- `events.csv`：每次提交和撤销，保留旧值。撤销后重标不会抹除历史。
- `training.csv`：每张图片一行，保留全部划分和无合格标签图片。只有非重复、已回答且未勾选拿不准的原始题计入多数票；平票/无票 label 为空。train/validation 至少 1 有效票，test 至少 3 有效票；满足数量且非平票时 eligible=1。agreement 是有效票中的多数比例，不是经过验证的信度指标。

**训练读取 `split=train AND eligible=1`；调参读取 validation；最终评估读取 test。不要把整份 training.csv 直接用于训练。** 不确定票、跳过和票数在清单中保留，未被当成负类。测试集上的有效标签覆盖率及分歧应另行报告，不能把排除后的准确率视为全部图库的表现。此处的聚合规则是第一版操作约定，不是通透构念验证。

CSV 使用 UTF-8 BOM，兼容常见表格软件；以 =、+、-、@ 等开头的文字加单引号避免被当作公式。原始未转义数据保留在数据库。

## 下一轮与训练

### 不通透原因（2026-09-07 更新）

选择“不通透”后出现原因表单，填写后才保存标签并进入下一张。可以只填写自由文字，或多选描述并补充文字；“其他”必须填写说明，“说不清具体原因”与其他选项互斥。通透和跳过不要求原因。原因框中的 1/2/S/Backspace 不会触发分类快捷键，Escape 可返回重新判断。

新增原始导出字段：`negative_reasons`（JSON 编码的原因代码数组）、`negative_note`（文字）、`reason_status`（provided/missing/空）。代码含义定义在 app.py 的 REASONS，版本为 v1。原因会写入 events 历史；撤销会清除当前原因但保留旧历史。训练汇总的投票算法不变，原因通过 image_id 与原始标注连接，不混成多数票原因。

升级前的负类答案保留原标签、耗时与时间，标记 missing。完成未答题后会自动进入“补充原因”流程；补充记录使用 reason_added 事件，不新增投票或改写旧标签。只选“说不清具体原因”表示解释缺失被明确报告，不代表已经找到了原因。选项是未验证的描述提示，不能当作已确认的通透维度。新增选项可能影响后续判断，跨协议前后比较应利用事件中的 reason_schema 区分。

设计参考：[Pew Research 问卷题目与选项顺序](https://www.pewresearch.org/writing-survey-questions/)。自由文字置于选项前，选项只在负类决定之后出现，但未验证消除了提示效应。

题库锁定后保持不变，下一轮可运行：

```bash
python -m annotation_mvp.app --data data/round-02 --port 8766 --open
```

跨轮训练合并前必须检查母图和重复图片的划分一致性；本工具目前只强制单个数据目录内的一致性。尚未实现模型训练、主动选图、自动修图、网络多人账户或跨轮自动合并。当前任务是可使用的标注基础设施。

## 验证

```bash
python -m unittest annotation_mvp.test_app -v
node --check annotation_mvp/static/app.js
```

测试使用临时合成像素文件与独立临时数据库，覆盖同母图划分、重复别名、事务拒绝、图像快照、隐藏重复间距、独立标注、续标、幂等重试、撤销历史、聚合排除、HTTP 图片/导出/备份及请求令牌保护。测试不创建真实人类标签。

实现依据：[Python SQLite backup](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup)、[Python HTTP server](https://docs.python.org/3/library/http.server.html)。该服务仅供本地使用，不作为公网生产服务。

## 标注备注

每张图下方提供选填备注（最多 5000 字），通透、不通透和跳过均可记录观察、疑问或修图想法。备注与不通透原因独立，随答案提交写入 SQLite 和事件历史，并在原始 labels.csv 的 comment 列导出；不改变标签聚合。

未提交的备注暂存在当前浏览器，刷新后用同一代号进入可恢复；这不是数据库备份。撤销上一条后会保留其备注以便修改。

## 无法判断

主按钮“3 无法判断”记录独立结果 status=undecidable，label 为空；可选填最多 2000 字原因，备注一起保存。它不同于有倾向但信心不足的“拿不准”，也不同于不作结论的“跳过”。原始导出包含 undecidable_reason，训练导出包含 n_undecidable；无法判断不计入二分类有效票。历史 skipped 不自动转换。支持撤销重判，旧原因保存在历史中，撤销后的当前原因清空，备注保留。
