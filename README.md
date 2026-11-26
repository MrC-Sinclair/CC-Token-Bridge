# CC 用量助手

这是配合官方 CC Switch 使用的独立 Windows 工具。它从本机应用的日志和数据库读取模型调用，按请求 ID 去重，将可核对的 Token 写入 CC 的用量统计。当前写入格式已在 CC Switch 4.0.42 上验证。

## 运行

在本项目目录双击 **启动用量助手.cmd** 即可打开窗口，使用当前文件夹里的源码。源码只依赖 Python 3.11 标准库，窗口界面需要 Tk。也可以在这个目录打开 PowerShell 运行以下命令。

```powershell
python main.py --history             # 查 CC 里各应用已保存的合计和日期范围
python main.py --daily codebuddy     # 查 CodeBuddy 每天的请求数和 Token
python main.py --sync                # 扫描并同步已配置的应用
python main.py                       # 打开窗口，编辑应用或选择自定义脚本
```

`--daily` 接受 `--history` 列出的应用 ID，也接受唯一匹配的显示名称或 CC 应用标识。查询包含明细和按日归档的用量。日期为空表示当前数据库中没有该来源的计量记录。

点窗口右上角的 **×** 可将助手收进 Windows 系统托盘，已启用的自动同步会继续。单击托盘图标可恢复窗口，右键可选择「退出助手」。

命令默认使用当前用户的助手配置，可加 `--config <配置文件路径>` 指定其他配置。源码命令默认向终端输出 JSON，也可加 `--result <文件路径>` 保存为 UTF-8。打包的窗口版 EXE 使用相同参数，命令行运行时请指定结果文件。

## 项目文件

| 文件 | 内容 |
| --- | --- |
| `main.py`、`bridge_ui.py`、`bridge_tray.py` | 命令行、窗口和 Windows 托盘入口 |
| `bridge_app.py`、`desktop_presets.py` | 配置、默认目录、可用的扫描器 |
| `bridge_scan.py` | CodeBuddy IDE 日志和自定义扫描脚本的执行入口 |
| `codebuddy_archive.py`、`codebuddy_rollup.py` | 读取桌面历史回合，将逐条日志尚未覆盖的用量写入 CC 按日汇总 |
| `scan_coding_cn.py` | Qoder CN 会话扫描 |
| `scan_agents_cn.py` | ZCode 和 Marvis 用量表解析 |
| `scan_office_cn.py` | 千问办公完成事件解析 |
| `bridge_db.py`、`bridge_history.py` | CC 写入去重、累计与按日查询 |
| `examples/`、`tests/` | 自定义扫描器模板和回归测试 |

默认配置位置为 `%LOCALAPPDATA%\CCUsageBridge\config.json`。各预设的数据范围、如何添加扫描脚本、CC 中的筛选方式见 [使用说明](使用说明.md)。

## 测试与打包

```powershell
python -m unittest discover -s tests -v
python -m pip install PyInstaller==6.22.3
python -m PyInstaller --onefile --windowed --noupx --icon usage.ico --add-data "usage.ico:." --name CC用量助手 main.py
```

运行测试只需要 Python。打包命令在 `dist` 下生成 EXE。修改源码后可以直接用 `python main.py` 运行新版，桌面快捷方式使用的是另行安装的 EXE。
