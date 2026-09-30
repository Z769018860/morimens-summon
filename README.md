# morimens-summon

忘却前夜（Morimens）抽卡记录分析工具。当前已能从本机通讯中解出真实抽卡历史，按类别增量保存到本地数据库并导出 CSV。

## 图形化分析

Windows 下双击 [start.cmd](start.cmd)，或运行 `python app.py` 后打开 `http://127.0.0.1:8765/`。页面只监听本机地址，从 `data/local/history.sqlite3` 读取已保存记录；点击“更新数据”可刷新显示。首次准备图标可运行 `python sync_skeydb_assets.py`，它会把 SKeyDB 图鉴图片保存到本地忽略目录。当前已缓存 61 名角色头像和 146 张命轮图片。

- 分类别展示覆盖范围、SSR 次数与每个 SSR 距离同类上次 SSR 的抽数；缺页时不跨缺口推算。
- 展示 SKeyDB 的限时卡池开始、结束时间及持续天数，时间按 UTC+8。历史 RPC 没有返回确切卡池 ID；可给每条 SSR 指定当期卡池，之后才明确标记命中 UP 或“歪”。
- 展示可修改的概率与保底参考值，并根据有效 SSR 间隔给出娱乐性欧非观察。默认规则未经当前服务器游戏内概率详情核实，不能作为官方结论。
- 导出带覆盖信息的结构化 JSON、CSV，以及本地生成的分享 PNG。导出的文件由用户自行保存或上传；PNG 使用的游戏图片请遵守相应素材权利要求。

**采集状态：** 解码、结构化保存和断点续取已验证。2026-10-01 的一次连接取得约 71 页后关闭；再次连接受到游戏进程重启影响。因此自动取全仍不能称为稳定。已完整采集类别 1、2；类别 10、16 仍有缺页。页面会明确显示这些范围。

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

旧版 `summon_cli.py` 只提供手工 CSV 录入和条数统计。图形界面直接读取通讯采集记录，不依赖该手工录入表。通讯数据保存在 `data/local/history.sqlite3`，也可导出到 `data/local/history_all.csv`。

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
