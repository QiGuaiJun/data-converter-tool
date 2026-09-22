# 数据导表工具

本地桌面风格的数据导表工具：数据库连接、导入、导出、SQL 查询、作业编排、定时任务；
部署到服务器时可开启**登录与角色权限**（只读 / 可写 / 管理员）并记录审计日志。

## 目录结构

源码与运行数据**分离**：源码目录可以随时删掉重新 clone，数据库和导出产物不会跟着丢，
也不会被误提交进仓库。

```text
data-converter-tool\            源码（git 仓库）
├─ server.py                    唯一后端入口（单文件）
├─ public\                      前端页面（含 login.html 登录页、users.html / audit.html 管理页）
├─ tests\                       测试用例 + conftest.py
├─ acceptance\                  验收复跑脚本与证据
├─ deploy\                      打包与部署说明（含 tencent-cloud-setup.sh 一键部署）
├─ scripts\                     watchdog、备份还原、打包脚本
├─ docs\                        模块设计文档、迁移记录、用户手册（docs/user/*.md）
├─ runtime\                     运行数据（整体 gitignore，删掉会自动重建）
│  ├─ data\                     imports.db、.secret_key、linked_sources\、task_sources\
│  └─ exports\ · uploads\ · logs\
├─ archive\                     过程留档（验收计划、审计产出）
└─ 启动导表工具.bat              双击启动
```

> 运行数据默认落在**项目内** `runtime/`（2026-09-17 迁移后的口径），可用
> `DATA_DIR` / `UPLOADS_DIR` / `EXPORTS_DIR` 覆盖；启动日志第一行会打印实际数据目录。

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
APP_AUTH_ENABLED=true
ADMIN_USER=admin
ADMIN_PASSWORD=一个足够复杂的密码
DC_MASTER_KEY=固定的加密主密钥
```

> ⚠️ `APP_AUTH_ENABLED` 与 `ADMIN_PASSWORD` **必须同时设置**。只设前者时，
> `public_auth_enabled()` 会判定认证未启用，**全部接口对匿名请求放行**。
> 部署完请务必访问一次 `/api/connections`，确认返回 401 而不是 200。
>
> `DC_MASTER_KEY` 用于加密数据库连接口令，**必须固定**：容器重建若丢了它，
> 已存的连接口令会全部解不开，只能重录。生成方式：
> `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`

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

仓库已包含 `Dockerfile` 和 `render.yaml`。在 Render 中新建 Blueprint 或 Web Service，连接 GitHub 仓库后设置
`ADMIN_PASSWORD` 与 `DC_MASTER_KEY` 两个 secret 即可（`APP_AUTH_ENABLED` 等已在 `render.yaml` 里声明）。

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

## 登录与账号（公网部署必读）

服务部署到公网后，用 `APP_AUTH_ENABLED=true` 开启登录。三种角色：

| 角色 | 能力 |
|---|---|
| 只读 viewer | 查看表 / 日志 / 已保存查询，只能执行只读 SQL |
| 可写 operator | 导入、导出、执行写语句、作业与定时任务 |
| 管理员 admin | 全部权限 + 连接增删改 + 账号管理 + 审计日志 |

要点：

- 登录页是**独立页面**（`public/login.html`，不加载全站脚本），未登录访问任何功能页会
  302 到它并带上 `next`，登录成功后跳回原页面。
- 密码用 scrypt 加盐哈希存储；会话 token 只落库 sha256；Cookie 为 `HttpOnly`（HTTPS 下加 `Secure`）。
- 写操作在 Cookie 会话下要求 `X-DC-Request` 校验头且同源，防 CSRF（页面脚本已自动带上）。
- 连续失败 5 次锁定 5 分钟；同 IP 每分钟最多 20 次登录 / 注册。
- **脚本通道保留 HTTP Basic**：`acceptance/` 下的复跑脚本与健康检查继续用
  `ADMIN_USER` / `ADMIN_PASSWORD`，无需改造。
- 审计日志默认保留 90 天（`DC_AUDIT_RETENTION_DAYS` 可调）。

**要让「任何人都能打开网址、注册并直接使用」**，只改配置、不动代码：

```text
APP_AUTH_ENABLED=true
DC_SIGNUP_CODE=            # 留空 → 不要邀请码，任何人都能注册
DC_DEFAULT_ROLE=operator   # 新账号直接可写（不设则默认只读 viewer）
```

> ⚠️ 这三条等于「互联网上任何人都能注册并写入你的库」，所以务必同时保证：
> 连接用的是**只授权单个业务库的专用账号**（不要 root）、定期看审计日志。
> `DC_DEFAULT_ROLE` 填错（拼写错误等）会**回落到 viewer**，不会意外放开权限。

详细操作见用户手册《登录与账号》（工具内「操作手册」页）。

腾讯云轻量服务器的一键部署脚本：`deploy/tencent-cloud-setup.sh`（幂等，含 Nginx 反代、
自签证书、systemd 常驻与认证自检）。

## 注意

运行数据目录（`runtime/`）、`.venv/`、`deployment-data/` 都不纳入仓库，避免上传本地数据库、
源文件、导出结果、日志和虚拟环境。

公网部署会暴露数据库连接和文件导入导出能力（查询模块可执行任意 SQL，包括 DDL），
请务必开启登录、使用强口令，并尽量限制访问来源。
