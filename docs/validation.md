# 独立项目验收 · 2026-10-28

本地环境：macOS arm64，Python 3.12 独立虚拟环境；从 pyproject.toml 全新安装依赖成功。开发模式安装与 wheel 构建均成功。

- unittest 自动发现共 49 项：48 通过，1 跳过。跳过项是依赖 ANNOTATION_LEGACY_BASELINE 的真实旧实验备份回归；本次未对旧实验数据做迁移验收。
- 两套前端 JavaScript 通过 node --check，macOS 双击脚本通过 zsh -n。
- 四个安装命令（easylabel / easylabel-classify / easylabel-review / easylabel-demo）的 --help 可用。
- wheel 解包到临时目录后，从仓库之外导入并启动 HTTP 服务；确认首页、JS、CSS 可读取，合成图片题库可提交，停止再启动后保留日志并进入下一题。
- Git 忽略规则已核对默认数据、结果、演示、虚拟环境和 .env 路径。

合成流程测试不构成人类标注结果、构念有效性证明或第三方 DeepSeek 接口连通性验证。GitHub Actions 已配置 Python 3.10 / 3.12；运行状态以仓库 Actions 为准。

本地测试依赖版本：numpy 2.5.4、scipy 1.18.1、networkx 3.7、Pillow 13.0.0、matplotlib 3.11.2。此处记录验收环境，项目依赖仍由 pyproject.toml 声明。
