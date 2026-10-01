# 网页部署与 Windows 版

## 网页

`python build_site.py` 生成 `dist/site/`，可部署在 GitHub Pages 或其他静态站点。仓库的 `.github/workflows/site.yml` 在 `main` 更新时构建并尝试部署到 GitHub Pages。页面使用相对路径，支持放在 `/morimens-summon/` 子目录。

静态页面从 `resources/catalog.public.json` 加载角色名称与卡池时间。点击“导入本地记录”，选择本机版导出的 `morimens-summon-history.json`，抽卡记录和人工卡池标记保存在当前浏览器的本地存储中。该文件不会由页面上传到服务器；导入前站点没有个人数据。游戏通讯采集依赖本机进程、只读内存访问与网络抓包，不能在静态托管页面中运行。

如果私有仓库的 GitHub Pages 尚未启用，需在仓库 Settings → Pages 中将 Build and deployment 的 Source 设为 GitHub Actions。GitHub 账号或组织套餐若不允许私有仓库 Pages，可将 `dist/site/` 部署到支持静态文件的其他托管服务。站点是否实际发布成功以 Actions 的 `Deploy analysis website` 结果和 Pages 地址为准。

## Windows 可下载版

构建环境为 Windows、Python 3.10。运行：

```powershell
python -m pip install -r requirements-build.txt
python build_exe.py
Compress-Archive -Path dist/MorimensSummon -DestinationPath dist/MorimensSummon-Windows.zip
```

压缩包必须整体解压；EXE 依赖同目录的 `web/`、`resources/`、`protocol/` 和 `_internal/`。双击 EXE 会在本机 `127.0.0.1:8765` 启动页面并打开默认浏览器，关闭控制台即停止服务。抽卡数据库位于解压目录的 `data/local/`，建议将整个目录放在可写位置，更新版本时保留该目录。

首次启动尝试从 SKeyDB 获取并缓存 SSR 图片；无法获取时使用随包卡池目录，图片显示占位。SKeyDB 的游戏图片并非其开源代码许可证授权的内容，因此 GitHub 源码与发布压缩包不包含游戏图片。用户本机缓存和账号记录均被 `.gitignore` 排除。协议实现已纳入 `protocol/`，Windows 包无需旁边的研究项目目录。

推送 `v*` 标签会触发 `.github/workflows/windows.yml`：构建 Windows ZIP、上传 Actions 构件，并发布包含该 ZIP 的 GitHub Release。Release 下载需要有访问本私有仓库的 GitHub 账号。
