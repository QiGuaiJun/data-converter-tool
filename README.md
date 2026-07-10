# 数据导表工具原型

本项目是本地桌面风格的数据导表工具原型，包含数据库连接、导入、导出、作业和定时任务模块。

## 运行方式

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
$env:PORT="8765"
.\.venv\Scripts\python.exe server.py
```

打开：

```text
http://127.0.0.1:8765/
```

## 测试

```powershell
.\.venv\Scripts\python.exe test_import_engine.py
.\.venv\Scripts\python.exe test_import_feature_matrix.py
.\.venv\Scripts\python.exe test_export_engine.py
.\.venv\Scripts\python.exe test_jobs_schedule.py
```

## 注意

`data/`、`uploads/`、`exports/`、`.venv/` 不纳入仓库，避免上传本地数据库、导入源文件、导出结果和虚拟环境。
