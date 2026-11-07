# Mac 构建

普通使用：Python 3.12 + `bash start.command`，不需要先打包。

生成原生 `.app`：在 macOS 14+、原生 Python 3.12 与 Apple Command Line Tools 环境下运行 `bash build_macos.command`；也可在本仓库 Actions 手动运行 Mac 构建工作流。

完整步骤、芯片选择、输出文件和签名说明见 [构建说明](docs/构建说明.md)。
