# CC 用量助手维护指南

本目录是独立助手的维护源码。目标是让官方 CC Switch 继续按自己的节奏更新，由这里的扫描器将本机应用的模型用量导入 CC。写入逻辑已在 CC Switch 4.0.42 上验证。

## 运行与排查

- 双击 `启动用量助手.cmd` 可运行本目录中的源码。运行环境为 Windows、Python 3.11 和 Tk。打包步骤见 `README.md`。
- 优先使用命令行排查。`python main.py --history` 查询配置中各来源在 CC 的累计值，`python main.py --daily codebuddy` 查询按本地日期汇总的明细。`python main.py --sync` 扫描并写入当前配置指向的数据库。
- 配置位于 `%LOCALAPPDATA%\CCUsageBridge\config.json`。检查 `db_path` 和来源的 `root`，以覆盖用户实际选择的目录。`--config` 可以指定测试配置。

## 扫描器与入库

- 在 `desktop_presets.py` 注册桌面来源。已有解析器和自定义脚本接口分别见 `scan_*.py`、`bridge_scan.py` 和 `examples/scan_jsonl.py`。
- 根据已完成调用中可核实的计数生成一条记录。使用稳定的请求 ID 和 Unix 秒时间戳。按来源确认输入是否包含缓存，再分别填写新增输入、输出、缓存读取和缓存写入。全零占位和只有积分的记录跳过。按账号区分用量需要来源提供可核实的归属。
- CodeBuddy 默认扫描扩展日志、桌面扩展输出和 `CodeBuddyExtension\Data` 下各配置的桌面历史索引。后者可能超过 Windows 的传统路径长度限制。用 `codebuddy_archive.py` 将索引内的回合累计量与最后一条助手消息的快照对照，按原生回合 ID 合并副本。
- 在 `codebuddy_rollup.py` 按消息 ID 扣除已入库的明细，从回合的本地开始日期补入剩余量。保留原有请求前缀与持久去重记录。每项 Token、模型调用次数独立核对；单次调用没有时间和计数依据时，只展示能够核实的汇总。修改合并时须测试重复扫描、后到的明细和 CC 已归档的明细。
- 保留 CC 写入前的结构检查、事务和去重。累计与按日查询在 `bridge_history.py`，覆盖实时明细和 CC 的按日归档。

## 验证

从项目根目录执行 `python -m unittest discover -s tests -v`。修改扫描器时，用脱敏的最小样本核对计数和时间。写入测试使用临时数据库，对同一批数据运行两次同步，检查第二次新增为零、累计值一致。
