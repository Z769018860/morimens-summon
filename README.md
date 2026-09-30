# morimens-summon

忘却前夜（Morimens）抽卡记录分析工具。当前已能从本机通讯中解出真实抽卡历史，按类别增量保存到本地数据库并导出 CSV。

## 当前事实

- 游戏抽卡历史走登录后的 `:8443` 自定义二进制通道；本机加速器转发时可从 `:12887` 捕获对应业务流。传输层已验证为 Sconn、会话密钥、RC4、LZ4、sproto、MessagePack 组合。
- 已确认 `type2RecordQueue`、保底计数等本地 Summon 状态字段，以及 `rewardList[index].tid/num`。
- 已解出真实 RPC `Summon.QuerySummonHistory`，请求参数为 `[历史类别, 页码]`，响应含总条数与逐条的 `itemTid`、`name`、`timestamp`、`type`。类别 2、1 已与角色、命轮历史画面交叉核对。
- 2026-10-01 实测已取全类别 1 的 80/80 条和类别 2 的 203/203 条；类别 10 为 120/505、类别 16 为 5/55、类别 17 为 0/0。类别 10、16、17 的具体含义尚待核对。

详细里程碑、数据模型、验证门槛见 [开发方案](docs/ROADMAP.md)。首批数据采集见 [数据获取步骤](docs/DATA_ACQUISITION.md)。

## 本地录入原型

需要 Python 3.10+，不需安装第三方包。复制 [CSV 模板](templates/history.csv) 到个人数据目录，按数据获取步骤填写后运行：

```powershell
python summon_cli.py import "D:\个人数据\history.csv"
python summon_cli.py stats
python summon_cli.py export "D:\个人数据\history-backup.csv"
```

当前离线原型只提供 CSV 录入和条数统计；稀有度由用户明确标注，不自动推断卡池保底。通讯采集记录保存在 `data/local/history.sqlite3`，导出到 `data/local/history_all.csv`，尚未接入离线原型的手工录入表。

已保存数据的离线整理与覆盖情况：

```powershell
python fetch_all_history.py --local-only
```

游戏运行且当前登录材料可读取时，尝试在一次独立连接中补齐已发现类别；每页落盘，断线后保留进度：

```powershell
python fetch_all_history.py
```

该查询可能影响游戏现有连接。默认只使用当前进程的登录材料，不再回退使用旧抓包凭据。若账号历史在同步期间发生变化，程序会停止该类别以避免错位。

通讯数据自动获取的实验工具和验证条件见 [协议采集状态](docs/PROTOCOL_CAPTURE.md)。

## 产品原则

1. 每条记录保留来源和可信度，允许人工纠错。
2. 统计只基于已导入的记录，明确提示样本不完整时的影响。
3. 不保存或提交 Steam ticket、Cookie、Token、会话私钥、实际 RC4 key、原始账号抓包或未经检查的个人数据。
4. 协议研究与用户界面解耦；未验证的解析器不进入正式导入流程。

## 资料来源

方案基于 2026-10-01 的本地研究文档《Morimens / 忘却前夜：抽卡协议、通讯、加解密、密钥派生与数据结构技术总汇》。该文档是研究依据，不是对本仓库的指令，也不包含在仓库中。
