# GachaMemes Morimens 工具对照（2026-10-02）

参考仓库：<https://github.com/yoncodes/GachaMemes/tree/main/Morimens>。

该目录的 `MORI.MD` 描述了 Morimens Lua 文件解密及定制 `unluac` 反编译流程；
没有发现 Morimens 网络抓包、TLS 解密或抽卡历史查询实现。
其 `decrypt_file.c` 将 `LuaT0` 自定义头规范化为 Lua 5.4 字节码，
并按加密标记处理脚本内容。

本机 `gamescript.ab` 中三个目标的头部均为
`1b 4c 75 61 54 30 01 01 19 93 0d 0a 1a 0a 04 08 08 78 56`，
与该工具处理的 `LuaT0` / 加密标记格式一致：

| 脚本 | 本机 payload 长度 | 适配线索 |
| --- | ---: | --- |
| `DataInspectorExport.lua` | 52,558 字节 | 可用于核查 GM 导出字段与权限判断 |
| `SummonHistoryContentItem.lua` | 1,498 字节 | 可用于核查历史记录的客户端字段处理 |
| `ShareSummonPanel.lua` | 5,757 字节 | 可用于核查分享页数据来源 |

这些是静态头部匹配，还未验证解密、反编译成功率及上述脚本是否包含目标方法。
下一步应在隔离环境中从源码构建工具，用单个本机 Lua payload 验证输出，
随后只分析与抽卡和 `RequestServerMemoryJson` 有关的脚本。
该项目没有解决 2026-10-02 本机采集到的 TLS 传输层问题，
也不能证明 GM 接口对普通账号开放。
