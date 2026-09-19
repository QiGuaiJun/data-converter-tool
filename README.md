# 数据导表工具

本地桌面风格的数据导表工具：数据库连接、导入、导出、作业编排、定时任务。

## 目录结构

源码与运行数据**分离**：源码目录可以随时删掉重新 clone，数据库和导出产物不会跟着丢，
也不会被误提交进仓库。

```text
data-converter-tool\            源码（git 仓库）
├─ server.py                    唯一后端入口（单文件）
├─ public\                      前端页面
├─ tests\                       测试用例 + conftest.py
├─ deploy\                      打包与部署说明（spec、requirements-packaging、PACKAGING.md）
├─ scripts\                     watchdog、备份还原、打包脚本
├─ docs\                        模块设计文档、迁移记录
├─ archive\                     过程留档（验收计划、审计产出）
└─ 启动导表工具.bat              双击启动

data-converter-tool-data\       运行数据（同级目录，不进 git）
├─ data\                        imports.db、.secret_key、linked_sources\、task_sources\
├─ exports\ · uploads\ · logs\
```

## 本地运行

最简单：双击 `启动导表工具.bat`（自动开浏览器，关掉窗口即停服务）。

手动启动：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
$env:PORT="51978"
.\.venv\Scripts\python.exe server.py
```

打开：

```text
http://127.0.0.1:51978/
```

启动第一屏会打印实际使用的数据目录，排查"数据为什么没更新"时先看这里。

### 让服务常驻（定时任务依赖它）

定时任务跑在服务进程里，**服务不在跑 = 定时任务不会触发**。两种常驻方式：

```text
双击 scripts\DataToolWatchdog.vbs           每 10 秒探测 51978，服务挂了自动拉起（常驻）
把该 vbs 复制到启动文件夹                     开机自动常驻（路径见文件内 PROJECT_ROOT）
```

停掉 watchdog：在数据目录建一个空文件 `data\watchdog.stop`，或任务管理器结束 `pythonw.exe`。

### 运行数据落点

默认在项目内的 `runtime\` 下（`runtime\data`、`runtime\uploads`、`runtime\exports`）。可以用环境变量覆盖：

```text
DATA_DIR=D:\somewhere\data
UPLOADS_DIR=D:\somewhere\uploads
EXPORTS_DIR=D:\somewhere\exports
```

## 公网部署

推荐使用支持 Docker 的云平台部署，例如 Render、Railway、阿里云、腾讯云或任意 VPS。

公网部署时必须设置：

```text
HOST=0.0.0.0
PORT=平台自动提供
ADMIN_USER=admin
ADMIN_PASSWORD=一个足够复杂的密码
```

如果平台支持持久化磁盘，建议设置：

```text
DATA_DIR=/app/persistent/data
UPLOADS_DIR=/app/persistent/uploads
EXPORTS_DIR=/app/persistent/exports
```

否则云端重启后，本地 SQLite 配置、上传文件和导出文件可能丢失。

补充：没设这三个环境变量时，落点是 `ROOT/runtime/data`、`ROOT/runtime/uploads`、`ROOT/runtime/exports`
（容器里即 `/app/runtime/...`）。见 `docs\迁移记录-20260917.md`。

## Render 部署

仓库已包含 `Dockerfile` 和 `render.yaml`。在 Render 中新建 Blueprint 或 Web Service，连接 GitHub 仓库后设置 `ADMIN_PASSWORD` 即可。

## 数据备份与迁移到公网环境

软件自身的配置数据保存在运行数据目录的 `data/imports.db`，包含已保存的数据库连接、
导入/导出/作业/定时任务配置和运行日志。

在本地生成只包含配置数据库的备份包：

```powershell
.\.venv\Scripts\python.exe scripts\create_data_backup.py
```

如确实需要把上传源文件和导出结果也一起带过去：

```powershell
.\.venv\Scripts\python.exe scripts\create_data_backup.py --include-uploads --include-exports
```

生成的文件会放在 `deployment-data/`，例如：

```text
deployment-data/data-converter-backup-20260712-120000.zip
```

在云服务器上恢复：

```bash
python scripts/restore_data_backup.py /path/to/data-converter-backup-xxxx.zip
```

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q                    # 全量
.\.venv\Scripts\python.exe -m pytest tests\test_p1_file_guard.py -q   # 单个文件
```

用例把数据目录指向 `%TEMP%`，不会碰真实运行数据；目标库用临时 sqlite，
不连接任何外部数据库。

## 注意

运行数据目录、`.venv/`、`deployment-data/` 都不纳入仓库，避免上传本地数据库、
源文件、导出结果、日志和虚拟环境。

公网部署会暴露数据库连接和文件导入导出能力，请务必使用强密码，并尽量限制访问来源。
