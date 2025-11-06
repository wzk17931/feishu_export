# 飞书多维表格导出助手

本项目提供一个可视化工具，用于从飞书多维表格批量导出 Markdown 内容并转换成 Word 文档。默认提供三个模板选项（单品、多品对比、多品科普），用户只需在界面中选择模板、填写筛选条件和输出目录即可完成导出。

## 环境准备

1. 安装 Python 3.9+。
2. 安装依赖库：
   ```bash
   pip install requests python-docx
   ```
   如需使用 Pandoc 提升文档转换质量，请额外安装 `pypandoc` 和系统级的 Pandoc。
3. 将 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`APP_TOKEN` 等配置为你自己的应用凭据，并在运行前修改 `feishu_export.py` 顶部的常量。

## 运行图形界面

在仓库根目录执行：

```bash
python feishu_export.py
```

若要强制使用命令行模式，可附加 `--cli` 或 `--no-gui` 参数。

## 打包为 Windows 可执行文件

1. 安装 PyInstaller：
   ```bash
   pip install pyinstaller
   ```
2. 在项目根目录执行打包命令：
   ```bash
   pyinstaller --onefile --windowed --name feishu_export_gui feishu_export.py
   ```
   * `--windowed` 可以在 Windows 下避免弹出额外的终端窗口。
   * 生成的可执行文件位于 `dist/feishu_export_gui.exe`。
3. 将生成的 exe 分发给最终用户，用户双击即可启动图形界面。

## 命令行模式

仍可通过 `python feishu_export.py --cli` 运行旧的命令行导出流程，相关筛选条件可在脚本顶部的常量中配置。

