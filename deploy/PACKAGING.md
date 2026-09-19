# Windows 桌面打包说明

这个项目可以打包成 Windows 桌面程序。桌面版会在本机启动服务，并自动打开浏览器访问本机地址，适合连接公司内网数据库。

## 打包

在**项目根目录**执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
```

（spec 文件在 `deploy\DataConverterTool.spec`，已用 `SPECPATH` 推出仓库根，
在任意目录调用 pyinstaller 都能打对包。）

打包成功后会生成：

```text
dist\DataConverterTool\DataConverterTool.exe
```

使用时请复制整个目录：

```text
dist\DataConverterTool\
```

不要只复制单个 exe，因为同目录里还有运行依赖和页面资源。

## 运行

双击：

```text
DataConverterTool.exe
```

程序会自动启动本机服务，并打开：

```text
http://127.0.0.1:51978/
```

如果 51978 端口被占用，程序会自动尝试 51979 到 52050 之间的空闲端口。

## 数据保存位置

桌面版的数据默认保存在 **exe 目录同级的 `<目录名>-data\`** 下（2026-09 起源码与数据分离）：

```text
DataConverterTool-data\
├─ data\imports.db
├─ uploads\
├─ exports\
└─ logs\
```

其中 `data\imports.db` 保存数据库连接、导入任务、导出任务、作业、定时任务和运行日志。
换电脑时，复制 `DataConverterTool` 与 `DataConverterTool-data` 两个文件夹即可带走配置。

（exe 直接放在盘符根目录这类没有「同级目录」可用的场景，会退回 exe 目录内的
`data\` / `uploads\` / `exports\`，行为与旧版一致。）

## 适用场景

- 需要访问公司内网 MySQL、SQL Server 等数据库。
- 不想配置内网穿透或 VPN。
- 只给自己或公司电脑使用。

## 注意

- 电脑关机后桌面版不会继续运行定时任务。
- 如果需要 24 小时自动执行定时任务，需要把程序放在一直开机的电脑或服务器上。
- 如需多人同时访问，建议使用内网服务器部署，而不是每个人各自运行桌面版。
