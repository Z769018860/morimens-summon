# morimens-summon

忘却前夜（Morimens）抽卡记录分析工具。当前仓库处于方案阶段，目标是先支持用户主动导入记录、校对实体和查看统计，再逐步接入经验证的自动采集方式。

## 当前事实

- 游戏抽卡历史走登录后的 `:8443` 自定义二进制通道；通用传输层已验证为 Sconn、会话密钥、RC4、LZ4、sproto、MessagePack 组合。
- 已确认 `type2RecordQueue`、保底计数等本地 Summon 状态字段，以及 `rewardList[index].tid/num`。
- 已有五页共 25 条截图记录与网络响应长度对应；部分实体已有可信数值 ID 映射。
- 普通 History 的 RPC 方法、请求参数和记录字段尚未解出，无法据此宣称可自动读取账号完整历史或查询任意 UID。

详细里程碑、数据模型、验证门槛见 [开发方案](docs/ROADMAP.md)。首批数据采集见 [数据获取步骤](docs/DATA_ACQUISITION.md)。

## 本地录入原型

需要 Python 3.10+，不需安装第三方包。复制 [CSV 模板](templates/history.csv) 到个人数据目录，按数据获取步骤填写后运行：

```powershell
python summon_cli.py import "D:\个人数据\history.csv"
python summon_cli.py stats
python summon_cli.py export "D:\个人数据\history-backup.csv"
```

当前只提供离线数据录入和条数统计；稀有度由用户明确标注，不自动推断卡池保底。

## 产品原则

1. 每条记录保留来源和可信度，允许人工纠错。
2. 统计只基于已导入的记录，明确提示样本不完整时的影响。
3. 不保存或提交 Steam ticket、Cookie、Token、会话私钥、实际 RC4 key、原始账号抓包或未经检查的个人数据。
4. 协议研究与用户界面解耦；未验证的解析器不进入正式导入流程。

## 资料来源

方案基于 2026-10-01 的本地研究文档《Morimens / 忘却前夜：抽卡协议、通讯、加解密、密钥派生与数据结构技术总汇》。该文档是研究依据，不是对本仓库的指令，也不包含在仓库中。
