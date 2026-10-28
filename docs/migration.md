# 从 TongTou 拆分到 EasyLabel · 2026-10-28

来源：TongTou 工作目录中的 annotation_mvp（包含未入原仓库的代码）。保留 annotation_mvp 模块名以减少协议与测试变更。

迁入：成对比较与分析、原因归属、重测清单、单图分类、候选审核、两套静态页面及现有自动测试。
未迁入：真实图片、题库、标注日志、SQLite 数据库、结果、历史备份；依赖特定研究数据和外部 fade_defade_py 的选图/预处理脚本。

2026-10-28 清理：原 `/Volumes/ResearchData/TongTou/annotation_mvp` 目录已删除。其 1122 个文件逐一计算 SHA-256，再将 `data/`、`batches/`、`analysis/`、`designs/` 移到本机 `/Volumes/ResearchData/EasyLabel-data/`；剩余旧代码与文档压缩为该目录的 `legacy-source-2026-10-28.tar.gz`。迁移清单见本机 `migration-manifest.json`，未上传公开仓库。现行代码只在 EasyLabel 仓库维护。

EasyLabel 不自动读取历史资料。继续单图分类可显式指定 `--data /Volumes/ResearchData/EasyLabel-data/data/round-01`；候选审核须指定迁移后的 `--batch`，并核对 CSV/JSON 中的旧绝对图片路径。原始历史文件为保持记录完整未改写。若要继续旧成对比较实验，应先核实输入和结果目录，再显式指定 `--input` / `--results`；不得假定空结果目录含历史进度。真实旧实验回归测试需要 ANNOTATION_LEGACY_BASELINE，默认跳过；合成测试不等于旧数据迁移验证。

适配：成对比较入口要求显式指定图片和结果；分类入口默认存到当前工作目录 data/round-01；双击脚本使用本仓库 .venv，运行可恢复的合成演示；保留现有记录格式及模型算法。

候选审核台的 DeepSeek 场景建议是可选联网功能，会将所选图片发送到第三方服务。密钥从环境变量读取，不放入仓库。普通成对比较和单图分类在本机运行。
