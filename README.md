# 数据导表工具原型

这是一个本地桌面风格的数据导表工具原型，包含数据库连接、导入、导出、作业和定时任务模块。

## 本地运行

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
$env:PORT="51978"
.\.venv\Scripts\python.exe server.py
```

打开：

```text
http://127.0.0.1:51978/
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

## Render 部署

仓库已包含 `Dockerfile` 和 `render.yaml`。在 Render 中新建 Blueprint 或 Web Service，连接 GitHub 仓库后设置 `ADMIN_PASSWORD` 即可。

## 测试

```powershell
.\.venv\Scripts\python.exe test_import_engine.py
.\.venv\Scripts\python.exe test_import_feature_matrix.py
.\.venv\Scripts\python.exe test_export_engine.py
.\.venv\Scripts\python.exe test_jobs_schedule.py
```

## 注意

`data/`、`uploads/`、`exports/`、`logs/`、`.venv/` 不纳入仓库，避免上传本地数据库、源文件、导出结果、日志和虚拟环境。

公网部署会暴露数据库连接和文件导入导出能力，请务必使用强密码，并尽量限制访问来源。
