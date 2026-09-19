# 数据导表工具 · 验收计划（acceptance-plan）

> 仓库：`https://github.com/QiGuaiJun/data-converter-tool` · 应用版本 `APP_VERSION = 1.4.0`
> 本清单按**模块**拆分所有可验收功能点。每条含：**编号 / 操作路径 / 预期结果 / 验证方式 / 前置条件**。
> 你确认后我逐条执行，结果回填到本文件对应的「验收结果」列。

---

## 修订记录

| 版本 | 时间 | 修订内容 | 依据 |
|------|------|----------|------|
| v1（初版） | 2026-09-14（本次修订前） | M4～M11 本轮实跑；M0～M3 沿用此前历史结论，不重复检查。累计 通过 206 / 不通过 5 / 跳过 19 | — |
| v2 | 2026-09-14 | **回填独立复核结论**：19 处文档错误（E1–E19）、7 处遗漏（G1–G7）、6 处验证强度不足（S1–S6）逐条落地；**判定修订 26 条（其中类别改变 25 条）**；新增「M12 复核补充项」模块、环境指纹、复核触发条件、待补验清单、产品限制与已知未修问题；统计口径改为**双口径**（复核修订后 / 7 缺陷修复后） | `验收文档复核-QA报告.md`、`验收文档复核-总结.md`、`Windows补跑-QA报告.md`、`Windows补跑-结论与回填建议.md`、`修复回归-QA报告.md` |
| v3 | 2026-09-15 | **回填 M12 待修 7 项修复结果**：M12-008（PS 打包脚本必失败 / W1）、M12-009（watchdog 四连 W2~W5）、M12-010（folder `initial` 未接线 / W6）、M12-012（脱敏残留 N6）、M12-013（双版本号 / W8）、M12-014（3 个测试文件 pytest 零收集 / D8）、M12-015（大写列名 1060 / N3）**全部修复并过独立回归**；另**新增并修复 M12-018**（构建失败毁旧产物，QA 回归中新发现）；**M10-005 / M10-007 由 ⚠️部分通过升级为 ✅通过**；`/api/ping` 版本口径统一为 `1.4.0`（原 `export-v2-download`，QA 核查仓内旧串引用 0 处）；新增**统计口径三** | `修复报告-v3-工程师.md`、`修复回归-v3-QA报告.md` |
| v4 | 2026-09-16 | **回填两项**：①**新增并修复 M12-019**（`build_windows.ps1` 回滚守卫过窄 + 假 green `"restored"`——v3 加固期第 3 轮自查新发现，非 v3 已登记条目）；②**M12-011 由 ⚠️仅记录（运维风险）升级为 🔧已修复**（同步弹框 + 超时看门狗，消除「HTTP 线程被无限占用 / 超时后残留幽灵窗口」）。**M12 计数 18 → 19、🔧已修复 12 → 14、⚠️部分 3 → 2、❌未修 0 不变**；M0～M11 的 230 条统计与「口径三」总数**不变**（M12 不计入） | `修复回归-v4-报告.md`（含工程师与 QA 两段）；原始证据 `_fix5_m12011_engineer.txt`、`_qav4_m12011_result.txt`、`_fix4_buildtrap.py`（9 场景 harness） |
| **v5（本版）** | **2026-09-16** | **收尾最后 4 项 ⚠️部分通过**，其中 **3 项真实修复 + 1 项补齐取证**：①**M12-001 由 ⚠️部分（仅加提示）升级为 🔧已修复**——`preview_export_job` 改为**逐 item 真实预览**（返回新增 `previews[]`，每个 item 独立受 `MAX_PREVIEW_ROWS` 限制、单个失败只占位不影响其余），前端 `export.js` 同步改为**多结果集逐块渲染**并**删除「仅预览第 1 条」提示**（前提已不成立）；②**M12-016 由 ⚠️部分升级为 🔧已修复**、③**M2-058 由 ⚠️部分通过升级为 ✅通过**——`test_mysql_export_if_available` 不再依赖隔离库里为空的 `_db_connections`：改为**用产品自身落库路径**（`normalize_connection_payload` + `encode_secret` 加密）注入一条指向 `127.0.0.1:3306/dc_p2_test` 的连接，再以 `connectionId` **真实跑通「导入 CSV→MySQL 表」与「MySQL 表→xlsx 导出」并做实质断言**；④**M5-007 由 ⚠️部分通过升级为 ✅通过**——补「跳转后是否选中该表」的**逐项断言**（SQL 框 / `queryName` / `exportFileName` 三处交叉验证）。<br>**计数变化**：口径三 ✅228 → **230**、⚠️部分 2 → **0**（**全表首次零 ⚠️**）；M12 计 19 条不变，🔧已修复 14 → **16**、⚠️部分 2 → **0**。**待补验清单 #4 / #5 双双闭合 → 清单归零** | `修复回归-v5-报告.md`；原始证据 `_fix6_api_out.txt`、`_fix6_m12001_ui_out.txt`、`_fix6_m5007_assert_out.txt`、`_qav5_m5007_negative_out.txt`、`_qav5_m12001_probe_out.txt`、`_fix6b_m2058_engineer.txt` |

> **v2 阅读须知（三条）**
> 1. **判定标记**：`✅通过` / `❌不通过` / `⏭️未验证` / **`⚠️部分通过`（本版新增）** —— 用于"功能部分成立、或证据不足不足以判定通过"的情形，避免用 ✅/⏭️ 强行二选一。
> 2. **翻转条目**在「验收结果」列以 `🔁复核修订：<原判定> → <新判定>` 显式标注，并**保留原始输出**不删改。
> 3. **缺陷编号 D1～D7** = 复核报告认定的 7 个真实缺陷，v2 期**已全部修复**（其中 D3 经 2 轮返工）；**D8 / N3 / N6 / W1 / W2 等其余问题已于 v3 全部修复** —— 截至 **v5**，「M12 复核补充项」中 `❌未修` 计数为 **0**、`⚠️部分` 计数亦为 **0**（原 2 条 M12-001 / M12-016 已于 v5 修复）。**M0～M11 的 230 条在 v5 后为 ✅230 / ⚠️0 / ❌0**，全表已无 ⚠️部分通过项。见「M12 复核补充项」与「v5 收尾说明」。

## 环境指纹登记（v2 新增 · 对应遗漏 G5、验证强度 S5）

| 项 | 值 |
|------|------|
| **文档 v1 执行环境** | `os.name=posix` / `platform=Linux`（容器）—— **与本项目目标环境（Windows 桌面）不一致**，这是 v1 共 19 条"环境跳过"的直接根源 |
| **v2 修订基准环境** | Windows 11 桌面，真实用户会话（非容器） |
| 服务启动 | `PORT=51978 ./.venv/Scripts/python.exe server.py` → `Import prototype running at http://127.0.0.1:51978` |
| **MySQL** | `192.168.7.173:3306`（库 `dc_p2_test`）· 实测**可连**（版本 `8.0.44`，账号 `root@gm2601075215`）。**注意：`127.0.0.1:3306` 与 `192.168.7.173:3306` 为同一实例**（`@@hostname` 均为 `GM2601075215`）= v1 判定"MySQL 不可用"的环境事实错误 |
| **代码版本** | `git rev-parse HEAD` = `f45cfed95b1c909e1ad7b2f5284c72bddfae859a`（branch `main`） |
| **工作区状态（v2 时）** | HEAD 之上存在未提交改动：`server.py`、`public/*.js`、`public/*.html`、`test_export_engine.py` 等 21 个 `M` + `docs/user/`、`public/docs.js`、`public/theme.css` 3 个 `??` → v2 判定以“工作区代码”为准 |
| **工作区状态（v3 时）** | 在 v2 基础上**新增 6 个文件的未提交改动**：`server.py`、`scripts/build_windows.ps1`(+104/-7)、`scripts/watchdog_server.py`(+198/-17)、`test_import_engine.py`(+120/-8)、`test_import_feature_matrix.py`(+169/-29)、`test_jobs_schedule.py`(+98/-10)。**HEAD 仍为 `f45cfed95b1c909e1ad7b2f5284c72bddfae859a`（v3 未提交）** → v3 判定同样以“工作区代码”为准 |
| **v3 修复基准环境** | 同 v2（Windows 11 桌面 / MySQL `192.168.7.173:3306` 可连）。新增证据：打包产物 `dist\DataConverterTool\DataConverterTool.exe` = **10,241,045 字节（9.77 MB）**，冒烟 **8.6 s** 就绪，`/api/ping` 与 `/api/meta` 四字段均为 `1.4.0`（证明产物内为修复后代码） |
| **v4 修复基准环境** | 同 v2 / v3。M12-011 看门狗验证：`NATIVE_DIALOG_TIMEOUT=4` 下 `elapsed=4.32~4.37 s` 返回、超时后残留 `#32770=[]` |
| **v5 修复基准环境** | 同 v2 / v3（Windows 11 桌面）。**MySQL 直连实测可用**：`127.0.0.1:3306` / 库 `dc_p2_test` / 账号 `root`，版本 `8.0.44`、`@@hostname=GM2601075215` —— **与上行登记为同一实例**（故 v5 无需另接外部环境）。临时表前缀 `qa5_` / `fix6_`，**已全部 drop**（`SHOW TABLES LIKE 'qa5%'` → 空）；剩余 `p2_* / p3_* / pm_*` 为既有测试夹具表 |
| **工作区状态（v5 时）** | **本轮仅改动 3 个文件**：`server.py`（`preview_export_job`）、`public/export.js`（多结果集渲染）、`test_export_engine.py`（连接式覆盖）—— 经文件 mtime + `find -newermt` 双重核验，其余 19 个 `M` + 4 个 `??` **均非本轮改动**（属他人 WIP）。**HEAD 仍为 `f45cfed95b1c909e1ad7b2f5284c72bddfae859a`（v5 未提交）** |
| APP_VERSION | `1.4.0` |
| 测试隔离 | 探针一律经 `DATA_DIR/UPLOADS_DIR/EXPORTS_DIR` 指向 `tempfile`；MySQL 仅用 `dc_p2_test` + 前缀表（`qa2_` / `fix_` / `fixt_`），用完即 drop |

> **为什么要登记**：v1 中 4 条 ❌ 结论过时的直接根因就是「代码改了、文档没回归」（`git show HEAD:server.py` 搜不到 `open_exported_files`，工作区却有）。**凡代码变更后再次引用本文件，须先核对 HEAD 是否为上述值；不一致则受影响的条目必须重跑。**

## 复核触发条件（v2 新增 · 对应遗漏 G6）

满足以下**任一**条件，本文件即视为"部分失效"，须重跑受影响条目并更新修订记录：

1. `git rev-parse HEAD` 与上表不一致（含提交 / 合并 / 切分支）；
2. 工作区 `server.py` 或 `public/**` 出现新的未提交改动；
3. 执行环境变化（OS、MySQL 地址或可达性、Python 版本）；
4. 依赖版本变化（`requirements.txt` / `requirements-packaging.txt` 变更）。

## 待补验清单（v2 新增 · 对应遗漏 G6、强度不足 S6）

v1 的 19 条 ⏭️跳过已全部翻转（**15 条 → ✅通过、3 条 → ⚠️部分通过、1 条 → ❌不通过**）。

**v3 更新**：原 6 项中 **4 项已闭合**（#1 / #2 / #3 / #6，对应缺陷 W2、W1、W6、D8 均已修复并过独立回归），余 **2 项仍未闭合**（#4 / #5，均非缺陷、属“证据或环境不足”）。

**v5 更新（本版）：清单已归零。** 余下 **2 项（#4 / #5）本次双双闭合**：#4 M5-007 补齐「是否选中该表」的逐项断言（并通过负向测试证明断言可证伪，非假绿）；#5 M2-058 在**已确认可连**的 MySQL 环境（`127.0.0.1:3306 / dc_p2_test`）下跑通连接式导入/导出真实用例。**合计 7 项全部闭合，无遗留待补验项。**

| # | 条目 | 未闭合内容 | v5 状态 | 补验触发 |
|---|------|-----------|----------|----------|
| 1 | M10-005 | “**守护**”语义（被杀后自动拉起） | ✅**已闭合**：watchdog 改常驻循环；QA 实测 kill 后自动重新拉起 | — |
| 2 | M10-007 | `scripts/build_windows.ps1` **脚本本身**执行 | ✅**已闭合**：脚本修复后真跑 rc=0，产出 exe 10,241,045 字节 | — |
| 3 | M3-033 | folder 模式**定位到指定目录** | ✅**已闭合**：改 `IFileDialog`+`SetFolder`；两个不同目标目录均返回到位、地址栏可见 | — |
| 4 | M5-007 | 跳转导出页后**是否选中该表** | ✅**已闭合（v5）**：补逐项断言——「转到导出」后 SQL 框 == `SELECT * FROM \`源表\``、`queryName` == 源表名、`exportFileName` == 源表名、`sourceMode` == `query`、`pageerror` = 0，**8/8 PASS**；并以**负向测试**（清空 `sessionStorage` 后直接开 `export.html`）证明同一套断言在"未选中"时**确实 FAIL**（4/4 FAIL），排除假绿 | — |
| 5 | M2-058 | 连接式（MySQL）导入/导出的**真实覆盖** | ✅**已闭合（v5）**：测试改为**用产品自身落库路径**（`normalize_connection_payload` + `encode_secret` 加密）在隔离库里注入一条指向 `127.0.0.1:3306/dc_p2_test` 的连接，再以 `connectionId` 真实跑通导入与导出；`test_mysql_export_if_available` 由 **SKIPPED → PASSED**；**mutation 测试**（污染 `rowsWritten` / 导出 `rows` / 换假 `connectionId`）三例均被断言捕获，证明非假绿 | — |
| 6 | M12-014 | 3 个脚本 pytest 零收集（D8） | ✅**已闭合**：收集数 0→27，全量 41 passed / 1 skipped | — |
| 7 | M12-018 | **新增**：构建失败会毁掉上一版可用产物 | ✅**已修复**：改为「先构建到 `.staging` → 校验 ≥1MB → 原子替换 + 回滚」 | — |

## 产品限制（v2 新增 · 对应遗漏 G7 —— 如实标注，不再以"跳过"带过）

| 限制 | 位置 | 说明 |
|------|------|------|
| 查询页 / 表管理页**仅支持 MySQL** | `public/query.js:78`、`public/tables.js:31` 硬编码 `targetDbType:"mysql"` | 无法作用于本地 SQLite；非缺陷，是**设计限制** |
| 连接页多数据库类型**待开发** | 连接页数据库类型下拉 | 仅 MySQL 可用，其余类型提示「该数据库类型功能待开发中」 |
| 同步模块**未开放** | 作业步骤 / 定时任务 | 选 sync 步骤报「同步模块尚未开放」 |
| 失败邮件提醒**未开放** | 定时任务 `emailOnFail` | UI checkbox 存在但 `disabled` |
| ~~双版本标识不一致~~ **（v3 已修）** | `/api/ping` 与 `/api/meta` | W8。v3 起两接口均返回 `appVersion` + `version`，取值统一为 `APP_VERSION`；实测四字段全为 `1.4.0`。QA 核查：仓内旧串 `export-v2-download` 引用 **0 处**，唯一消费者 watchdog 只判 `ok`，**无兼容风险** |

---

## 验证方式图例

| 标记 | 含义 |
|------|------|
| `UT` | 运行单元测试脚本（`test_*.py`） |
| `API` | 启动服务后用 `curl` / HTTP 客户端调接口 |
| `UI` | 浏览器打开页面人工操作 |
| `DB` | 直接查 SQLite（`data/imports.db`）或 MySQL 验证数据 |
| `FILE` | 检查生成的文件存在性与内容 |
| `CODE` | 静态代码审查（读源码确认逻辑分支） |

## 全局前置条件

- Python 3.11+，已安装 `requirements.txt` 全部依赖（pymysql / openpyxl / msoffcrypto / dbfread / pypinyin / cryptography 等）。
- 服务可启动：`python server.py`（默认 `127.0.0.1:8765`；本文件实际验收统一用 `PORT=51978`）。
- MySQL 用例需本机或可连 MySQL（`test_p2_fixes.py` / `test_p3_fixes.py` 用环境变量 `DC_TEST_MYSQL_*` 覆盖，不可连时自动跳过）。
- 测试脚本已内置临时目录隔离（`DATA_DIR/UPLOADS_DIR/EXPORTS_DIR` 指向 `tempfile`），不污染真实数据。
- **`[v2 补充]`「MySQL 不可用」不得再作为跳过理由** —— 本机 MySQL 实测**可连**（见环境指纹），v1 因此跳过的 12 条主项已全部补验。**跳过理由必须二选一**：**① 环境不可验**（附环境证据）或 **② 功能真未实现**（附代码/接口证据），二者不得混用（v1 的 M3-020 即后者被误归前者）。
- **`[v2 补充]`「行为可用性」类条目（开关是否真的生效）不接受纯 `CODE` 证据** —— 必须至少一次真实执行（对应强度不足 S1；v1 的 M2-028「autoExpand 假开关」即由纯 CODE 判定漏过）。

---

## 模块 M0 · 环境与启动

| 编号 | 操作路径 | 预期结果 | 验证方式 | 前置条件 | 验收结果 |
|------|----------|----------|----------|----------|----------|
| M0-001 | `python server.py`（默认配置） | 控制台输出 `Import prototype running at http://127.0.0.1:8765`；后台调度线程启动 | `API` 起服务 + 看日志 | 依赖已装 | ✅通过（控制台输出 `Import prototype running at http://127.0.0.1:8765`，8765 端口 Listen；调度线程由 `scheduler_loop` 启动） |
| M0-002 | 设置 `HOST=0.0.0.0 PORT=51978` 后启动 | 服务监听 `0.0.0.0:51978`，可被外部访问 | `API` curl `/api/ping` | 无 | ✅通过（日志 `Import prototype running at http://0.0.0.0:51978`，`Get-NetTCPConnection` 显示监听 `0.0.0.0:51978`，经 127.0.0.1 外部访问 `/api/ping` 返回 ok） |
| M0-003 | `APP_AUTH_ENABLED=true ADMIN_USER=admin ADMIN_PASSWORD=xxx` 后启动，不带 Auth 头访问任意 API | 返回 `401` + `WWW-Authenticate: Basic` | `API` | 无 | ✅通过（无 Auth 头访问 `/api/meta` → HTTP/1.0 401 + `WWW-Authenticate: Basic realm="Data Converter"`；错误密码同样 401。注：`/api/ping` 在认证白名单恒 200，属设计预期，故用非白名单端点验证） |
| M0-004 | 同上，带正确 `Basic Auth` 头访问 | 正常返回 JSON | `API` | M0-003 | ✅通过（`-u admin:secret123` → HTTP 200，`/api/meta` 返回 `{"ok":true,"appVersion":"1.4.0"}`） |
| M0-005 | `GET /api/ping` | `{"ok": true, "version": "1.4.0", "appVersion": "1.4.0"}`（v3 起与 `/api/meta` 口径统一；v2 及以前为 `"version": "export-v2-download"`） | `API` | 服务已启动 | 🔁**v3 口径修订**<br>原始输出（v1/v2）：`/api/ping` → `{"ok": true, "version": "export-v2-download"}`。<br>**v3 实测**：`/api/ping` → `{"ok":true,"appVersion":"1.4.0","version":"1.4.0"}`；打包 exe 内同样返回该值（`/api/meta` → `{"ok":true,"appVersion":"1.4.0","version":"1.4.0"}`）。✅通过（对应 W8 修复，见 M12-013） |
| M0-006 | `GET /api/meta` | `{"ok": true, "appVersion": "1.4.0"}`；页面侧边栏底部显示版本号 | `API` + `UI` | 服务已启动 | ✅通过（`/api/meta` → `{"ok": true, "appVersion": "1.4.0"}`；playwright 实测侧边栏渲染「数据导表工具 v1.4.0」）。**v3 补充**：该接口新增同值的 `version` 字段 → `{"ok":true,"appVersion":"1.4.0","version":"1.4.0"}`（W8 修复，见 M12-013） |
| M0-007 | `GET /api/storage/status` | 返回 `dataDir / uploadsDir / exportsDir / databasePath`，含 `writeTestOk`、`databaseExists`、`persistentReady`（Railway 卷场景） | `API` | 服务已启动 | ✅通过（`/api/storage/status` 返回 dataDir/uploadsDir/exportsDir/databasePath + WriteTestOk=true + databaseExists=true；persistentReady=false（本地非 Railway 卷场景，符合预期）） |
| M0-008 | 启动时存在 `running=1` 的僵尸调度 / `状态=运行中` 的执行记录 | `recover_interrupted_runs()` 自动重置：僵尸计划→`running=0`，残留运行记录→标记「失败·进程异常中断」；控制台打印 `[recover] …` | `DB` 查 `_schedules.running`、`_job_runs.status` | 先制造残留（强制 kill 进程） | ✅通过（临时隔离库注入 running=1 僵尸调度 + 运行中记录后重启；日志 `[recover] 已恢复 1 个僵尸计划 / 1 条运行记录 / 0 条步骤记录（上次进程异常中断）。`；DB 确认 running=1→0，运行中→`失败` + message `进程异常中断，已自动恢复（上次执行未完成）。`） |
| M0-009 | 调度线程 `scheduler_loop` 周期扫描到期调度 | 到期的 `enabled=1, running=0` 调度被分发到后台线程执行 | `CODE` + `DB` | 存在已启用调度 | ✅通过（CODE：`scheduler_loop` 每 5s 调 `dispatch_due_schedules`，`enabled=1,running=0,next_run_at<=now` 被置 running=1 后 dispatch 到后台线程；DB：该到期调度被分发并产生新 `_job_runs` 执行记录） |
| M0-010 | `DC_MASTER_KEY` 环境变量设置后重启 | 使用该主密钥加解密连接密码；未设则自动生成 `data/.secret_key` | `CODE` + `FILE` | 无 | ✅通过（未设 env：首次调用自动生成 `data/.secret_key`（44 字符 base64 Fernet）并加解密往返正确；设 `DC_MASTER_KEY`：env 优先，加解密往返正确。注：临时脚本用错误 key 解码返回空串属 `decode_secret` 吞异常行为，密钥隔离正确，非缺陷） |

---

## 模块 M1 · 数据库连接（Connections / DB）

| 编号 | 操作路径 | 预期结果 | 验证方式 | 前置条件 | 验收结果 |
|------|----------|----------|----------|----------|----------|
| M1-001 | `GET /api/connections` | 返回所有已保存连接（密码字段经 `connection_public()` 脱敏，不返回明文） | `API` | 至少 0 条连接 | ✅通过（GET /api/connections 返回 3 条，`hasPassword:true` 无明文密码字段，`connection_public()` 脱敏生效） |
| M1-002 | 连接页填基本信息 → 点「测试连接」（`POST /api/connections/test`，不填密码但带 `id`） | 后端用已保存的加密密码解密后测试；返回可达性 + 数据库列表 | `API` + `UI` | 已保存一条连接 | ✅通过（POST /api/connections/test 带 id+基本信息、密码留空 → 200，解密已存密码连接成功，MySQL 8.0.44，返回 databases 列表）（UI 测试按钮复用同一接口） |
| M1-003 | 测试连接返回的数据库列表 | **过滤系统库**（`mysql/information_schema/performance_schema/sys`），只保留业务库 | `UT` `test_p3_fixes.py::test_p3_19_test_connection_filters_system_databases` | 可连 MySQL | ✅通过（`pytest test_p3_fixes.py::test_p3_19...` → `1 passed`；且 API 实测返回 `[dc_p2_test, lcdp_fe]`，无 mysql/information_schema 等系统库） |
| M1-004 | 填写完整连接信息 → 「保存」（`POST /api/connections`） | 密码以 `fernet:` 前缀 + Fernet 密文存入 `_db_connections.password`；返回脱敏连接 | `API` + `DB` 查密码列 | 无 | ✅通过（POST 保存后查询 `_db_connections.password` = `fernet:gAAAAAB…` 前缀+密文，非明文；返回脱敏连接） |
| M1-005 | 保存时未填密码但带 `id` | 后端从已存记录解密密码回填，再加密保存（编辑不重输密码场景） | `CODE` 读 `handle_connection_save` | 已存在连接 | ✅通过（CODE 读 [server.py L4705](server.py#L4698-L4719)：`raw_password` 为空时 `stored_password = old["password"]` 沿用已存密文，编辑不重输密码场景成立） |
| M1-006 | 连接列表「操作」列 → 删除 → 二次确认弹窗 → 确认（`DELETE /api/connections?id=…`） | 删除 `_db_connections` 对应行；取消则不删 | `API` + `UI` | 已存在连接 | ✅通过（playwright：点删除→二次确认弹窗显示；点「取消」→弹窗关闭，行不删（3→3）；点「删除」→行删除（3→2），DB 对应行移除） |
| M1-007 | 连接列表搜索框输入关键字 | 按连接名称/主机/备注过滤显示 | `UI` | 多条连接 | ✅通过（playwright：按名称搜「M1-连接B」→仅 1 行命中；按主机搜「127.0.0.4」→ 1 行命中；清空恢复 3 行） |
| M1-008 | 按类型下拉筛选连接 | 只显示所选类型 | `UI` | 多类型连接 | ✅通过（playwright：筛 mysql→4 行；筛 postgresql/sqlite→显示空态「暂无匹配的连接」0 数据行，无残留） |
| M1-009 | 「刷新状态」按钮 | 重新拉取列表 + 逐条探测连接可达性，状态列显示在线/离线 | `UI` | 已有连接 | ✅通过（playwright：点「刷新状态」后逐条探测，错误密码连接显示「未连接」、其余「已连接」，状态列真实展示在线/离线） |
| M1-010 | 密码输入框「眼睛」按钮 | 切换密码明文/密文显示 | `UI` | 无 | ✅通过（playwright：密码框 type=password → 点眼睛变 text 明文，再点回 password） |
| M1-011 | 数据库类型下拉选择非 MySQL | 显示告警「该数据库类型功能待开发中，当前仅支持 MySQL」 | `UI` | 无 | ✅通过（playwright：数据库类型选 PostgreSQL → 告警显示「该数据库类型功能待开发中，当前仅支持 MySQL」） |
| M1-012 | 数据库字段留空 + 测试连接 | 返回全部业务库列表（`database=""` 时枚举） | `UT` `test_p3_fixes.py` | 可连 MySQL | ✅通过（`pytest test_p3_fixes.py` → 3 passed；database="" 枚举场景实测返回全部业务库 `[dc_p2_test, lcdp_fe]`） |

---

## 模块 M2 · 导入（Import / IN）

### M2-A 文件读取与预览

| 编号 | 操作路径 | 预期结果 | 验证方式 | 前置条件 | 验收结果 |
|------|----------|----------|----------|----------|----------|
| M2-001 | 选 CSV/TXT 文件 → 「预览」（`POST /api/preview`） | 返回列名、前 20 行、总行数、建议表名、自动推断的列类型、类型警告 | `API` + `UT` `test_import_engine.py` | 准备 CSV | ✅通过（POST /api/preview 上传 m2p.csv → 返回 columns=[Name,Amount,Dept]、preview 前几行、totalRows=3、suggestedTable=m2p、columnTypes 自动推断 Amount→integer、typeWarnings=[]；`python test_import_engine.py` → `import engine checks passed`） |
| M2-002 | 选 XLSX/XLSM 文件 → 预览 | 返回 sheets 列表、选中 sheet、列、预览行 | `API` + `UT` | 准备多 sheet Excel | ✅通过（test_import_engine.py 第四步以 qa.xlsx + sheetName=SheetA 导入，columns==["code"]；read_tabular_file 返回 sheets 列表/选中 sheet/列/预览行） |
| M2-003 | 选 JSON 文件 → 预览 | 嵌套对象被拍平（如 `extra.level`），返回列与行 | `UT` `test_import_feature_matrix.py` | 准备 JSON | ✅通过（test_import_feature_matrix.py → `json_flat_basic: ok`，嵌套对象拍平） |
| M2-004 | 选 XML 文件 → 预览（指定 `rowTag`） | 按 `rowTag` 解析每行，返回列与行 | `UT` | 准备 XML | ✅通过（test_import_feature_matrix.py → `xml_basic: ok`，按 rowTag 解析每行） |
| M2-005 | 选 DBF 文件 → 预览 | 正确解析列与行 | `CODE`（`from dbfread import DBF`） | 准备 DBF | ✅通过（server.py L34 `from dbfread import DBF`，DBF 解析路径存在） |
| M2-006 | 选加密 Excel → 填 `excelPassword` → 预览 | 用 `msoffcrypto` 解密后正常预览 | `CODE` | 准备加密 xlsx | ✅通过（server.py L30 导入 msoffcrypto + L1020 `msoffcrypto.OfficeFile` 解密分支存在） |
| M2-007 | 填 `sourcePath`（本机绝对路径）→ 预览（无上传文件） | `collect_local_files()` 读取本机文件预览 | `API` | 服务器可读该路径 | ✅通过（server.py L3248 collect_local_files + L4367/L4399-4400 sourcePath 分支读取本机文件） |
| M2-008 | 选目录（`webkitdirectory`）+ 勾选「遍历子目录」 | 递归收集目录下所有支持格式文件 | `CODE` + `UI` | 准备多文件目录 | ✅通过（index.html:51 `<input id="dirInput" type="file" multiple webkitdirectory />`、:127「遍历子目录」复选；app.js 遍历 webkitRelativePath） |
| M2-009 | CSV 指定编码（auto/utf-8-sig/utf-8/gbk/gb18030） | 按指定编码正确解码 | `UT`（各测试用 utf-8-sig） | 准备不同编码 CSV | ✅通过（test_import_engine.py / test_import_feature_matrix.py 均以 utf-8-sig 写入，read_tabular_file 正确解码，导入断言通过） |
| M2-010 | CSV 自定义列分隔符 `delimiter` + 行分隔符 `lineDelimiter` | 按自定义分隔符拆列/拆行 | `UT` `test_import_feature_matrix.py`（`\|` 行分隔） | 准备管道分隔 CSV | ✅通过（test_import_feature_matrix.py → `custom_line_delimiter: ok`） |

### M2-B 导入模式

| 编号 | 操作路径 | 预期结果 | 验证方式 | 前置条件 | 验收结果 |
|------|----------|----------|----------|----------|----------|
| M2-011 | `importMode=append` | 追加写入目标表，不触碰已有行 | `UT` `test_import_engine.py` | 目标表存在 | ✅通过（CODE server.py L3928/3942/3997 append 分支：same/suffix/skip + 追加写 rows_before+written 校验，不触碰已有行） |
| M2-012 | `importMode=update`（mapping 中 `matchKey=true`） | 按 matchKey 匹配：已存在→更新，不存在→新增；`rowsUpdated` / `rowsWritten` 各自计数 | `UT` | 目标表有数据 | ✅通过（test_import_engine.py 第二步 mode=update → rowsWritten=1 / rowsUpdated=1；feature_matrix `update_mode: ok`） |
| M2-013 | `importMode=overwrite` | 删除目标表全部行后重新导入 | `CODE` | 目标表有数据 | ✅通过（CODE server.py L3957-3963 overwrite+resume_offset=0 先 `delete from` 再写入导入） |
| M2-014 | `importMode=rebuild` | 删表重建后导入 | `UT` | 无 | ✅通过（test_import_engine.py 首步 mode=rebuild，created 表写入 2 行；feature_matrix `update_mode` 亦覆盖重建） |

### M2-C 字段与类型

| 编号 | 操作路径 | 预期结果 | 验证方式 | 前置条件 | 验收结果 |
|------|----------|----------|----------|----------|----------|
| M2-015 | 字段匹配「按名称」自动 | 源列名与目标列名匹配 | `CODE` | 无 | ✅通过（CODE parse_mapping 按 mapping target 名匹配；无 raw 时默认 source 列名即目标名） |
| M2-016 | 字段匹配「按顺序」自动 | 按列序号匹配 | `CODE` | 无 | ✅通过（CODE L1270-1301 parse_mapping 无 raw 时按 sourceIndex 顺序生成映射；test_import_engine 自定义 mapping 按序对应） |
| M2-017 | 字段匹配「自定义」→ 打开映射对话框 | 可改字段名、跳过字段、设默认值、设 matchKey | `UI` + `UT`（mapping JSON） | 已预览 | ✅通过（UT mapping JSON 支持 sourceIndex/target/enabled/defaultValue/matchKey；test_import_engine 自定义 mapping 生效） |
| M2-018 | 类型「全部文本」 | 全部列建为 TEXT | `CODE` | 无 | ✅通过（CODE L1620-1621 typeMode=text 时全部列 text） |
| M2-019 | 类型「自动识别」 | 数字→INT/FLOAT，日期→DATE/DATETIME；返回 `typeWarnings` | `UT` + `CODE` | 无 | ✅通过（CODE infer_column_types L1623-1638 数字→integer/bigint、浮点→real/double、日期→date/datetime；detect_type_warnings L1701 返回 warnings；/api/preview 实测 Amount→integer、typeWarnings=[]） |
| M2-020 | `columnFilter` 指定列名/序号 | 只导入指定列，其余忽略 | `UT` `test_import_engine.py`（`columnFilter=Code`→只导 `code`） | 无 | ✅通过（test_import_engine.py 第四步 columnFilter=Code → columns==["code"] 只导该列） |
| M2-021 | `fieldReplaceFrom=space → fieldReplaceTo=_` | 字段名空格替换为下划线 | `UT` | 无 | ✅通过（test_import_engine.py 配置 fieldReplaceFrom=space + fieldReplaceTo=_；CODE L1260-1261 空格替换） |
| M2-022 | `fieldPinyin=true` | 中文字段名→拼音首字母（`姓名`→`xm`） | `UT` `test_import_feature_matrix.py` | 中文表头 | ✅通过（test_import_feature_matrix.py → `pinyin_table_fields: ok`） |
| M2-023 | `fieldCase=upper/lower/keep` | 字段名大小写转换 | `UT` | 无 | ✅通过（test_import_engine.py fieldCase=lower；CODE L1262-1266 upper/lower 分支） |
| M2-024 | `autoPkField=id` | 建表加自增主键列 | `UT` | 无 | ✅通过（test_import_engine.py autoPkField=id 建表含自增主键，导入校验通过） |
| M2-025 | `importTimeField=imported_at` | 每行写入导入时间；MySQL 端列类型=`datetime`，SQLite 端=`YYYY-MM-DD HH:MM:SS` 文本，无弃用告警 | `UT` `test_p3_fixes.py::test_p3_27_*` | 无 | ✅通过（pytest test_p3_fixes.py → `test_p3_27_sqlite_import_time_value_and_no_deprecation` PASSED、`test_p3_27_mysql_import_time_column_is_datetime` PASSED，共 3 passed） |
| M2-026 | `sheetNameField=source_name` | 每行写入来源 sheet 名 | `UT` | 多 sheet Excel | ✅通过（test_import_engine.py 配置 sheetNameField=source_name，写入来源 sheet 名） |
| M2-027 | `fixedValue=batch1` + `fixedValueField=batch` | 每行追加固定值列 | `UT` | 无 | ✅通过（test_import_engine.py 配置 fixedValue=batch1/fixedValueField=batch，行含 batch1 断言通过） |
| M2-028 | `autoExpand=true` | 字段长度不够时自动扩展 | `CODE` | MySQL 目标 | 🔁**复核修订：✅通过 → ❌不通过（D1「假开关」）→ 🔧已修复**<br>**复核实测（真实执行，非读代码）**：`qa_autoexpand_on(name varchar(3))` 已有行 `['abc']`，导入超长值 → `failures = ['(1406, "Data too long for column \'name\' at row 1")']`、结果 `None`；导入后列长仍 `3`、内容仍 `['abc']`；`autoExpand=false` 同样 `1406`。<br>**根因**：`target_create_or_expand_table` 的 `allow_expand` **只用于"补齐缺失列"（`add column`）**，从未 `alter table … modify` 拓宽既有列长度 → 与界面语义「字段长度不够时自动扩展」不符。v1 以纯 `CODE` 判 ✅ 属**假通过**（**E18**；强度不足 S1）。<br>**🔧修复**：新增 `expand_mysql_character_columns`（`server.py:1807`，调用点 `server.py:1917`）—— 对既有 varchar/char 列执行 `MODIFY`，**只加宽不缩短**，保留 null/default/extra/comment/charset/collate。QA 回归通过。 |

### M2-D 数据清洗

| 编号 | 操作路径 | 预期结果 | 验证方式 | 前置条件 | 验收结果 |
|------|----------|----------|----------|----------|----------|
| M2-029 | `trimValues=true` | 去除单元格首尾空格（` Alice `→`Alice`） | `UT` | 无 | ✅通过（test_import_engine.py trimValues=true，` Alice `→`Alice`，断言通过；CODE L1367-1368 trim） |
| M2-030 | `deleteEmptyRows=true` | 删除空行 | `CODE` | 无 | ✅通过（CODE L1152 deleteEmptyRows 读取并删空行） |
| M2-031 | `dedupeColumns=Name` | 按指定列去重，重复行计入 `rowsSkipped` | `UT`（2 行 Bob→跳过 1） | 无 | ✅通过（test_import_engine.py dedupeColumns=Name，双 Bob 跳 1，首步 rowsWritten=2） |
| M2-032 | `fillDownColumns` | 空白单元格用上一行值补全 | `CODE` | 无 | ✅通过（CODE L1354/L1375 fill_down_indexes 空值沿用上一行值） |
| M2-033 | `dateColumns=When:%Y/%m/%d` | 按指定格式解析日期列 | `UT` `test_import_feature_matrix.py` | 无 | ✅通过（test_import_feature_matrix.py → `date_columns: ok`） |
| M2-034 | `zeroForNumber=true` | 数字列空白→`0` | `UT` | 无 | ✅通过（test_import_engine.py zeroForNumber=true，Amount 空白→0，断言通过） |
| M2-035 | `emptyAsNull=true` | 空白→NULL | `CODE` | 无 | ✅通过（CODE L1347 empty_as_null 空白→NULL） |
| M2-036 | `blankCellValues` / `removeText` / `replaceBlankWith` / `replaceTextFrom→To` | 对应文本替换清洗生效 | `CODE` | 无 | ✅通过（CODE L1349-1353 apply_cleaning 实现 blank_values/remove_text/replace_text_from→to/replace_blank_with） |

### M2-E 目标表命名

| 编号 | 操作路径 | 预期结果 | 验证方式 | 前置条件 | 验收结果 |
|------|----------|----------|----------|----------|----------|
| M2-037 | `tableNameRule=file/sheet/auto` | 按文件名/sheet名/自动判断命名 | `UT`（`tableNameRule=sheet` 全 sheet 模式） | 无 | ✅通过（test_import_feature_matrix.py `excel_all_sheets` 以 tableNameRule=sheet 每个 sheet 建独立表） |
| M2-038 | `tablePinyin=true` | 中文表名→拼音首字母（`员工`→`yg`） | `UT` `test_import_feature_matrix.py` | 中文文件名 | ✅通过（test_import_feature_matrix.py → `pinyin_table_fields: ok` 覆盖中文表名→拼音） |
| M2-039 | `tableCase=upper/lower/keep` | 表名大小写转换 | `UT` | 无 | ✅通过（test_import_engine.py tableCase=lower；CODE L1262-1266 表名大小写转换） |
| M2-040 | `tablePrefix` / `tableSuffix` | 表名加前后缀 | `CODE` | 无 | ✅通过（CODE L1489 `{tablePrefix}{base}{tableSuffix}` 命名拼接） |
| M2-041 | `tableRegex` | 正则提取表名 | `CODE` | 无 | ✅通过（CODE L1478 用 tableRegex 从文件名正则提取表名） |
| M2-042 | `symbolToUnderscore=true` | 表名符号→下划线 | `CODE` | 无 | ✅通过（CODE L1484 symbolToUnderscore 表名符号替换为下划线） |
| M2-043 | `duplicateTableMode=same/suffix/skip` | 重复表名时写入同表 / 加后缀 / 跳过 | `CODE` | 多文件同名 | ✅通过（CODE L3927-3947 duplicate_mode same/suffix/skip 分支：不换名/加 _2 后缀/整文件跳过） |

### M2-F 执行与高级

| 编号 | 操作路径 | 预期结果 | 验证方式 | 前置条件 | 验收结果 |
|------|----------|----------|----------|----------|----------|
| M2-044 | `POST /api/import`（完整导入） | 返回 summary（总文件/成功/失败/跳过/读取/写入/更新/跳过行）+ results + failures | `API` + `UT` | 文件 + 目标库 | ✅通过（API 实测 POST /api/import（rebuild）→ summary{totalFiles:1,successFiles:1,failedFiles:0,skippedFiles:0,rowsRead:3,rowsWritten:3,rowsUpdated:0,rowsSkipped:0} + results + failures=[] + exportPath；测试表已清理） |
| M2-045 | 多文件批量导入 | 逐文件执行，汇总结果 | `CODE`（`run_import_batch`） | 多文件 | ✅通过（test_p2_12_run_import_batch_skips_unchanged_file PASSED，run_import_batch 逐文件执行并汇总） |
| M2-046 | `sheetMode=all` + `tableNameRule=sheet` | 每个 sheet 建独立表，各写入对应行 | `UT` `test_import_feature_matrix.py`（2 sheet→2 行） | 多 sheet Excel | ✅通过（test_import_feature_matrix.py → `excel_all_sheets: ok`，2 sheet 各建表写入） |
| M2-047 | `batchRows=1` + `resumeImport=true` | 断点续传：按 checkpoint 从中断处继续；总写入正确 | `UT` `test_import_feature_matrix.py` | 无 | ✅通过（test_import_feature_matrix.py → `resume_checkpoint: ok`） |
| M2-048 | `skipSeenFile=true`（文件指纹命中） | 整文件跳过，`rowsWritten=0`，message 含「跳过」；目标表不被重写 | `UT` `test_p2_fixes.py::test_p2_12_*` | 先导入一次 | ✅通过（pytest test_p2_fixes.py → `test_p2_12_run_import_batch_skips_unchanged_file` PASSED，整文件跳过、含「跳过」） |
| M2-049 | `skipSeenFile=true` + 文件内容变更 | 指纹变化→重新导入 | `UT` `test_p2_fixes.py` | 无 | ✅通过（pytest test_p2_fixes.py → `test_p2_12_fingerprint_stable_and_sensitive` PASSED，指纹随内容变化） |
| M2-050 | 指纹 key 含目标身份（文件+表+连接） | 同文件导入不同表/不同连接不算「已导入」 | `UT` `test_p2_fixes.py::test_p2_12_file_key_includes_target_identity` | 无 | ✅通过（pytest test_p2_fixes.py → `test_p2_12_file_key_includes_target_identity` PASSED） |
| M2-051 | `beforeAllSql` | 全部导入开始前执行（如建临时表） | `CODE` + `UT`（`afterEachSql` 建标记表） | 目标库 | ✅通过（CODE L3346/L4410 beforeAllSql 全部导入开始前 target_execute_sql_batch 执行） |
| M2-052 | `afterEachSql` | 每次导入成功后执行 | `UT` `test_import_feature_matrix.py`（标记表写入 `after_each`） | 无 | ✅通过（test_import_feature_matrix.py → `after_each_sql: [["after_each"],["after_each"]]`，每次写入标记） |
| M2-053 | `afterAllSql` | 全部导入结束后执行 | `CODE` | 无 | ✅通过（CODE L3347/L4420 afterAllSql 全部导入结束后 target_execute_sql_batch 执行） |
| M2-054 | `afterQuerySql` + `afterQueryExport` | 导入后查询并将结果导出 Excel；返回 `exportPath` | `CODE` + `UT`（`export_query_to_excel`） | 无 | ✅通过（test_import_feature_matrix.py → `query_export` 返回 `mx_matrix_result.xlsx` 绝对路径） |
| M2-055 | `deleteAfterSuccess=true` | 导入成功后删除源文件 | `CODE` | 无 | ✅通过（CODE L4041 deleteAfterSuccess 成功后删除源文件） |
| M2-056 | `disableLog=true` | 不写 `_import_logs` | `CODE` | 无 | ✅通过（CODE L3929/L4005 disableLog 为真时跳过 log_import） |
| M2-057 | 目标 DB = SQLite（`targetDbType=sqlite`） | 写入本地 `imports.db` | `UT` | 无 | ✅通过（默认 targetDbType=sqlite 写入本地库；/api/import 实测入库 */api/tables 可见） |
| M2-058 | 目标 DB = MySQL（`connectionId` 指向已保存连接） | 写入 MySQL；密码从连接解密 | `UT` `test_export_engine.py::test_mysql_export_if_available` | MySQL 可连 | 🔁**复核修订：✅通过 → ⚠️部分通过（D5 证据无效）**<br>**复核实测**：该用例在隔离 `DATA_DIR` 下 `_db_connections` 行数 = **0**；函数体在"查不到 MySQL 连接"时直接 `return`（既不 `skip` 也不失败）→ 原判「MySQL 可连、密码解密」**在该测试中并不存在**，`PASSED` 具误导性。<br>**佐证 MySQL 本身可用**：`POST /api/connections/test` → `{"ok":true,"message":"连接成功。","version":"8.0.44","databases":["dc_p2_test","lcdp_fe"]}`（HTTP 200）；`GET /api/tables?targetDbType=mysql&connectionId=…` → 22 张表。（**E19**；强度不足 S3）<br>**🔧修复**：`return` 改为 `pytest.skip(...)`，并新增 `skipif(MYSQL_READY)` 的真实 MySQL 用例。**残留**：默认隔离环境仍 skip → 连接式导入/导出真实覆盖仍不完整，已列入待补验清单。<br>**🔧v5 已修复（⚠️部分 → ✅通过）**：测试改为**用产品自身落库路径**在隔离 sqlite 里写入一条指向 `127.0.0.1:3306/dc_p2_test` 的 MySQL 连接（`normalize_connection_payload` 校验 + `encode_secret` Fernet 加密，与 `handle_connection_save` 同构，**不插明文、不绕过加密**），再以该 `connectionId` 真实跑「导入 CSV → MySQL 表」与「MySQL 表 → xlsx 导出」，断言导入 `rowsWritten == 2`、导出 `rows == 2`、xlsx 列头与数据正确、且密码能解密回明文。**实测**：`test_mysql_export_if_available` 由 `SKIPPED` → **`PASSED`**；全量 6 个测试文件 **42 passed / 0 skipped / 0 failed**。**排除假绿**：mutation 测试（污染 `rowsWritten=999`、污染导出 `rows=999`、替换为不存在的 `connectionId`）三例**全部被断言/异常捕获**。 |
| M2-059 | 导入日志 `GET /api/logs` | 返回最近 50 条 `_import_logs` | `API` | 有导入历史 | ✅通过（API 实测 GET /api/logs → `{"ok":true,"logs":[...]}`，返回最近日志含 会员小票/中秋试饮 等） |

### M2-G 任务管理（导入任务资产）

| 编号 | 操作路径 | 预期结果 | 验证方式 | 前置条件 | 验收结果 |
|------|----------|----------|----------|----------|----------|
| M2-060 | 导入页「关联本机原文件」（`POST /api/import/choose-source`） | 文件副本落入 `linked_sources/`，返回绝对 `sourcePath`；提示需「保存为任务」 | `API` + `UI` | 选了文件 | ✅通过（CODE L4292 路由 POST /api/import/choose-source，文件落 linked_sources/ 返回 sourcePath） |
| M2-061 | `POST /api/task-source` | 文件落入 `task_sources/<uuid>/`，返回绝对路径 | `API` | 上传文件 | ✅通过（CODE L4289 路由 POST /api/task-source，文件落 task_sources/<uuid>/ 返回绝对路径） |
| M2-062 | 填任务名 + 路径 → 「保存为任务」（`POST /api/jobs`，单步 import） | 存为 `_jobs` 单步 import 资产；导入模式校验一致 | `API` + `UI` | 无 | ✅通过（CODE L4238/4316 /api/jobs POST 单步 import 存 _jobs；导入模式校验） |
| M2-063 | 任务列表加载 / 选中 / 打开 | 加载任务配置回填表单 | `UI` + `CODE`（`app.js`） | 已存任务 | ✅通过（app.js 任务列表选择后回填表单；P2-32 只回填配置，CODE L1354） |
| M2-064 | 路径非绝对（只填文件名） | 报错「定时任务必须填写完整路径」 | `UI` + `CODE` | 无 | ✅通过（app.js L473 抛错「定时任务必须填写完整路径，不能只填写文件名：…」） |
| M2-065 | 「新增导入」 | 清空编辑器，进入新建态 | `UI` | 无 | ✅通过（app.js L540/L595 「新增导入」按钮清空编辑器回退新建态） |
| M2-066 | 底部「已导入表」列表 | 显示本地 SQLite 中已导入的表，点击查看前 50 行 | `UI` + `API`（`/api/tables`） | 有导入历史 | ✅通过（API 实测 GET /api/tables → `{"ok":true,"tables":[...export_large_people,qa_import,...]}`，前端点击查前 50 行） |

---

## 模块 M3 · 导出（Export / OUT）

### M3-A 导出对象选择

| 编号 | 操作路径 | 预期结果 | 验证方式 | 前置条件 | 验收结果 |
|------|----------|----------|----------|----------|----------|
| M3-001 | 「选择表」（`GET/POST /api/export/sources`） | 返回表/视图列表，含 `name/type/comment/rows/rowsApproximate` | `API` + `UT` | 目标库有表 | ✅通过<br>原始输出：`export_people table · 3`；API 返回 `name/type/comment/rows/rowsApproximate` 完整字段。 |
| M3-002 | SQLite 源行数 | 精确 `COUNT(*)`，`rowsApproximate=false` | `UT` `test_p2_fixes.py::test_p2_7_sqlite_sources_are_precise` | 无 | ✅通过<br>原始输出：`{'name': 'export_people', 'type': 'table', 'comment': '', 'rows': 3, 'rowsApproximate': False}`；M11-005 内置用例也 PASSED。 |
| M3-003 | MySQL 源 ≤10 万行 | 精确 `COUNT(*)`，`rowsApproximate=false` | `UT` `test_p2_fixes.py::test_p2_7_mysql_small_table_uses_exact_count` | MySQL | 🔁**复核修订：⏭️跳过 → ✅通过（E9）**<br>原始输出（v1）：`MYSQL_UNAVAILABLE: OperationalError: (2003, "Can't connect to MySQL server on '127.0.0.1' ([Errno 111] Connection refused)")`；前置 MySQL 不可用。<br>**复核实测（本机 MySQL 可连）**：`pytest test_p2_fixes.py -v` → **`7 passed in 0.84s`（0 skipped）**，本用例 PASSED；源列表实测 `{'name': 'p2_rowcount_t', 'type': 'BASE TABLE', 'comment': '', 'rows': 1234, 'rowsApproximate': False}`。<br>**v1 跳过理由不成立**：`127.0.0.1:3306` 与连接表里的 `192.168.7.173:3306` 实为**同一实例**，实测可连（见环境指纹）。 |
| M3-004 | MySQL 源 >10 万行 | 用 `information_schema` 估算值，`rowsApproximate=true`（前端显示「约 N 行」） | `CODE` | 大表 | 🔁**复核修订：⏭️跳过 → ✅通过（E9）**<br>原始输出（v1）：同 M3-003；无可用 MySQL 大表，无法真实执行。<br>**复核实测**：本机 MySQL 上**本就有** >10 万行的表，无需新建业务表 —— `{'name': 'p2_big_t', 'type': 'BASE TABLE', 'comment': '', 'rows': 149948, 'rowsApproximate': True}`（149,948 行 → `rowsApproximate=true`）；另源列表输出 `已读取 22 个数据库对象`（含 `约 287,172 行` 等估算值）。 |
| M3-005 | 「单个查询」→ 输入 SQL | 以查询结果为导出源 | `UI` | 无 | ✅通过<br>原始输出：预览元数据 `query · 3 行 · 3 列`；结果内容 `Alice10北京Bob20上海Carol30北京`。 |
| M3-006 | 「多个查询」→ 多条 SQL（分号分隔） | 每条查询一个导出项 | `CODE` | 无 | 🔁**复核修订：❌不通过 → ❌不通过（理由重写）→ 🔧已修复（D3）**<br>原始输出（v1）：界面显示 `当前使用多个 SQL 查询，使用分号分隔`；预览控制台 `Failed to load resource: … status of 400 (Bad Request) @ /api/export/preview`。<br>**复核重定义（E13）**：①「多个查询」**真实载荷实测 200**（`export.js:399-406` 在 `sourceMode==="multi"` 时已按 `;` 拆成多个 item）→ v1 的 400 **不是"多查询"造成的**；② 400 的**真实触发条件**是「**单个查询**模式下把含分号的 SQL 当一条提交」（`{"items":[{"sql":"select 1; select 2"}]}` → `{"ok":false,"error":"near \";\": syntax error"}`）；③ **真实缺陷在预览侧**：`preview_export_job` 只取 `items[0]`（`server.py:2906-2911`）→ 多查询预览**只覆盖第 1 条**，而导出侧正确（`run` → `files=[qa_multi_1.csv, qa_multi_2.csv], rows=2`）→ **预览与导出行为不一致**（另见 M12-001 / 遗漏 G1）。<br>**🔧修复（D3）**：新增注释感知的 `split_sql_statements`（`server.py:2365`）—— 引号 + `/* */` + `--`（后跟空白）+ `#` 注释感知，纯注释尾块按 `has_code` 过滤；前端同载荷多 item 时提示「仅预览第 1 条」。经 **2 轮返工**（N1 注释感知、N2 前端同口径）后 QA 三轮复核通过：不变量 36/36、580 条模糊测试**丢字符 0**、`select 1; -- tail` 由 400 → 200、24 条 HEAD 对照**零接受性回退**。 |
| M3-007 | 「选择文件」加载 `.sql` 文件 | SQL 填入编辑器 | `UI` | 准备 .sql | ✅通过<br>原始输出：加载 `/tmp/m3_query.sql` 后编辑器值为 `select name, amount from export_people order by name;`。 |

### M3-B 导出执行与格式

| 编号 | 操作路径 | 预期结果 | 验证方式 | 前置条件 | 验收结果 |
|------|----------|----------|----------|----------|----------|
| M3-008 | 「预览」（`POST /api/export/preview`） | 返回列与前 20 行预览 | `API` | 选了对象 | ✅通过<br>原始输出：`{"sourceName":"export_people","columns":["name","amount","city"],"rows":[["Alice","10","北京"],["Bob","20","上海"],["Carol","30","北京"]]}`。 |
| M3-009 | 「开始导出」（`POST /api/export/run`） | 返回 `files` 路径列表 + `downloadUrls` + `rows` + `elapsedMs` | `API` + `UT` | 无 | ✅通过<br>原始输出：UI `1 个文件 · 3 行 · 6 ms`；结果 `m3_ui_export.xlsx`，`/tmp/dc_m3_browser/exports/m3_ui_export.xlsx`。 |
| M3-010 | 导出 `.xlsx` | 生成 xlsx；行数正确；含表头 | `UT` `test_export_engine.py::test_sqlite_exports` | 无 | ✅通过<br>原始输出：`rows=3`；`export engine checks passed`。 |
| M3-011 | 导出 `.csv` | 生成 CSV；首行表头；编码/分隔符按配置 | `UT` | 无 | ✅通过<br>原始输出：`header: name,amount,city`；M11-003 通过。 |
| M3-012 | 导出 `.json` | 生成 JSON；每行一个对象 | `UT` | 无 | ✅通过<br>原始输出：`type: list`；M11-003 通过。 |
| M3-013 | 导出 `.xml` | 生成 XML；首行 `<?xml` 声明 | `UT` | 无 | ✅通过<br>原始输出：`<?xml version="1.0" encoding="utf-8"?>`；M11-003 通过。 |
| M3-014 | 导出 `.txt` | 生成 TXT | `CODE` | 无 | ✅通过<br>原始输出：`{'exists': True}`。 |
| M3-015 | `splitField=city` 按字段分割 | 按字段值生成多个文件 | `UT`（`splitField=city`→2 文件） | 数据有多个城市 | ✅通过<br>原始输出：`files=[m3_split_Beijing.json,m3_split_Shanghai.json], rows=3`。 |
| M3-016 | `splitIntoFolder=true` | 按字段分割同时分到对应文件夹 | `CODE` | 无 | 🔁**复核修订：❌不通过 → ✅通过（E5）**<br>原始输出（v1）：`files=[.../exports/m3_split_folder_Beijing.json,…]`；`parents=['.../exports']`，未创建对应字段文件夹。<br>**复核实测（文件系统读回）**：**已创建** —— `files = [...\exports\北京\qa_split_folder_北京.json, ...\exports\上海\qa_split_folder_上海.json]`；`export_root` 下层级含 `上海\qa_split_folder_上海.json`、`北京\qa_split_folder_北京.json`；对照组（无 `splitIntoFolder`）仍平铺 `parents=['exports']`。<br>**过时根因**：修复只存在于**工作区未提交代码**（`git log -S "split_into_folder" -- server.py` → 空输出，即 HEAD 从未有过），v1「沿用此前已完成结果，不重复检查」故停留在修复前。 |
| M3-017 | `exportFields=name,amount` | 只导出指定字段 | `UT` | 无 | ✅通过<br>原始输出：CSV 首行 `name,amount`。 |
| M3-018 | `whereClause=amount >= 20` | 只导出满足条件的行 | `UT` | 无 | ✅通过<br>原始输出：`rows=2`。 |
| M3-019 | `exportMode=workbook/sheet/data` | 覆盖整个文件 / 仅覆盖指定 sheet / 仅覆盖 sheet 数据 | `CODE` | 无 | ✅通过<br>原始输出：`after_sheet=['First','Second']`；`after_data=['First','Second']`；`second_rows=3`。 |
| M3-020 | `headerMode=field/comment/none` | 字段名 / 字段注释 / 无表头 | `CODE` + `UT`（field 模式） | 无 | 🔁**复核修订：⏭️跳过 → ❌不通过（功能真未实现，D2）→ 🔧已修复**<br>原始输出（v1）：SQLite `field_rows=4, none_rows=3`；理由「`comment` 变体需要带字段注释的 MySQL，当前 MySQL 不可用」。<br>**复核实测（E14）**：带列注释的 MySQL 表 → 导出 CSV 首行仍为 **`id,name`**（未使用字段注释）→ **功能未实现**；`server.py` 只区分 `none` / 非 `none`。**v1 把"功能未实现"误归为"环境跳过"**（MySQL 注释表可建可验，非环境问题 → 违反"跳过理由二选一"，对应强度不足 S6）。<br>**🔧修复**：新增 `export_header_labels`（`server.py:2440`）三路写出，`field` / `comment` / `none` 均生效；QA 回归覆盖「注释含逗号 / 含双引号 / 含真实换行 / 有无注释混排 / 空白注释回退 / SQLite 目标回退」全部通过。 |
| M3-021 | `commentAsFileName=true`（MySQL）+ 未指定文件名 | 表注释作为文件名 | `UT` `test_p2_fixes.py::test_p2_13_comment_as_filename_mysql` | MySQL | 🔁**复核修订：⏭️跳过 → ✅通过（E10）**<br>原始输出（v1）：P2 用例 `SKIPPED (本机 MySQL 不可用)`。<br>**复核实测**：`pytest test_p2_fixes.py -v` → `7 passed`（0 skipped），本用例 PASSED；`commentAsFileName=true` → 文件 stem **`P2注释文件名`**；源表 `{'name': 'p2_comment_t', 'type': 'BASE TABLE', 'comment': 'P2注释文件名', 'rows': 3}`。 |
| M3-022 | `commentAsFileName=true` + 指定 `outputName` | 显式文件名优先于表注释 | `UT` `test_p2_fixes.py` | MySQL | 🔁**复核修订：⏭️跳过 → ✅通过（E10）**<br>原始输出（v1）：P2 用例 `SKIPPED (本机 MySQL 不可用)`。<br>**复核实测**：显式 `outputName` 优先 → 文件 stem **`explicit_qa`**（表注释未覆盖显式名）。 |
| M3-023 | `commentAsFileName=true`（SQLite） | 不受影响，仍用表名 | `UT` `test_p2_fixes.py::test_p2_13_sqlite_unaffected_by_flag` | 无 | ✅通过<br>原始输出：文件 stem `export_people`。 |
| M3-024 | 大数据流式导出（20000 行 xlsx） | 行数正确（20000+表头）；不 OOM | `UT` `test_export_engine.py::test_large_streaming_export` | 无 | ✅通过<br>原始输出：M11-003 命令退出码 0，`export engine checks passed`。 |
| M3-025 | 大数据流式导出（20000 行 csv） | 行数正确；首行表头 | `UT` | 无 | ✅通过<br>原始输出：M11-003 命令退出码 0，`export engine checks passed`。 |

### M3-C 格式选项

| 编号 | 操作路径 | 预期结果 | 验证方式 | 前置条件 | 验收结果 |
|------|----------|----------|----------|----------|----------|
| M3-026 | `rowHeight=22` + `columnWidth=18` + `fontName=Arial` + `fontSize=11` | xlsx 行高/列宽/字体生效 | `UT` `test_export_engine.py` | 无 | ✅通过<br>原始输出：`default_row_height=22.0, col_width=18.0, font=Arial, font_size=11.0`。 |
| M3-027 | `addBorder=true` | 添加边框线 | `UT` | 无 | ✅通过<br>原始输出：`border_left='thin'`。 |
| M3-028 | `lockHeader=true` | 锁定表头行不可编辑（Sheet 保护） | `UT` | 无 | 🔁**复核修订：❌不通过 → ✅通过（E6）**<br>原始输出（v1）：`{'sheet_protected': False, 'A1_locked': True, 'A2_locked': True, 'B1_locked': True}`。<br>**复核实测（openpyxl 读回，3 条路径）**：小表 / 2 万行大表 / 多 item **均 `sheet_protected=True`，且仅表头行 locked** —— `A1_locked=True, A2_locked=False, B1_locked=True, B2_locked=False`（`max_row=20001` 大表一致）。<br>**过时根因（可解释 v1 输出）**：HEAD `server.py:2557` 的 `can_stream` 判定**未考虑锁定** → `lockHeader` 走 write_only 流式写入、保护代码整段被跳过；工作区（`server.py:2777-2787`）已补 `lock_requested = parse_bool(fields,"lockHeader",False) or bool(export_split_list(fields.get("lockedColumns","")))` 并纳入 `can_stream` 条件。 |
| M3-029 | `lockedColumns` | 锁定指定列 | `CODE` | 无 | 🔁**复核修订：❌不通过 → ✅通过（E7）**<br>原始输出（v1）：`lockedColumns=name` 时 `sheet_protected=False`；虽单元格默认 `locked=True`，但工作表未保护。 <br>**复核实测（openpyxl 读回）**：`lockedColumns=name` → `sheet_protected=True`，**name 列 locked=True、其余列 False** —— `{'A1_locked': True, 'A2_locked': True, 'B1_locked': False, 'B2_locked': False}`。<br>**过时根因**：与 M3-028 同一处（`can_stream` 未考虑锁定），工作区已修。 |
| M3-030 | CSV `encoding` / `delimiter` / `lineDelimiter` | 按配置输出 | `UT`（utf-8） | 无 | ✅通过<br>原始输出：前两行 `name;amount;city` / `Alice;10;Beijing`。 |

### M3-D 下载与目录选择

| 编号 | 操作路径 | 预期结果 | 验证方式 | 前置条件 | 验收结果 |
|------|----------|----------|----------|----------|----------|
| M3-031 | `GET /api/export/download?path=…` | 返回文件流；`Content-Disposition` 含 RFC 5987 编码的中文文件名 | `UT` `test_export_engine.py::test_chinese_filename_download_header` | 有导出文件 | ✅通过<br>原始输出：`HTTP/1.0 200 OK`；`Content-Disposition: attachment; filename="export.xlsx"; filename*=UTF-8''%E4%B8%AD%E6%96%87%E5%AF%BC%E5%87%BA%E6%96%87%E4%BB%B6.xlsx`；`body_bytes=4`。 |
| M3-032 | 下载路径不在 `DOWNLOAD_ALLOWED_ROOTS`（exports/uploads/task_sources） | 拒绝下载（安全限制） | `CODE` | 无 | ✅通过<br>原始输出：`HTTP/1.0 400 Bad Request`；`该路径不在允许下载的目录内。`。 |
| M3-033 | 「…」选文件夹（`POST /api/export/choose-target` `{"mode":"folder"}`，Windows） | 由**服务进程**弹出系统原生文件夹选择框：窗口类 `#32770`、归属 PID = 服务进程、**窗口标题为系统本地化文案「浏览文件夹」**、框内说明文字为「选择导出文件夹（选择后会显示完整路径）」；含「文件夹(&F):」输入框与「确定 / 取消 / 新建文件夹(&M)」；点「确定」返回所选目录的**绝对路径**且 `cancelled=false`，取消返回 `{"ok":true,"path":"","cancelled":true}` | `API`+`UI` | Windows 桌面 | 🔁**复核修订：⏭️跳过 → ✅通过（Windows 补跑；1 子项未验）**<br>原始输出（v1）：`{'os.name': 'posix', 'platform': 'Linux'}`。<br>**补跑实测（主理人独立复跑确认）**：`EnumWindows` → `hwnd=0x71320 class="#32770" pid=<服务进程> title="浏览文件夹" visible=True`；子控件 `Static id=0x3742 text='选择导出文件夹（选择后会显示完整路径）'`、`Button id=0x1 '确定'`、`Button id=0x2 '取消'`、`Button id=0x3746 '新建文件夹(&M)'`；`WM_CLOSE` → `{"ok":true,"path":"","cancelled":true}`；`WM_COMMAND/IDOK` → `{"ok":true,"path":"C:\\Users\\Administrator","cancelled":false}`。<br>**两条勘误**：(1) **断言窗口标题不要用 `lpszTitle`** —— 该文案渲染在**框内**（`Static id=0x3742`），窗口标题固定为系统本地化「浏览文件夹」，照 v1 写法断言会**误判为失败**；(2) **v2 勘误、v3 已修**：v2 时 `initial` 在 folder 模式**未接线**（folder 分支未调用 `resolve_initial_dir`，W6）→「定位到**指定**目录」子项未验。**v3 修复**：弃 `SHBrowseForFolderW`，改用 `IFileDialog` + `FOS_PICKFOLDERS` + `SetFolder`（纯 ctypes COM，零依赖）。⚠️ **注意：换实现后窗口标题不再是系统文案「浏览文件夹」**，而是 `SetTitle` 传入的「选择导出文件夹（选择后会显示完整路径）」，地址栏为 `ToolbarWindow32` —— 凡“按标题断言‘浏览文件夹’”的写法对 folder 模式**已失效**（file 模式仍是 `GetSaveFileNameW`，标题即 `lpstrTitle`，未变）。**v3 实测（QA 独立复跑）**：两个**不同**目标目录均返回到位、地址栏可见目标路径（排除“shell 记住上次位置”假阳性）；`handle_export_choose_target` 与 file 模式分支 **byte-identical**（换方案未波及旁路）。<br>⚠️ 运维风险（W7）：该接口**阻塞 HTTP 线程直到对话框关闭**，无超时兜底。 |
| M3-034 | 同上 `{"mode":"file","extension":"xlsx","suggest":"…"}` | 由**服务进程**弹出系统原生「另存为」框：类 `#32770`、归属 PID = 服务进程、窗口标题 `选择导出文件保存位置`；「文件名(N)」预填 `<suggest>.<extension>`（无参时 `export.xlsx`）、「保存类型(T)」按 `extension` 过滤（`XLSX 文件 (*.xlsx)`）；点「保存」返回绝对路径且 `cancelled=false`，取消返回 `cancelled=true`；**该调用只返回路径，不落盘** | `API`+`UI` | Windows 桌面 | 🔁**复核修订：⏭️跳过 → ✅通过（Windows 补跑）**<br>原始输出（v1）：`{'os.name': 'posix', 'platform': 'Linux'}`。<br>**补跑实测**：`title="选择导出文件保存位置"`、`Button id=0x1 text='保存(&S)'`、`ToolbarWindow32 id=0x3E9 '地址: C:\…\2026-09-11-08-34-04'`（**`initial` 在 file 模式生效**）；`{"mode":"file","initial":"<目录>","suggest":"抽验驱动","extension":"xlsx"}` + IDOK → `{"ok":true,"path":"C:\\Users\\Administrator\\WorkBuddy\\2026-09-11-08-34-04\\抽验驱动.xlsx","cancelled":false}`；`extension=csv` → `…\抽验取消.csv`；`WM_CLOSE` → `{"ok":true,"path":"","cancelled":true}`；目标目录**无新文件**（不落盘）。<br>**注意**：file 模式窗口标题**就是** `lpstrTitle`（与 folder 模式不同，见 M3-033 勘误）。 |
| M3-035 | 非 Windows 调用 choose-target | 返回 `unsupported: true`（501），前端回退手填 | `API` | 非 Windows | ✅通过<br>原始输出：`HTTP/1.0 501 Not Implemented`；`{"ok": false, "unsupported": true, "error": "服务端不是 Windows，无法打开系统选择框，请手动填写绝对路径。"}`。 |
| M3-036 | `openFileAfterExport` / `openFolderAfterExport` | 导出后打开文件/文件夹 | `CODE` | 桌面环境 | 🔁**复核修订：❌不通过 → ✅通过（E8；v1 验证方法错误）**<br>原始输出（v1）：`node --check public/export.js` 退出码 0；`openFileAfterExport 2`、`openFolderAfterExport 2`、`window.open references 0`；结论"未找到导出后打开文件/文件夹的执行调用"。<br>**复核纠错（强度不足 S4）**：v1 **只读了前端一个文件**，而该功能**后端 100% 实现** —— `open_exported_files`（`server.py:2302-2337`，用 `os.startfile`）+ `server.py:5006` handler 调用；`run_export_job` 内不调用属正常（调用点在 HTTP handler）。前端本就不该有 `window.open`，**只查 `public/export.js` 必然为 0**。<br>**正确验证方式**：读 `server.py` + 真实跑一次导出。**CODE 类须覆盖后端 + 前端两侧**。 |
| M3-037 | `filePrefix` / `fileSuffix` | 文件名加前后缀 | `CODE` | 无 | ✅通过<br>原始输出：生成文件名 `prem3_prefixsuf.csv`（前缀/后缀均生效）。 |
| M3-038 | `batchRows` + `splitByBatch` | 按批次分割导出文件 | `CODE` | 无 | ✅通过<br>原始输出：`files=[m3_batch_001.csv,m3_batch_002.csv], rows=3`。 |
| M3-039 | `exportTimeField` | 导出时间作为列 | `CODE` | 无 | ✅通过<br>原始输出：CSV 首行 `name,amount,city,exported_at`。 |
| M3-040 | `skipEmptyTable=true` | 空表跳过不导出 | `CODE` | 无 | ✅通过<br>原始输出：`files=[], rows=0, elapsedMs=0`。 |
| M3-041 | `beforeSql` / `afterSql` | 导出开始前/结束后执行 SQL | `CODE` | 无 | ✅通过<br>原始输出：SQLite 标记表 `m3_before_marker`、`m3_after_marker` 均存在。 |

### M3-E 导出任务管理

| 编号 | 操作路径 | 预期结果 | 验证方式 | 前置条件 | 验收结果 |
|------|----------|----------|----------|----------|----------|
| M3-042 | 填任务名 → 「保存」（`POST /api/jobs`，单步 export） | 存为单步 export 资产 | `CODE`（`export.js::saveExportTask`） | 无 | ✅通过<br>原始输出：`已保存导出任务：m3_saved_export_task，可在定时任务中调用。`；列表显示 `OUTm3_saved_export_task`。 |
| M3-043 | 任务名留空 → 保存 | 报错「保存任务前请先填写」 | `UI` + `CODE` | 无 | ✅通过<br>原始输出：状态栏 `请填写任务名称。`。 |
| M3-044 | 加载已存导出任务 | 配置回填表单 | `CODE` | 已存任务 | ✅通过<br>原始输出：打开任务后 `sql=select name, amount from export_people`、`taskName=m3_saved_export_task`、`source=当前使用单个 SQL 查询`。 |
| M3-045 | 导出结果区 | 显示导出文件列表 + 下载链接 | `UI` | 有导出历史 | ✅通过<br>原始输出：`m3_ui_export.xlsx成功/tmp/dc_m3_browser/exports/m3_ui_export.xlsx下载文件`。 |

---

## 模块 M4 · 查询（Query / SQL）

| 编号 | 操作路径 | 预期结果 | 验证方式 | 前置条件 | 验收结果 |
|------|----------|----------|----------|----------|----------|
| M4-001 | 选连接 → 输入 `SELECT …` → 「执行查询」（`POST /api/query/run`） | 返回 `columns / rows / rowCount / elapsedMs / truncated / message` | `API` | 可连库 | ✅通过<br>原始输出：`rowCount=3; truncated=false; message=查询执行成功。`。 |
| M4-002 | 输入 `INSERT/UPDATE/DELETE/DDL` | 报错「只允许 SELECT、SHOW、DESCRIBE、EXPLAIN 和只读 WITH」 | `API` + `CODE` | 无 | ✅通过<br>原始输出：写操作和 DDL 均返回 `查询模块当前只允许 SELECT、SHOW、DESCRIBE、EXPLAIN 和只读 WITH 查询。`。 |
| M4-003 | 输入含 `;` 多语句 | 报错「一次只能执行一条 SQL」 | `API` + `CODE` | 无 | ✅通过<br>原始输出：HTTP 400；`一次只能执行一条 SQL 查询。`。 |
| M4-004 | 结果 >1000 行 | 返回前 1000 行 + `truncated=true` | `CODE` | 大结果集 | ✅通过<br>原始输出：`rowCount=1000; rows_len=1000; truncated=True; last_row=["1000","1000"]`。 |
| M4-005 | `Ctrl+Enter` 快捷键 | 执行查询 | `UI` | 无 | 🔁**复核修订：⏭️跳过 → ✅通过（E11）**<br>原始输出（v1）："查询页实际以 MySQL 连接执行，当前隔离环境无可连接 MySQL，无法真实完成 Ctrl+Enter 查询链路。"<br>**复核拆解（E16）**：前半句**成立**（`query.js:78` 硬编码 `targetDbType:"mysql"` → **产品限制**，见「产品限制」节）；后半句**不成立** —— 本机 MySQL 实测可连。<br>**复核实测（Playwright）**：查询页连接下拉 = `[{"v":"0f22c4df…","t":"sr"},…]`；`Ctrl+Enter` 后结果行 = **`[["8.0.44","42"]]`**、结果区文字 = `查询执行成功`、`pageerror` 数量 = **0**（SQL：`select version() as v, 42 as n`，连接 `sr`）。 |
| M4-006 | 填查询名 + SQL → 「保存查询」（`POST /api/queries`） | 存入 `_saved_queries`；同时 upsert 代理 job（`查询：xxx`） | `API` + `DB` | 无 | ✅通过<br>原始输出：保存查询后 `_saved_queries` 有记录，同时生成 `查询：<名称>` 代理作业。 |
| M4-007 | 查询名为空 → 保存 | 报错「请填写查询名称」 | `API` + `CODE` | 无 | ✅通过<br>原始输出：查询名称为空返回 `请填写查询名称。`。 |
| M4-008 | SQL 为空 → 保存 | 报错「请输入要保存的 SQL」 | `API` + `CODE` | 无 | ✅通过<br>原始输出：SQL 为空返回 `请输入要保存的 SQL。`。 |
| M4-009 | `GET /api/queries` | 返回全部已保存查询（含 `connectionId`） | `API` | 无 | ✅通过<br>原始输出：`GET /api/queries` 返回保存查询及 `connectionId`。 |
| M4-010 | 选中已保存查询 | SQL 与名称回填编辑器 | `UI` | 已存查询 | ✅通过<br>原始输出：浏览器点击已保存查询后，名称 `qa_idempotent`、SQL `select 2` 正确回填。 |
| M4-011 | 「删除查询」（`DELETE /api/queries?id=…`） | 删 `_saved_queries` + 删对应代理 job；返回 `removedJobs` | `API` + `DB` | 已存查询 | ✅通过<br>原始输出：删除查询返回 `removedJobs`，查询记录和对应代理作业均删除。 |
| M4-012 | 「转到导出」 | 当前 SQL 跳转导出页并填入 | `UI` | 有 SQL | ✅通过<br>原始输出：点击「转到导出」跳转 `/export.html`，SQL `select 2` 和文件名 `qa_idempotent` 已带入。 |
| M4-013 | 重新保存同名查询 | 更新同一条，不产生重复代理 job | `CODE`（`_sync_query_to_job` 幂等 upsert） | 已存查询 | ✅通过<br>原始输出：同名重存后 `query_count=1; proxy_count=1; sql=select 2`。 |

---

## 模块 M5 · 表管理（Tables / TAB）

| 编号 | 操作路径 | 预期结果 | 验证方式 | 前置条件 | 验收结果 |
|------|----------|----------|----------|----------|----------|
| M5-001 | 选连接 → 「刷新表列表」（`GET /api/tables`） | 返回表/视图列表 | `API` + `UI` | 可连库 | 🔁**复核修订：⏭️跳过 → ✅通过（E12）**<br>原始输出（v1）：SQLite API 返回 `tables=["qa_big","qa_table"]`；以"表管理页固定 `targetDbType="mysql"` + MySQL 不可连接"为由跳过。<br>**复核拆解（E15）**：前半句**成立**（`tables.js:31` 硬编码 `targetDbType:"mysql"` → **产品限制**）；后半句**不成立**（本机 MySQL 可连）。<br>**复核实测（Playwright + API，只读）**：`已读取 22 个数据库对象`（含 `约 287,172 行` 等估算值）；`GET /api/tables?targetDbType=mysql&connectionId=…` → 22 张表（`ai外呼客户触达名单`、`上海中秋活动`、`中秋试饮`、`会员小票表` …）。 |
| M5-002 | 搜索框输入表名 | 列表实时过滤 | `UI` | 有表 | 🔁**复核修订：⏭️跳过 → ✅通过（E12）**<br>原始输出（v1）："搜索框控件存在（placeholder=`输入表名或注释`），但当前页面没有可用 MySQL 表列表，无法完成实时过滤验收。"<br>**复核实测（Playwright）**：搜索前 22 条 → 搜「中秋」后 2 条 `["上海中秋活动","中秋试饮"]`；搜「会员」后 1 条 `["会员小票表"]`。 |
| M5-003 | 选表 → 「字段结构」Tab（`GET /api/target-table-details`） | 显示列名/类型/注释/是否可空/主键 | `API` + `UI` | 选了表 | 🔁**复核修订：⏭️跳过 → ✅通过（E12）**<br>原始输出（v1）：SQLite API 字段 `id,name,amount`、DDL 正确；"页面完整字段结构 UI 依赖 MySQL，当前不可连接"。<br>**复核实测（Playwright）**：选中表 = `会员小票表`，元信息 = `BASE TABLE · 22 个字段`；`[columns] display=block 其它面板可见数=0 表体行数=22`；表头 `序号 字段名 数据类型 键 允许空 默认值 附加属性 注释`，首行 `1 提单时间 varchar(100) MUL 是 …`。 |
| M5-004 | 「数据预览」Tab（`GET /api/table`） | 显示前 100 行 | `API` + `UI` | 选了表 | 🔁**复核修订：⏭️跳过 → ✅通过（E12）**<br>原始输出（v1）：SQLite API `totalRows=3`；页面预览 UI 以"依赖 MySQL"为由跳过。<br>**复核实测（Playwright）**：`[preview] display=block 其它面板可见数=0 表体行数=100`。 |
| M5-005 | 「建表 DDL」Tab | 显示 `SHOW CREATE TABLE` / SQLite schema | `API` + `UI` | 选了表 | 🔁**复核修订：⏭️跳过 → ✅通过（E12）**<br>原始输出（v1）：SQLite schema DDL 正确；DDL Tab UI 以"依赖 MySQL"为由跳过。<br>**复核实测（Playwright）**：`[ddl] display=block 其它面板可见数=0`；内容 `CREATE TABLE \`会员小票表\` ( \`提单时间\` varchar(100) …`。 |
| M5-006 | 「转到查询」 | 跳转查询页 | `UI` | 选了表 | 🔁**复核修订：⏭️跳过 → ✅通过（E12）**<br>原始输出（v1）："页面存在「转到查询」按钮，但当前无可选表，无法完成真实点击后的表上下文跳转验收。"<br>**复核实测（Playwright）**：按钮 `disabled` 状态 `{ q: false, e: false }`（可点）；跳转后 URL = `http://127.0.0.1:51978/query.html`；查询页 SQL 框回填 = `SELECT * FROM \`ai外呼客户触达名单\` LIMIT 100`（表上下文正确带入）。 |
| M5-007 | 「转到导出」 | 跳转导出页并选中该表 | `UI` | 选了表 | 🔁**复核修订：⏭️跳过 → ⚠️部分通过（E12 + 部分验证）**<br>原始输出（v1）："页面存在「转到导出」按钮，但当前无可选表，无法完成真实点击后的表选择回填验收。"<br>**复核实测（Playwright）**：跳转后 URL = `http://127.0.0.1:51978/export.html`、`pageerror` 数量 = **0**；但「**选中该表**」子项**未逐项断言**（仅验到跳转层）→ 记 ⚠️部分通过，已列入待补验清单。<br>**🔧v5 已修复（⚠️部分 → ✅通过）**：补齐「**选中该表**」逐项断言（Playwright，真实走「表管理页 → 选表 → 转到导出」）。**正向 8/8 PASS**：URL 到 `export.html`；SQL 框 == ``SELECT * FROM `源表` ``；`queryName` == 源表名；`exportFileName` == 源表名；`sourceMode` == `query`；`pageerror` = 0；三处交叉一致。**负向 4/4 FAIL（证明断言可证伪）**：清空 `sessionStorage` 后直接打开 `export.html`（等价"未选表"），同一套断言全部失败（实测 `sql="select 1 as value"`、`queryName="query"`、`exportFileName=""`）→ 排除"只打印不比较"的假绿。**机制说明**：该跳转经 `sessionStorage`（`pendingExportSql` / `pendingExportName`）传参，非 URL query。 |

---

## 模块 M6 · 作业（Jobs / JOB）

| 编号 | 操作路径 | 预期结果 | 验证方式 | 前置条件 | 验收结果 |
|------|----------|----------|----------|----------|----------|
| M6-001 | `GET /api/jobs` → 作业页列表 | **只显示多步作业**；单步导入/导出任务资产、`- 自动作业` 载体被过滤 | `API` + `UI` + `CODE`（`jobs.js::loadJobs` 过滤） | 有各类 job | ✅通过<br>原始输出：作业页只显示多步骤作业；单步导入/导出资产和自动作业载体被过滤。 |
| M6-002 | 「新增作业」→ 填名 → 添加步骤 | 步骤下拉只列已保存的单步任务 + 保存查询 | `UI` | 已存任务/查询 | ✅通过<br>原始输出：新增作业对话框可填名称，候选步骤来自已保存任务/查询。 |
| M6-003 | 选类型(导入/导出/查询) → 任务下拉 | 按类型自动筛选 | `UI` | 无 | ✅通过<br>原始输出：选择导入、导出、查询类型后，任务下拉分别显示对应候选。 |
| M6-004 | 点 `>>` 添加步骤 | 复制任务**完整配置快照**（深拷贝 config），与源任务脱钩 | `CODE`（`jobs.js::draftStepFromSelection`） | 无 | ✅通过<br>原始输出：`>>` 添加步骤后显示完整步骤；源码使用 `JSON.parse(JSON.stringify(source.config))` 深拷贝配置。 |
| M6-005 | 步骤 `<<` 移除 / `↑` `↓` 调序 | 步骤增删与顺序调整 | `UI` | 有步骤 | ✅通过<br>原始输出：`<<`、`↑`、`↓` 操作真实执行；两步骤顺序从导出→查询调整为查询→导出。 |
| M6-006 | 「确认」保存（`POST /api/jobs`） | 存入 `_jobs`（steps_json + guard_json）；含 sync 步骤时报错 | `API` + `DB` | 无 | ✅通过<br>原始输出：作业保存成功写入 `_jobs`；sync 步骤保存返回 `同步模块尚未开放。`。 |
| M6-007 | 作业名留空 → 保存 | 报错「请填写作业名称」 | `API` + `CODE` | 无 | ✅通过<br>原始输出：空作业名返回 `请填写作业名称。`。 |
| M6-008 | 无步骤 → 保存 | 报错「请至少添加一个子任务」 | `API` + `CODE` | 无 | ✅通过<br>原始输出：无步骤保存返回 `请至少添加一个子任务。`。 |
| M6-009 | 「编辑作业」→ 修改 → 保存 | 更新同一条 job（`on conflict do update`），`created_at` 不变 | `API` + `DB` | 已存作业 | ✅通过<br>原始输出：编辑作业后同一 ID 更新，`created_at` 保持不变。 |
| M6-010 | 「复制作业」（`POST /api/jobs`，新 id，名加「（副本）」） | 深拷贝全部步骤，生成新作业并选中 | `UI` + `CODE` | 已存作业 | ✅通过<br>原始输出：复制作业生成新作业，名称带 `（副本）`；随后已删除临时副本。 |
| M6-011 | 「删除作业」（`DELETE /api/jobs?id=…`） | 删 `_jobs` 行 + 关联 `_schedules` 一并删；二次确认 | `API` + `DB` | 已存作业 | ✅通过<br>原始输出：删除作业确认后 `_jobs` 行删除，关联调度同步清理。 |
| M6-012 | 「立即执行」（`POST /api/jobs/run`） | 按顺序执行各步骤；返回 `status(成功/失败/跳过) + message` | `API` + `UT` `test_jobs_schedule.py` | 有多步作业 | ✅通过<br>原始输出：`{"status":"成功","message":"作业执行成功：2 个步骤成功，0 个步骤未启用。"}`。 |
| M6-013 | 步骤含 query→insert + export | query 步骤先写数据，export 步骤导出；两步均成功 | `UT` `test_jobs_schedule.py` | 无 | ✅通过<br>原始输出：query 步骤写入 `qa_job_table`，export 步骤生成导出文件，两步均成功。 |
| M6-014 | 步骤失败 + `continueOnError=false` | 中止后续步骤；作业标记「失败」 | `CODE` | 无 | ✅通过<br>原始输出：失败停止：`作业执行失败：0 个步骤成功，1 个步骤失败... no such table: missing_qa_table`。 |
| M6-015 | 步骤失败 + `continueOnError=true` | 继续执行下一步；作业最终「失败」 | `CODE` | 无 | ✅通过<br>原始输出：失败继续：`作业执行失败：1 个步骤成功，1 个步骤失败...`；第二步已执行。 |
| M6-016 | 步骤 `enabled=false` | 跳过该步，计入 `skipped_steps` | `CODE` | 无 | ✅通过<br>原始输出：禁用步骤跳过，结果包含 `0 个步骤未启用` 统计；启用步骤正常执行。 |
| M6-017 | 作业循环引用（job 步骤引用自身） | 报错「检测到作业循环引用，已停止执行」 | `CODE`（`visited` 集合） | 构造循环 | ✅通过<br>原始输出：`检测到作业循环引用，已停止执行。`。 |
| M6-018 | sync 步骤 | 报错「同步模块尚未开放」 | `CODE` + M6-006 | 无 | ✅通过<br>原始输出：保存 sync 步骤返回 `同步模块尚未开放。`。 |
| M6-019 | 运行日志面板（`GET /api/job-runs?jobId=…`） | 显示最近 80 次运行：状态/起止时间/耗时/步骤明细 | `API` + `UI` | 有运行历史 | ✅通过<br>原始输出：作业页运行日志展示状态、开始/结束时间、耗时、成功/失败步骤及错误信息。 |
| M6-020 | 步骤级运行记录（`_job_run_steps`） | 每步独立记录状态/起止/耗时/message | `DB` + `UT` | 有运行历史 | ✅通过<br>原始输出：SQLite `_job_run_steps` 每步均有状态、起止时间、耗时和 message。 |
| M6-021 | 立即执行带 `scheduleId` | 结果回写 `_schedules.last_run_at/last_status` | `CODE`（`handle_job_run`） | 有调度 | ✅通过<br>原始输出：带 `scheduleId` 立即执行返回成功；调度记录更新为 `lastStatus=成功`、`lastRunAt=2026-09-14 11:09:16`。 |

### M6-A 作业执行条件（Guard）

| 编号 | 操作路径 | 预期结果 | 验证方式 | 前置条件 | 验收结果 |
|------|----------|----------|----------|----------|----------|
| M6-022 | guard `type=file_has_new` | 作业中全部启用 import 步骤的源文件无更新→整作业跳过（`status=跳过`） | `CODE`（`_guard_summary` + 指纹比对） | 有 import 步骤 | ✅通过<br>原始输出：文件 Guard 首次执行成功，文件未变化再次执行返回 `跳过`。 |
| M6-023 | 步骤 config `skipIfFileUnchanged=true`（单步级） | 仅该步文件守卫启用 | `CODE` | 无 | ✅通过<br>原始输出：单步 `skipIfFileUnchanged=true` 首次执行成功，第二次跳过。 |
| M6-024 | 文件守卫：文件有更新 | 正常执行，执行后更新指纹基线 | `CODE` | 无 | ✅通过<br>原始输出：修改源文件后文件指纹变化，第三次作业恢复执行并更新基线。 |
| M6-025 | guard `date_match` + `mode=range`（start~end） | 今天不在范围内→跳过 | `CODE`（`evaluate_job_guard`） | 无 | ✅通过<br>原始输出：日期范围不匹配返回 `ok=false`，原因含作业已跳过。<br>**复核补充（真实执行 39 行真值表，基准 `now = 2026-09-14 10:30`）**：`range` **主路径与边界均正确** —— 命中 → `(True,'今天在作业执行日期范围内')`；**边界 `start=end=今天` → True（闭区间正确）**；未命中（明天起 / 昨天止）→ False；**缺 end / 全空 → `ValueError: 作业执行条件：日期范围需填写开始与结束日期。`**；跨年 `12-31~01-02` → False（ISO 字符串比较正确）；带时间戳格式 → True（`[:10]` 截断正确）。<br>**同族缺陷（D4）**：`weekday` / `monthday` 空值或 `mode` 拼错时**静默放行**（与本条 `range` 报错行为不一致）→ 详见 M6-027 / M6-028 与 M12-002 / M12-003。 |
| M6-026 | guard `date_match` + `mode=dates`（指定日期） | 今天不在列表→跳过 | `CODE` | 无 | ✅通过<br>原始输出：指定日期不匹配返回 `ok=false`。<br>**复核补充**：命中 / 未命中 → `True` / `False`；**空列表 → `ValueError: …请至少选择一个执行日期。`**（与 `weekday`/`monthday` 空值**静默放行**形成对比，见 D4）。 |
| M6-027 | guard `date_match` + `mode=weekday`（周几） | 今天不是指定周几→跳过 | `CODE` | 无 | ✅通过<br>原始输出：指定星期不匹配返回 `ok=false`。<br>🔁**复核补充（D4 边界缺陷，已修复）**：命中（周一）/ 未命中 → `True` / `(False,'作业仅在 周三、周五 执行…')`；但 **`values` 空列表 → `(True,'')` 静默放行**（不报错、天天执行）；**字符串混入 `["","1","x"]` → `(True,'今天为作业执行日')`** → 后端兜底缺失（UI 侧有校验，见 M6-030）。<br>**🔧修复**：`evaluate_job_guard`（`server.py:3788`）对空值 / 拼错 `mode` 抛 `ValueError`。 |
| M6-028 | guard `date_match` + `mode=monthday`（每月几号） | 今天不是指定号→跳过 | `CODE` | 无 | ✅通过<br>原始输出：指定每月日期不匹配返回 `ok=false`。<br>🔁**复核补充（D4 边界缺陷，已修复）**：命中（14）/ 未命中（1,15）→ `True` / `(False,'作业仅在每月 [1, 15] 号执行…')`；**`values` 空列表 → `(True,'')` 静默放行**（同 M6-027）；`monthday=31`（9 月无 31 号）→ `False`（正确）。<br>**🔧修复**：同 M6-027（空值 / 拼错 `mode` 抛 `ValueError`）。 |
| M6-029 | guard 日期范围未填齐 start/end | 报错「需填写开始与结束日期」 | `UI` + `CODE` | 无 | ✅通过<br>原始输出：UI 报错 `请填写日期范围（开始与结束日期）。`。 |
| M6-030 | guard weekday 未选任何 | 报错「请选择至少一个星期」 | `UI` + `CODE` | 无 | ✅通过<br>原始输出：UI 报错 `请选择至少一个星期。`。 |
| M6-031 | guard 条件评估异常（如连接失败） | 作业标记失败（不静默），暴露配置问题 | `CODE` | 无 | ✅通过<br>原始输出：错误 Guard 直接暴露 `作业执行条件评估失败：不支持的作业执行条件类型：bad_guard`，未静默成功。 |

---

## 模块 M7 · 定时任务（Schedule / TIME）

| 编号 | 操作路径 | 预期结果 | 验证方式 | 前置条件 | 验收结果 |
|------|----------|----------|----------|----------|----------|
| M7-001 | `GET /api/schedules` | 返回全部调度（含 `nextRunAt/lastRunAt/lastStatus/enabled`） | `API` + `UI` | 无 | ✅通过<br>原始输出：`GET /api/schedules` 返回 `nextRunAt/lastRunAt/lastStatus/enabled` 等字段；页面显示调度列表。 |
| M7-002 | 「新增任务」→ 选子任务类型(导入/导出/查询/作业) → 添加步骤 | 步骤选择器加载对应类型可用任务 | `UI` | 已存任务 | ✅通过<br>原始输出：新增任务页面可切换导入/导出/查询/作业，并分别加载 `qa_import_asset`、`qa_export_asset`、`qa_idempotent`、`qa_multi_job` 等候选。 |
| M7-003 | 选「同步」类型 | disabled，不可选 | `UI` | 无 | ✅通过<br>原始输出：同步 radio 存在但 `disabled=true`。 |
| M7-004 | 定时方式「轮询」(`interval`) amount+unit | `compute_next_run` 计算下次运行时间 | `UT` `test_jobs_schedule.py` | 无 | ✅通过<br>原始输出：interval 规则 `amount=1,unit=seconds` 返回有效下次时间。 |
| M7-005 | 定时方式「定时」daily/weekly/monthly/yearly + time | 各模式 `compute_next_run` 均返回有效下次时间 | `UT` `test_jobs_schedule.py` | 无 | ✅通过<br>原始输出：daily/weekly/monthly/yearly 均返回有效下次时间。 |
| M7-006 | 「定时设置助手」 | 轮询/定时两面板切换，设置后回填 `ruleSummary` | `UI` | 无 | ✅通过<br>原始输出：助手轮询/定时面板可切换；设置 `3 秒` 后表单摘要显示 `每 3 秒轮询`。 |
| M7-007 | 填起止时间 + 规则 → 「确认」（`POST /api/schedules`） | 存入 `_schedules`；`enabled` 时算 `nextRunAt` | `API` + `DB` | 有 job | ✅通过<br>原始输出：保存 `qa_ui_schedule` 成功，`enabled=true` 且 `nextRunAt` 已计算。 |
| M7-008 | 任务名留空 | 报错「请填写任务名称」 | `API` + `CODE` | 无 | ✅通过<br>原始输出：任务名为空返回 `请填写任务名称。`。 |
| M7-009 | 未选 job | 报错「请选择作业」 | `API` + `CODE` | 无 | ✅通过<br>原始输出：未选作业返回 `请选择作业。`。 |
| M7-010 | 「启用任务」（`POST /api/schedules/start`） | `enabled=1, running=0`，重算 `nextRunAt` | `API` + `DB` | 有调度 | ✅通过<br>原始输出：启用后 `enabled=1,running=0`，`nextRunAt` 重新计算。 |
| M7-011 | 「禁用任务」（`POST /api/schedules/pause`） | `enabled=0, nextRunAt=''` | `API` + `DB` | 有启用调度 | ✅通过<br>原始输出：禁用后 `enabled=0,nextRunAt=""`。 |
| M7-012 | 「立即运行」 | 触发执行 + 结果回写 `last_status` | `API` + `CODE` | 有调度 | ✅通过<br>原始输出：点击「立即运行」显示 `成功：作业执行成功：1 个步骤成功，0 个步骤未启用。`，列表 lastStatus=成功。 |
| M7-013 | 「删除任务」（`DELETE /api/schedules?id=…`） | 删 `_schedules`；若指向 `- 自动作业` 载体且无其他引用，一并清理 | `API` + `DB` | 有调度 | ✅通过<br>原始输出：删除调度后 `_schedules` 记录删除；自动作业载体按引用关系清理。 |
| M7-014 | 「查看日志」 | 弹窗显示该调度运行历史 | `UI` + `API`（`/api/job-runs?scheduleId=`） | 有运行历史 | ✅通过<br>原始输出：点击「查看日志」弹出「运行日志」对话框，显示作业状态、起止、耗时和步骤明细。 |
| M7-015 | 调度到期 → 调度线程分发 | `running=1`→后台线程执行→结束重置 `running=0`，更新 `lastRunAt/lastStatus/nextRunAt` | `CODE` + `DB` | 启用的调度 | ✅通过<br>原始输出：到期分发后 `running=0,last_status=成功,next_run_at` 更新。 |
| M7-016 | `logRetentionDays=3` | 超期运行记录自动清理（`prune_job_logs`） | `CODE` | 有旧日志 | ✅通过<br>原始输出：注入 2000 年旧运行记录后 `prune_job_logs(3)` 删除旧 run 和 step。 |
| M7-017 | `emailOnFail` | UI disabled（后续支持），当前不可用 | `UI` | 无 | ✅通过<br>原始输出：`任务失败时发送邮件提醒` checkbox 存在且 disabled。 |
| M7-018 | 调度执行时进程被 kill | 重启后 `recover_interrupted_runs` 重置 `running`，标记残留运行记录 | `CODE` + M0-008 | 制造中断 | ✅通过<br>原始输出：恢复逻辑将僵尸调度 `running=1→0`，运行和步骤标记为失败并写入进程中断消息。 |
| M7-019 | 列表自动刷新（`lastRefreshTime`） | 显示最后刷新时间，可手动刷新 | `UI` | 无 | ✅通过<br>原始输出：页面显示 `刷新时间 · 11:07:04` 等时间戳；自动轮询持续刷新列表。 |

---

## 模块 M8 · 占位模块（Sync / API / Docs / Feedback）

| 编号 | 操作路径 | 预期结果 | 验证方式 | 前置条件 | 验收结果 |
|------|----------|----------|----------|----------|----------|
| M8-001 | 打开 `/sync.html` | 渲染占位页：「此模块已拆分为独立页面…」+ 模块说明 | `UI` | 无 | ✅通过<br>原始输出：`/sync.html` 页面渲染同步占位页，显示模块说明及 `此模块已拆分为独立页面...`。 |
| M8-002 | 打开 `/api.html` | 同上占位 | `UI` | 无 | ✅通过<br>原始输出：`/api.html` 页面渲染 API 占位页及模块说明。 |
| M8-003 | 打开 `/docs.html` | 同上占位 | `UI` | 无 | ✅通过<br>原始输出：`/docs.html` 页面渲染操作手册占位页及模块说明。 |
| M8-004 | 打开 `/feedback.html` | 同上占位 | `UI` | 无 | ✅通过<br>原始输出：`/feedback.html` 页面渲染咨询建议反馈占位页及模块说明。 |
| M8-005 | 占位页侧边栏版本号 | 异步拉取 `/api/meta` 显示 `v1.4.0` | `UI` + `CODE`（`module-pages.js`） | 服务运行 | ✅通过<br>原始输出：四个页面均显示 `数据导表工具 v1.4.0`；`module-pages.js` 通过 `/api/meta` 异步加载版本号。 |
| M8-006 | 作业中选 sync 步骤 | 保存时报错「同步模块尚未开放」 | M6-018 | 无 | ✅通过<br>原始输出：保存含 sync 步骤的作业返回 `同步模块尚未开放。`。 |

---

## 模块 M9 · 安全与横切

| 编号 | 操作路径 | 预期结果 | 验证方式 | 前置条件 | 验收结果 |
|------|----------|----------|----------|----------|----------|
| M9-001 | 保存连接 → 查 `_db_connections.password` | 密码以 `fernet:` 前缀密文存储，非明文 | `DB` + `CODE` | 无 | ✅通过<br>原始输出：SQLite password 列前缀 `fernet:`，非明文；解密回读 `s3cret`。 |
| M9-002 | 旧版 `b64:` 前缀密码 → 重启 | `_migrate_legacy_secrets_once` 自动迁移为 Fernet 加密 | `CODE` + `DB` | 有旧密码 | ✅通过<br>原始输出：迁移输出 `连接密码 1 条，作业快照 1 条，错误 0 条`；旧值均转为 `fernet:`。 |
| M9-003 | 日志中含 `dbPassword=xxx` 查询参数 | `redact_log_text` 替换为 `***` | `CODE` | 无 | ✅通过<br>原始输出：`dbPassword=secret123` 被替换为 `dbPassword=***`。<br>**复核补充（脱敏矩阵）**：`dbPasswordSecret=…` → `***` ✓；`DBPASSWORD=UpperCase` → `***` ✓（大小写不敏感）；`password=p%40ss` → `***` ✓；非白名单 `dbPasswd=nottracked` 与路径里的 `password=` → **原样**（符合设计）。<br>🔁**🔧已修缺陷（D6）**：原正则 `([?&](?:dbPassword\|dbPasswordSecret\|password)=)[^&#\s"']*` 在 `"` 处截断 → `?dbPassword="quoted"` 输出 `dbPassword=***"quoted"`（**引号内值仍可见**）。修复后引号（单/双）包裹值 → 完整 `***`。**残留 N6**：`?dbPassword='a b'&z=1` → `*** b'&z=1`（含空格时仍截断），见 M12-012。 |
| M9-004 | `GET /api/connections` 返回 | 不含密码明文（`connection_public` 脱敏） | M1-001 | 无 | ✅通过<br>原始输出：`connection_public()` 返回字段不含 `password`，仅含 `hasPassword`。 |
| M9-005 | SQLite datetime 适配器注册 | 写入 `dt.datetime`/`dt.date` 不触发 Python 3.12 弃用告警 | `UT` `test_p3_fixes.py::test_p3_27_sqlite_*`（告警升级为 error 仍通过） | 无 | ✅通过<br>原始输出（v1）：`1 passed, 2 skipped`；"MySQL 专属用例因服务不可用跳过"。<br>🔁**复核修订（E3）**：实测 `pytest test_p3_fixes.py -v` → **`3 passed in 0.62s`（0 skipped）** —— 含 `test_p3_27_mysql_import_time_column_is_datetime`、`test_p3_19_test_connection_filters_system_databases` **均真实执行且通过**。<br>结论**仍为 ✅通过**，但**证据字符串与"因服务不可用跳过"的表述已订正**（v1 与 M11-006 同源错误）。 |
| M9-006 | 静态文件路径穿越攻击（`/../etc/passwd`） | `translate_path` 限制在 `public/` 内，返回 `__missing__` | `CODE` + `API` | 无 | ✅通过<br>原始输出：`GET /../etc/passwd` 返回 HTTP 404，未读取系统文件。 |

---

## 模块 M10 · 脚本与打包

| 编号 | 操作路径 | 预期结果 | 验证方式 | 前置条件 | 验收结果 |
|------|----------|----------|----------|----------|----------|
| M10-001 | `python scripts/create_data_backup.py` | 生成 `deployment-data/data-converter-backup-<timestamp>.zip`，只含 `imports.db` | `FILE` | 有 data 目录 | ✅通过<br>原始输出：隔离副本生成 `data-converter-backup-20260914-110447.zip`；内容仅 `manifest.json`、`data/imports.db`。 |
| M10-002 | 同上 + `--include-uploads --include-exports` | 备份含 uploads + exports | `FILE` | 无 | ✅通过<br>原始输出：带选项备份包含 `uploads/sample.txt`、`exports/sample.csv`。 |
| M10-003 | `python scripts/restore_data_backup.py <zip>` | 恢复 `imports.db`（及可选上传/导出）到 `DATA_DIR` | `DB` | 有备份包 | ✅通过<br>原始输出：恢复后 SQLite 查询得到 `backup-smoke`，上传和导出文件均恢复；恶意 `../escaped.txt` 被拒绝并返回 `Unsafe archive path`。 |
| M10-004 | `python scripts/migrate_password_encryption.py` | 迁移旧密码加密格式 | `CODE` | 有旧数据 | ✅通过<br>原始输出：`迁移完成：连接密码 1 条，作业快照 0 条，错误 0 条。`；password 前缀为 `fernet:`。 |
| M10-005 | `python scripts/watchdog_server.py` | 看门狗进程守护主服务 | `CODE`+`PROC` | Windows 桌面 | 🔁**复核修订：⏭️跳过 → ⚠️部分通过（「守护」语义不成立）**<br>原始输出（v1）："脚本启动分支依赖 Windows `.venv/Scripts/python.exe`、Windows 守护进程标志；当前 Linux 环境不执行伪造验证。"<br>**补跑实测（通过部分）**：`EXIT_CODE=0`；`data/watchdog.log` 新增 `[2026-09-14 12:38:29] 检测到服务未运行，正在拉起...` / `[12:38:31] 服务已成功启动并监听。`；`netstat` → `TCP 127.0.0.1:51978 … LISTENING 26588`；`/api/meta` → `{"ok":true,"appVersion":"1.4.0"}`。**幂等复跑** → `EXIT_CODE=0`、`[12:38:33] 服务已在运行，跳过启动。`、监听 PID 仍 26588（未新增进程）。<br>**反证（决定性实验，W2）**：`taskkill /F /PID 26588` 后 **10 秒内无人拉起**（51978 持续无监听、`watchdog.log` 行数 19→19 不变、`/api/meta` 不可达）→ 脚本 `main()` 实为 `is_running() → start_server() → 最多等 20 s → return`，**无任何循环**；自启文件 `DataToolWatchdog.vbs` 也仅在**用户登录时触发一次**。<br>**结论（v2 时）**：它是“**登录时一次性拉起器**”，**不是守护进程** —— 登录后被杀的进程不会自动恢复；v2 判 ⚠️部分通过。<br>🔁**v3 二次修订：⚠️部分通过 → ✅通过（W2~W5 已修复）**<br>**v3 修复内容**：① **W2** 改为**常驻守护**（循环检测 + 自动拉起 + 间隔 sleep），并支持 `data/watchdog.stop` 标志文件体面退出；② **W5** 增加 Windows 命名互斥体单实例锁；③ **W3** docstring 文件名订正为 `DataToolWatchdog.vbs`；④ **W4** `is_running()` 在 TCP connect 之外追加 `GET /api/ping` 响应校验。<br>**改后实测（QA 独立复跑，含正对照）**：kill 掉 `server.py` 后**自动重新拉起**（新监听 PID 出现）；第二个守护启动**即自行退出 rc=0**、不产生第二个 server；伪服务（HTTP 200 + `ok:false`）与纯 TCP 监听占端口时 `is_running()` 均返回 **False**（正对照：假 `ok:true` 必须判 True）→ W4 不再误判。 |
| M10-006 | `python desktop_launcher.py` | 启动桌面版（拉起 server + 浏览器） | `CODE` | 桌面环境 | ✅通过<br>原始输出：隔离启动 `desktop_launcher.py` 后 `http://127.0.0.1:18866/api/ping` 返回 `ok=true`，日志输出服务已启动。 |
| M10-007 | `scripts/build_windows.ps1` + `DataConverterTool.spec` | PyInstaller 打包为 Windows exe | `CODE`+`FILE` | Windows + PyInstaller | 🔁**复核修订：⏭️跳过 → ⚠️部分通过（打包产物 ✅通过；脚本本身 ❌会失败）** → 🔁**v3 二次修订：⚠️部分通过 → ✅通过（W1 已修复，脚本真跑 rc=0）**<br>原始输出（v1）："当前无 `pwsh/powershell` 和 PyInstaller，Linux 无法真实执行 Windows EXE 打包。"<br>**A. 打包（✅通过，主理人独立实测）**：`& .venv\Scripts\python.exe -m PyInstaller --clean --noconfirm DataConverterTool.spec` → `EXIT=0`、耗时 44.9 s、`dist\DataConverterTool\DataConverterTool.exe` = **10,229,817 字节（约 10.2 MB）**、`dist\` 合计 82 MB（onedir）。**冒烟实测**：启动 exe（`PORT=51999`）→ 8.9 s 后监听；`/api/ping` → `{"ok":true,"version":"export-v2-download"}`（**v3 起该字段已改为 `1.4.0`，见 M12-013**）；`/api/meta` → `appVersion 1.4.0`；`GET /` → 200 / 18296 字节含「数据导表工具」；运行期自建 data/uploads/exports/logs 全 True。告警仅 `pysqlite2` / `MySQLdb` / `mx.DateTime` 三个可选驱动缺失（无害）。<br>**B. 脚本本身失败（❌，W1）**：`& scripts\build_windows.ps1` 在 **Windows PowerShell 5.1**（`5.1.26100.9444`）下于打包步中断 —— `PIPELINE_FAILED: … INFO: PyInstaller: 6.22.3`、`LASTEXITCODE=-1`、耗时 178.8 s，`dist\` / `build\` **均未生成**。根因：脚本首行 `$ErrorActionPreference = "Stop"` 把 PyInstaller 的 **stderr** 升级为 `NativeCommandError` 终止错误；且脚本**从不检查 `$LASTEXITCODE`**（失败被静默吞掉 + 留下旧 exe 假绿 = 更严重的无条件缺陷）。<br>🔧**v3 已修复（W1）**：新增 `Invoke-Native` 包装 —— 先把 `$ErrorActionPreference` 临时降为 `Continue` 再调原生命令、逐行 tee 到日志、调用后**显式检查 `$LASTEXITCODE`，非 0 即 `throw` 并指向日志**；构建前清旧产物，产物做「存在 且 ≥1MB」校验。<br>**v3 改后实测**：`powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1` → **rc=0**，`dist\DataConverterTool\DataConverterTool.exe` = **10,241,045 字节（9.77 MB）**；exe 冒烟 8.6 s 就绪、两接口四字段均 `1.4.0`。**失败路径亦实测**：注入必失败步骤 → rc=1、旧产物 SHA256 逐字节未变（`diff_count=0`）、无 `.prev` 残留。⚠️ 注：`… 2>&1 \| Tee-Object` 这个“直觉改法”在 PS 5.1 下**反而更糟**（会把成功也判失败，实测成功时 rc 报 0、失败报 -1）—— 正确做法是降 EAP + 显式查 `$LASTEXITCODE`。<br>**C. v1 跳过理由不准**：本机**有** `powershell.exe`(5.1)，只缺 pwsh(7)；PyInstaller 可由脚本自身 `pip install -r requirements-packaging.txt` 装上（实测装到 6.22.3）。 |
| M10-008 | `Dockerfile` 构建 + 运行 | 容器化部署可用 | `CODE` | Docker | ✅通过<br>原始输出：`docker build` 完成；容器启动后 `/api/ping` 返回 `{"ok": true, "version": "export-v2-download"}`（该值为 v3 之前的输出；v3 起 `version` 与 `appVersion` 统一为 `1.4.0`，见 M12-013）。 |
| M10-009 | `render.yaml` / `railway.json` | 云平台 Blueprint 配置正确 | `CODE` | 无 | ✅通过<br>原始输出：`railway.json` JSON 解析成功，healthcheck=`/api/ping`；`render.yaml` YAML 解析成功，Dockerfile 路径正确。 |

---

## 模块 M11 · 单元测试套件回归

| 编号 | 操作路径 | 预期结果 | 验证方式 | 前置条件 | 验收结果 |
|------|----------|----------|----------|----------|----------|
| M11-001 | `python test_import_engine.py` | 输出 `import engine checks passed` | `UT` | 无 | ✅通过<br>原始输出：`import engine checks passed`；退出码 0。 |
| M11-002 | `python test_import_feature_matrix.py` | 输出 11 项全 `ok` 的 JSON | `UT` | 无 | ✅通过<br>原始输出：11 个矩阵项目均为 `ok`，`query_export` 生成 xlsx；退出码 0。<br>🔁**复核订正（E4）**：实测 JSON **顶层键数 = 12**，其中**值为 `ok` 的键 = 10 个**，另含 `after_each_sql`（列表 ×2）与 `query_export`（xlsx 绝对路径）。→ 正确表述应为「**10 个检查项全 `ok`**，另输出 `after_each_sql` 与 `query_export` 路径（**共 12 个键**）」。结论仍 ✅通过，**数字已订正**。 |
| M11-003 | `python test_export_engine.py` | 输出 `export engine checks passed` | `UT` | 无 | ✅通过<br>原始输出：`export engine checks passed`；退出码 0。 |
| M11-004 | `python test_jobs_schedule.py` | 输出 `jobs schedule checks passed` | `UT` | 无 | ✅通过<br>原始输出：`jobs schedule checks passed`；退出码 0。 |
| M11-005 | `python -m pytest test_p2_fixes.py -v` | P2 修复全绿（MySQL 用例不可连时 skip） | `UT` | 无 | 🔁**复核修订：⏭️跳过 → ✅通过（E1）**<br>原始输出（v1）：`5 passed, 2 skipped in 1.05s`；以"MySQL 用例跳过，不计入通过"为由**把整条**标 ⏭️跳过。<br>**复核实测**：`collected 7 items` → **`7 passed in 0.84s`（0 skipped）**，7 条逐条 PASSED（含 `test_p2_7_mysql_small_table_uses_exact_count`、`test_p2_13_comment_as_filename_mysql`）。<br>**v1 标记错误（另见 E17 内部矛盾）**：① 批次本身**不是** MySQL 用例，批次内既有 MySQL 也有 SQLite 用例 → 把整批标跳过**违反文档自己定的规则**；② v1 在 M2-048/049/050、M3-002/023、M9-005 处**又用同一条 pytest 命令的 PASSED 作为 ✅通过证据** → **自相矛盾**。<br>**正确标记**：按其规则应为「✅通过（批次 5 passed；2 条 MySQL 用例跳过）」= **"通过 + 部分子用例跳过"**，而非整条跳过；本机实测为 `7 passed / 0 skipped`。 |
| M11-006 | `python -m pytest test_p3_fixes.py -v` | P3 修复全绿（MySQL 用例不可连时 skip） | `UT` | 无 | 🔁**复核修订：⏭️跳过 → ✅通过（E2）**<br>原始输出（v1）：`1 passed, 2 skipped in 0.99s`；以同样理由把整条标跳过。<br>**复核实测**：`collected 3 items` → **`3 passed in 0.62s`（0 skipped）**，含 `test_p3_27_mysql_import_time_column_is_datetime`（MySQL 端列类型 = `datetime` ✓）与 `test_p3_19_test_connection_filters_system_databases`（系统库已过滤 ✓）**均真实执行且通过**。<br>**v1 标记错误同 E1 / E17**，已按"通过 + 部分子用例跳过"口径订正。 |

---

## 模块 M12 · 复核补充项（v2 新增）

> **本模块不计入 M0～M11 的 230 条统计**。收录独立复核（`验收文档复核-QA报告.md` §5.2 / §5.3、`修复回归-QA报告.md`、`Windows补跑-结论与回填建议.md` §5）**新发现、而 v1 文档无对应编号**的条目。
> 状态标记：`🔧已修复` / `✅已落地` / `⚠️部分` / `❌未修`。

| 编号 | 项目 | 来源 | 证据 | 状态 |
|------|------|------|------|------|
| M12-001 | **多查询预览只覆盖第 1 条**（`preview_export_job` 取 `items[0]`），与导出行为（正确产出 2 文件）**不一致** | 遗漏 G1 | `server.py:2906-2911`；multi 载荷 200 但 `sourceName=q_1`、只返回第一个结果集 | 🔧**已修复（v5）**：`preview_export_job`（`server.py:3424` 起）改为**逐 item 真实预览**——对 `item_list` 每个 item 各自解析并取数，**每个 item 独立受 `MAX_PREVIEW_ROWS`（=20）限制**（互不抢占内存），返回新增字段 **`previews: [{sourceName, columns, rows, ok, error}]**；**某个 item 执行失败（如表不存在）只返回该 item 的错误占位，其余照常返回**，不再整请求 500。顶层 `sourceName` / `columns` / `rows` **保留且 = 第 1 个 item**（向后兼容旧调用方），`itemCount` / `sourceNames` 语义不变。前端 `public/export.js`（`:805` 起）改为 **`renderPreviewTables()` 多结果集逐块渲染**，并**删除「仅预览第 1 条」提示**（前提已不成立）。<br>**实测（API）**：3 item（含中间一个坏 SQL）→ `previews` 3 条、顺序一致、`bad` 项 `ok=false` 带 `error`、其余两项正常且列数 2 vs 3 各不相同；**全失败**时请求不崩、返回 2 条占位；单 item / 旧 `table` 分支兼容；**多 item 导出仍产出 2 文件**（未回归）。<br>**实测（UI）**：页面上出现 **2 个结果集块**（列头 7 列 vs 22 列，可证不同源），meta 由「仅预览第 1 条」变为「共 2 条查询 · 合计预览 40 行」，`pageerror` = 0。<br>**独立复核**：20 项边界断言 **19 PASS**；10 个 item 响应 **0.03 s**。⚠️ 遗留（非功能）：`.preview-block` 未加分隔样式（`public/styles.css` 属他人 WIP，未改动） |
| M12-002 | **Guard `weekday` 空值 / 拼错 `mode` 静默放行**（返回 `(True,'')`，作业天天执行） | 遗漏 G2、缺陷 D4 | 39 行真值表实测（`values=[]` → `(True,'')`） | 🔧**已修复**：`evaluate_job_guard`（`server.py:3788`）对空值 / 拼错 `mode` 抛 `ValueError` |
| M12-003 | **Guard `monthday` 空值静默放行**；`mode` **未知**时同样静默放行 | 遗漏 G3、缺陷 D4 | 同上；`mode 未知 → (True,'')` | 🔧**已修复**（同 M12-002） |
| M12-004 | **`redact_log_text` 对引号包裹的值脱敏不彻底**（`dbPassword="quoted"` → `***"quoted"`） | 遗漏 G4、缺陷 D6 | 脱敏矩阵 | 🔧**已修复**：正则去引号排除（`server.py:4203`），单/双引号包裹值完整替换为 `***` |
| M12-005 | 验收对象的**代码版本未记录**（HEAD? 含工作区改动?） | 遗漏 G5、强度 S5 | `git status --short` 大量未提交改动 | ✅**已落地**：见「环境指纹登记」 |
| M12-006 | 文档**无「跳过项复核触发条件」** | 遗漏 G6 | v1 的 19 条跳过中至少 16 条本机可验 | ✅**已落地**：见「复核触发条件」+「待补验清单」 |
| M12-007 | `tables` / `query` 页硬编码 `targetDbType="mysql"`（不支持本地 SQLite）这一**产品限制未如实标注** | 遗漏 G7、缺陷 D7 | `tables.js:31`、`query.js:78` | ✅**已落地**：见「产品限制」节 |
| M12-008 | **`scripts/build_windows.ps1` 在 PS 5.1 下必失败**（`$ErrorActionPreference="Stop"` 把 PyInstaller 的 stderr 升级为终止错误；且**从不检查 `$LASTEXITCODE`** → 失败被静默吞掉、留下旧 exe 假绿） | 补跑 W1 | 见 M10-007 的 B 段 | 🔧**已修复**（v3）：新增 `Invoke-Native`（临时降 EAP → 逐行 tee → **显式查 `$LASTEXITCODE`，非 0 即 `throw` 并指向日志**）+ 构建前清旧产物 + 产物「存在且 ≥1MB」校验。**实测 rc=0、exe 10,241,045 字节**；失败路径亦实测 rc=1 且旧产物 SHA256 逐字节未变。（原建议的 `2>&1 \| Tee-Object` 在 PS 5.1 下会让**成功也判失败**，已弃用） |
| M12-009 | `watchdog_server.py` 四连：**①「守护」语义不成立（W2）；②无单实例锁，并发会各拉起一个 `server.py`（W5）；③docstring 文件名错：写 `DataTool服务守护.vbs`，实际为 `DataToolWatchdog.vbs`（W3）；④`is_running()` 仅 TCP connect，端口被他人占用会误判"已在运行"（W4）** | 补跑 W2～W5 | 见 M10-005 | 🔧**已修复**（v3）：①改为**常驻守护**循环（检测 + 拉起 + 间隔 sleep）+ `data/watchdog.stop` 体面退出；②加 Windows 命名互斥体单实例锁；③docstring 订正为 `DataToolWatchdog.vbs`；④connect 后追加 `GET /api/ping` 响应校验。**实测**：kill 后自动重新拉起、第二实例立即退出 rc=0、伪服务（HTTP 200+`ok:false`）与纯 TCP 监听占端口均判 **False**（含正对照） |
| M12-010 | `native_pick_path()` folder 分支 **`initial` 参数未接线**（从未调用 `resolve_initial_dir`） | 补跑 W6 | 见 M3-033 勘误 (2) | 🔧**已修复**（v3，**换实现方案**）：8 组矩阵实验（发给顶层 `#32770` / 回调 hwnd / root × pidl / 宽字符串 × 带不带 `BIF_NEWDIALOGSTYLE`）证明 `BFFM_SETSELECTIONW` 在本机（Win11 10.0.26200）**完全不生效**——回调通路正常（确收 `BFFM_INITIALIZED`）但 `SendMessage` 恒返 0、Edit 文本不动；故改用 `IFileDialog` + `FOS_PICKFOLDERS` + `SetFolder`（纯 ctypes COM）。**实测两个不同目标目录均返回到位、地址栏可见目标路径**；`handle_export_choose_target` 与 file 模式分支 **byte-identical**。⚠️ 换实现后 folder 模式**窗口标题不再是「浏览文件夹」** |
| M12-011 | `handle_export_choose_target()` **阻塞 HTTP 线程直到对话框关闭，无超时兜底** | 补跑 W7 | QA 实测 `FOLDER` 一次客户端 15 s 超时、服务端遗留窗口 | 🔧**已修复（v4）**：改为「**同步弹框 + 超时看门狗**」——新增 `NATIVE_DIALOG_TIMEOUT`（`server.py:2957`，默认 120 s，可用环境变量覆盖）；弹框前拍 `#32770` **基线快照**，daemon 看门狗到点后 `PostMessage(WM_CLOSE)` 强制关闭**基线之后新出现**的窗口，使 `Show()` 返回 `ERROR_CANCELLED` → 请求以 `cancelled:true` 正常结束；成功路径 `finally: stop.set()` 后看门狗不再枚举，**不误关后续窗口**。**API 形态与前端 `export.js` 零改动**。<br>**QA 独立回归（4/4 通过）**：① 不挂死 `elapsed=4.37s`（`NATIVE_DIALOG_TIMEOUT=4`）；② 清理后残留本请求 `#32770=[]`；③ 成功 `path==initial`、取消 `cancelled:true`、非法 `mode`→400；④ **M12-010 防回归**：`path` 精确等于 `initial(alpha/beta)`，证明 `IFileDialog.SetFolder` 仍生效、未回退 `SHBrowseForFolderW`。另测：并发 2 请求无残留、连续 3 次无泄漏（`/api/meta` 200）。<br>⚠️ **关键实现约束**：本机 `IFileDialog` 会把 UI **代理到另一进程**（实测对话框 PID ≠ 服务进程 PID），故**按 PID 过滤永远匹配不到**，只能用「基线对比」；依旧**只按类名 `#32770` 匹配、绝不按标题关键词过滤**（历史上曾误关用户 Chrome 窗口）。<br>⚠️ **已登记残留风险**：看门狗关的是「基线后新出现的**任意** `#32770`」，**不限定本请求**——QA 量化确认存在误伤（基线后新窗口被关 1/1、基线前预存窗口存活 1/1）；本机单用户桌面场景影响极低，但**并发多对话框时理论上会交叉关窗** |
| M12-012 | **脱敏残留 N6**：`?dbPassword='a b'&z=1` → `*** b'&z=1`（引号内**含空格**时值未完整脱敏，剩余部分连同后续参数一并漏出） | 修复回归 N6 | D6 复跑 | 🔧**已修复**（v3）：**样本 FAIL 9/14 → 0/16**，覆盖引号含空格、引号含 `&`、单/双引号、无引号、多敏感参数串联、值出现在串中间；均完整脱敏且**不吞掉后续合法参数**，原有用例不回退。（QA 另注：不覆盖 JSON body，但当前唯一调用点只记请求行，**无暴露路径**，记为轻微） |
| M12-013 | **双版本标识不一致**：`/api/ping` → `export-v2-download`，`/api/meta` → `1.4.0` | 补跑 W8 | 见 M10-007 的 A 段 | 🔧**已修复**（v3）：两接口统一返回 `appVersion` + `version`，取值均为 `APP_VERSION`。**实测四字段全 `1.4.0`**（含打包 exe 内：冒烟 8.6 s 就绪返回同值）；QA 核查仓内旧串 `export-v2-download` 引用 **0 处**、唯一消费者 watchdog 只判 `ok` → **无兼容风险** |
| M12-014 | **3 个测试文件的 `def test_*` 数量为 0，pytest 从未收集**（`test_import_engine.py` / `test_import_feature_matrix.py` / `test_jobs_schedule.py`）→ 直接 `python xx.py` 有输出，但 `pytest` 收集数为 0，**"绿色"是假象** | 修复回归 **D8** | `pytest --collect-only` 计数 = 0 | 🔧**已修复**（v3）：断言拆为独立 `def test_*`，导入流程抽成带缓存的 `run_pipeline()` / `run_matrix()` / `run_checks()`，保留 `if __name__ == "__main__"` 兼容原用法。**收集数 0→27（6+13+8）；全量 `41 passed / 1 skipped`（rc=0）**；QA 复核：**无** `assert True` 式水分用例、**无** `except` 吞失败、三个 `__main__` 入口仍可跑 |
| M12-015 | **大写列名 + `fieldCase=lower` → `1060 Duplicate column` 打穿 `autoExpand`** | 修复回归 **N3（既有缺陷）** | 非本次引入 | 🔧**已修复**（v3）：新增 `target_existing_column_keys()` 以**小写集合**判重（MySQL 列名大小写不敏感，而 `information_schema.column_name` 返回建表时大小写 → 原用精确比较必误判）；`ALTER` 时仍用原始大小写列名，**绝不改用户列名**。**实测**：1060 消失、`NAME`/`CITY` 保持大写、`NAME` varchar(5)→(24) 且 24 字符完整读回。QA 判定：MySQL 物理上不能并存 `NAME`/`name`，故**不丢数据**（改前为硬失败，改后为可用合并） |
| M12-016 | **连接式（MySQL）导入/导出真实覆盖仍不完整**（`test_mysql_export_if_available` 已改为诚实 `skip`，但默认隔离环境仍不覆盖） | 缺陷 D5 残留 | 见 M2-058 | 🔧**已修复（v5）**：与 M2-058 同根因、同一次修复。测试不再因隔离库 `_db_connections` 为空而 `skip`，改为**用产品自身落库路径**注入一条真实可用的 MySQL 连接后，以 `connectionId` 真实执行导入/导出并做实质断言。**`SKIPPED → PASSED`**；全量 `42 passed / 0 skipped / 0 failed`；mutation 三例证明非假绿。**连接式路径至此首次获得真实覆盖** |
| M12-017 | **表格格式缺陷（v1 遗留）**：**M0 / M1 / M2（7 小节）/ M4 / M5 / M6 / M6-A / M7 / M8 / M9 / M10 共 17 张表**的表头只有 5 列（缺「验收结果」列），而数据行为 6 格 → 渲染时列错位；另 M2-010 行内 `` `\|` `` 未转义 | v2 自查 | 表格列数体检（Python 脚本逐块统计 `\|` 计数 + 分隔行列数单列检查） | 🔧**已修复**：17 张表头补第 6 列「验收结果」、对应分隔行同步补 1 组；M2-010 竖线转义为 `` `\|` ``。**复核确认**：M0～M11 的 23 张条目表表头均 6 列、全表列数一致（M12 表为 5 列结构，另计；分隔行已纳入校验）。（注：M3 的 5 张表与 M11 原有表头本已 6 列，不在缺陷范围内） |
| M12-018 | **构建脚本会毁掉上一版可用产物**：`build_windows.ps1` 在调用 PyInstaller **之前**就 `Remove-Item dist\DataConverterTool` → 任何构建失败 = 上一版可运行 exe 被销毁且**无回滚**（v3 会话中真实发生：沙箱配额耗尽致末次构建失败，exe 被清空，被迫手工重跑恢复） | **QA v3 回归新发现（一般级）** | QA 报告「新发现问题·一般级」；失败路径实测 sha256 `diff_count=0` | 🔧**已修复**（v3）：改为「**先构建到 `dist\.staging` → 校验 exe 存在且 ≥1MB → 旧目录 rename 为 `.prev` → 原子替换，失败自动回滚**」。**失败路径实测**：rc=1 且旧产物 SHA256 **逐字节未变**（`diff_count=0`）、无 `.prev` 残留。遗留：失败后可能留一个**空** `.staging` 目录（不影响功能，已在报告风险项注明） |
| M12-019 | **回滚守卫过窄 + 假 green `"restored"`**：`build_windows.ps1` 的「让开 → 提升 → 回位」三段守卫在真实失败形态下失效——(2) 守卫 `!(Test-Path $AppDir)` 恒为 False（PowerShell `Move-Item <目录> <不存在目标>` 是**先建目标目录再搬子项**，半途撞锁时 `$AppDir` 已存在为空）→ 整段回滚被跳过、产品路径成空目录、旧产物滞留 `.prev` **且无任何提示**；(3) 让开那步 `-EA SilentlyContinue` 且**不复核结果** → 让开失败时回位 `Move-Item` 因目标已存在而**嵌套**成 `dist\DataConverterTool\.prev-DataConverterTool\`，却仍打印 **`"restored"`（假绿）** | **v3 加固期主理人逐行审查新发现**（非 v3 已登记条目） | 9 场景 harness `_fix4_buildtrap.py` 全通过，含 **P3 A/B 对照**：加固前 `nested=True` + 旧产物 0/5 在产品路径 + 假 `"restored"`；加固后 `nested=False` + 5/5 **逐字节**回位 | 🔧**已修复（v4 登记）**：改为把「**让开是否真的成功**」当分派条件（`if (!(Test-Path $AppDir))` 走 rename，否则退化到 merge 兜底），`$restored` **仅在旧产物真正回位时**置真。<br>**实测**：`scripts/build_windows.ps1` sha256 `7413d16b…a00ae63b`、11,630 字节、`PSParser::ParseFile` **0 错误**、`nonASCII=0`、CRLF 238 / loneLF 0；9 场景 harness 全绿。<br>**遗留（不影响功能）**：① 让开失败时会残留 `dist\DataConverterTool.partial`（位于 gitignore 的 `dist/` 内）；② `.staging` 容错清理后若仍有被锁文件则保留；③ 「move-aside 在**某子目录内部**半途失败 → 同名子目录同时在两处 → 并回会嵌套 `<dir>/<dir>`」**该路径未触发，理论未验**，留待 QA 对抗性验证 |

---

## 执行统计（v3 改版 · 三口径）

> **v1 统计口径的两个问题**（对应强度不足 S2）：① 把"本轮真跑"与"沿用历史结论"混列；② M3 段整体「沿用此前已完成结果，不重复检查」，导致 4 条结论过时。v2 改为**双口径**并明确标注数据来源。

### 口径一：复核修订后（基准 = **工作区代码**，2026-09-14 独立复核）

| 类别 | 数量 | 说明 |
|------|------:|------|
| ✅通过 | **223** | 含 19 条类别升级为通过 |
| ❌ 不通过 | **3** | M2-028（D1）、M3-006（D3）、M3-020（D2） |
| ⚠️ 部分通过 | **4** | M2-058（D5 证据无效）、M5-007（仅验到跳转层）、M10-005（守护语义不成立）、M10-007（脚本本身失败） |
| ⏭️ 未验证 | **0** | v1 的 19 条跳过**已全部闭合** |
| **合计** | **230** | 与编号总数一致（无缺号、无重复） |

### 口径二：7 缺陷（D1～D7）修复后（2026-09-14，QA 三轮回归通过）

| 类别 | 数量 | 变化 |
|------|------:|------|
| ✅通过 | **226** | M2-028、M3-006、M3-020 由 ❌ → 🔧已修复 → ✅（+3） |
| ❌ 不通过 | **0** | — |
| ⚠️ 部分通过 | **4** | M2-058（D5 残留）、M5-007（未补验）、M10-005（W2 未修）、M10-007（W1 未修） |
| ⏭️ 未验证 | **0** | — |
| **合计** | **230** | — |

### 口径三：M12 待修 7 项修复后（v3 · 2026-09-15；**v5 已更新 —— 全表首次零 ⚠️**）

| 类别 | 数量 | 变化 |
|------|------:|------|
| ✅通过 | **230** | v3：M10-005、M10-007 由 ⚠️部分 → ✅通过（+2）；**v5：M2-058、M5-007 由 ⚠️部分 → ✅通过（+2）** |
| ❌ 不通过 | **0** | — |
| ⚠️ 部分通过 | **0** | **v5 已归零**（原 M2-058、M5-007 两条已闭合） |
| ⏭️ 未验证 | **0** | — |
| **合计** | **230** | — |

> **v3 相对口径二的变化**：M10-005（W2 watchdog 守护语义）与 M10-007（W1 打包脚本）两项的阻塞因素已修复，判定由 ⚠️部分 → ✅通过。**M12 复核补充项中 `❌未修` 已归零**。
> **仍未闭合（v3 时）**：仅剩 2 条 ⚠️部分（M2-058 / M5-007），**均非缺陷** —— M2-058 是“连接式路径在默认隔离环境下零覆盖”，M5-007 是“缺逐项断言”。
> **v4 说明**：M12-011 修复与 M12-019 登记**均属 M12 模块**，而 M12 **不计入 M0～M11 的 230 条统计**（见「模块 M12」开头），故本口径三的三类计数**不变**；M12 模块自身的计数变化见「M12 复核补充项」与「v4 收尾说明」。
> **v5 说明（本版）**：M2-058 与 M5-007 双双闭合 → **⚠️部分 2 → 0、✅通过 228 → 230**，M0～M11 的 230 条**首次全部为 ✅通过**（`❌0 / ⚠️0 / ⏭️0`）。二者仍属 M12 之外的编号，故计入本口径。M12 模块自身：🔧已修复 14 → **16**、⚠️部分 2 → **0**（M12-001 真实修复、M12-016 与 M2-058 同批修复）。
> ⚠️ **仍须保留的诚实声明**：本口径三的 230 条是**判定结果**，不等于"全部由本版实测背书"——数据来源分层（A 本次真跑 / B v1 真跑未复跑 / C 沿用历史结论）**未变**，见「数据来源分层」节。v5 新增的真实执行仅覆盖 M2-058 / M5-007 / M12-001 / M12-016 相关链路。

### 判定修订明细（26 条）

| 修订类型 | 条数 | 编号 |
|------|------:|------|
| ⏭️跳过 → ✅通过 | 15 | M3-003、M3-004、M3-021、M3-022、M3-033、M3-034、M4-005、M5-001、M5-002、M5-003、M5-004、M5-005、M5-006、M11-005、M11-006 |
| ⏭️跳过 → ⚠️部分通过 | 3 | M5-007、M10-005、M10-007 |
| ⏭️跳过 → ❌不通过（🔧已修复） | 1 | M3-020 |
| ❌不通过 → ✅通过 | 4 | M3-016、M3-028、M3-029、M3-036 |
| ✅通过 → ❌不通过（🔧已修复） | 1 | M2-028 |
| ✅通过 → ⚠️部分通过 | 1 | M2-058 |
| ❌不通过 → ❌不通过（**理由重写**，🔧已修复） | 1 | M3-006 |
| **合计** | **26** | 其中"类别发生改变" **25** 条（M3-006 为同类改写） |

> **校验**：跳过项 15 + 3 + 1 = **19** ✅ 与 v1 跳过总数一致；通过项 206 − 1(M2-028) − 1(M2-058) + 15 + 4 = **223** ✅。

### 数据来源分层（防过度解读 · 对应强度不足 S2）

| 分层 | 范围 | 条数 | 本版是否重跑 |
|------|------|-----:|--------------|
| **A. v2 本次真跑**（独立复核 + Windows 补跑 + 缺陷修复回归） | M2-028、M2-058；M3-003/004/006/016/020/021/022/028/029/033/034/036；M4-005；M5-001～007；M6-025～028；M9-003/005；M10-005/007；M11-001～006 | **36** | ✅ 是 |
| **B. v1 本轮真跑、v2 未复跑** | M4（除 005）；M6（除 025～028）；M7（全部）；M8（全部）；M9（除 003/005）；M10（除 005/007） | **75** | ❌ 否 |
| **C. v1 沿用历史结论** | M0、M1（全部）；M2（除 028/058）；M3（除 A 层 12 条） | **119** | ❌ 否 |
| **合计** | — | **230** | — |

> ⚠️ **B / C 层「未重跑 ≠ 已确认无误」** —— 这两层的 ✅ 沿用 v1 结论（v1 对 M0～M2 更是沿用更早文档、且以 `CODE` 类证据为主、执行环境为 Linux 容器），其可靠性**未经本版验证**。
> **A 层之外的条目若要用作发布依据，须先按「复核触发条件」重跑。**
> **v5 补充**：v5 对 A 层中的 **M2-058 / M5-007** 做了**深化验证**——前者由"隔离环境静默跳过"改为**真实连接式跑通**并附 **mutation 反证**；后者补齐**逐项断言**并附**负向反证**。另新增 **M12-001 / M12-016** 的真实执行证据（M12 为独立模块，不计入本表 230 条）。**三层计数不变（36 / 75 / 119）。**

### 未通过项（口径一：3 条）

| 编号 | 缺陷 | 状态 |
|------|------|------|
| M2-028 | D1 `autoExpand` 假开关（不拓宽既有列，`1406 Data too long`） | 🔧已修复 |
| M3-006 | D3 单条 SQL 含 `;` 报 400 + 多查询预览只覆盖首条 | 🔧已修复（经 2 轮返工） |
| M3-020 | D2 `headerMode=comment` 未实现（导出仍用字段名作表头） | 🔧已修复 |

### 部分通过项（v5：**0 条**）

| 编号 | 未闭合内容 | 去向 |
|------|-----------|------|
| — | **v5 后已无 ⚠️部分通过项** | — |

> **v5：本表已清空。** 原 2 条 —— M2-058（连接式导入/导出真实覆盖）与 M5-007（「选中该表」逐项断言）—— 均于 v5 闭合、判定升级为 ✅通过，且各自附**负向验证**证明有效（M5-007：未选表时同一套断言 4/4 FAIL；M2-058：mutation 三例全部被捕获）。
> v2 时另有 **M10-005**、**M10-007** 两条 ⚠️部分：v3 因缺陷修复（W2 / W1）**已升级为 ✅通过**，见口径三。

### 未验证项（口径一：0 条）

v1 的 19 条 ⏭️跳过**已全部闭合**（15 → ✅通过、3 → ⚠️部分通过、1 → ❌不通过并已修复），明细见上方「判定修订明细」。

## 验收执行优先级建议

| 优先级 | 范围 | 说明 |
|--------|------|------|
| P0 先跑 | M11 全部 | 6 个测试脚本全绿 = 导入/导出/作业/调度核心链路基线可靠 |
| P1 核心 | M0 + M2(001-058) + M3(001-025) + M6(001-021) + M7(001-015) | 起服务 + 导入导出主流程 + 作业调度执行 |
| P2 任务化 | M2(059-066) + M3(042-045) + M4 全部 + M6-A Guard | 任务资产 CRUD + 查询 + 执行条件 |
| P3 边界 | M1 + M5 + M8 + M9 | 连接管理 + 表浏览 + 占位 + 安全 |
| P4 交付 | M10 | 备份恢复 + 打包 |

---

> **v3 收尾说明（历史快照 · 计数已被 v4 取代，见下）**
> 1. **本版回填内容**：M12 待修 7 项（M12-008 / 009 / 010 / 012 / 013 / 014 / 015）**全部修复并过独立回归**；另**新增并修复 M12-018**（构建失败毁旧产物）；**M10-005 / M10-007 由 ⚠️部分通过升级为 ✅通过**；`/api/ping` 版本口径统一为 `1.4.0`。
> 2. **判定标记**：`✅通过` / `❌ 不通过+原因` / `⏭️ 未验证+理由（须注明"环境不可验"或"功能未实现"）` / `⚠️ 部分通过`。
> 3. **M12 复核补充项（v3：共 18 条）** = 🔧已修复 **12**（M12-002/003/004/**008**/**009**/**010**/**012**/**013**/**014**/**015**/017/**018**）+ ✅已落地 **3**（M12-005/006/007）+ ⚠️部分 **3**（M12-001/011/016）+ ❌**未修 0**。
> 4. **待补验清单**：由 v2 的 6 项收敛为 **2 项**（#4 M5-007、#5 M2-058），**均为“证据/环境不足”而非缺陷**。
> 5. **v3 时的未闭合 5 项**：① M12-001 多查询预览仍只覆盖第 1 条；② M12-011 `choose-target` 阻塞 HTTP 线程且无超时兜底；③ M12-016 / M2-058 连接式路径零覆盖；④ M5-007 缺逐项断言；⑤ QA 未能独立核对项（exe 内代码静态不可验、watchdog `--once` “服务未运行”分支、VBS 登录自启、MySQL char/索引受限时的 ALTER 失败提示）。**→ 其中 ② 已于 v4 修复，见下。**
> 6. **引用本文件前**，请先核对「环境指纹登记」中的 `git rev-parse HEAD` 与工作区状态；不一致则受影响的条目必须重跑（见「复核触发条件」）。
> 7. 修订前版本已备份：`acceptance-plan-final.bak-v1.md`（md5 `8d33e19f2640d46b62121d74c8eeb37b`）、`acceptance-plan-final.bak-v2.md`（md5 `a5b68495e2e1eae1904186bf980a9810`），可随时比对。

> **v4 收尾说明（历史快照 · 计数已被 v5 取代，见下）**
> 1. **本版回填内容（2 项）**：① **新增并修复 M12-019**（`build_windows.ps1` 回滚守卫过窄 + 假 green `"restored"`，v3 加固期第 3 轮自查新发现，此前未登记）；② **M12-011 由 ⚠️仅记录（运维风险）升级为 🔧已修复**（同步弹框 + 超时看门狗，`NATIVE_DIALOG_TIMEOUT` 默认 120 s）。
> 2. **M12 复核补充项（v4：共 19 条）** = 🔧已修复 **14**（M12-002/003/004/008/009/010/**011**/012/013/014/015/017/018/**019**）+ ✅已落地 **3**（M12-005/006/007）+ ⚠️部分 **2**（M12-001/016）+ ❌**未修 0**。
> 3. **M0～M11 的 230 条与「口径三」计数不变**（✅ 228 / ⚠️ 2 / ❌ 0）—— M12 为独立模块，不计入该统计。
> 4. **仍未闭合（如实列出，供下一轮输入）**：① **M12-001** 多查询预览仍只覆盖第 1 条（仅加显式提示，**预览行为本身未改**）；② **M12-016 / M2-058** 连接式（MySQL）导入/导出路径在默认隔离环境下零覆盖（**证据/环境不足，非缺陷**）；③ **M5-007** 缺“跳转后是否选中该表”的逐项断言；④ **QA 未能独立核对项**（沿用 v3）：exe 内代码静态不可验、watchdog `--once` “服务未运行”分支、VBS 登录自启、MySQL char/索引受限时的 ALTER 失败提示。
> 5. **已登记残留风险（均已量化，非阻断）**：① **M12-011** 看门狗关的是“基线后新出现的**任意** `#32770`”，**不限定本请求** → QA 实测存在误伤（基线后新窗口被关 1/1、基线前预存窗口存活 1/1），单用户桌面影响极低，并发多对话框理论上会交叉关窗；② **M12-019** 让开失败残留 `dist\DataConverterTool.partial`、`.staging` 被锁文件保留、“子目录内部半途失败 → 并回嵌套”路径**未触发未验**；③ **M12-018** 失败后可能留空 `.staging`。
> 6. **产品限制 4 项（非缺陷、设计如此）**：查询页/表管理页仅支持 MySQL（`query.js:78`、`tables.js:31` 硬编码 `targetDbType:"mysql"`）；连接页多数据库类型待开发；同步模块未开放；失败邮件提醒 `emailOnFail` 未开放（UI checkbox `disabled`）。详见「产品限制」节。
> 7. **验收强度提醒**：「口径三」的 ✅ 228 中，**B 层 75 项 + C 层 119 项 = 194 条沿用 v1 结论、本版未复跑** —— 「未重跑 ≠ 已确认无误」，其可靠性未经本版验证。详见「数据来源分层」。
> 8. **代码状态**：全部修复仍**未提交**，只在工作区（`server.py`、`scripts/*.ps1`、`scripts/watchdog_server.py`、4 个 `test_*.py`）；另有 21 个 `public/*` + `docs/` 属**他人未提交 WIP**，提交时需 **blob 级隔离**，不可整文件 `git add`。

> **v5 收尾说明（本版 · 2026-09-16）**
> 1. **本版回填内容（4 项，收尾最后 4 条 ⚠️部分）**：① **M12-001** ⚠️部分 → **🔧已修复**：`preview_export_job` 改为**逐 item 真实预览**（新增 `previews[]`，每 item 独立受 `MAX_PREVIEW_ROWS=20` 限制、单项失败仅占位不拖垮整请求；顶层旧字段保留 = 第 1 项），前端 `export.js` 改为**多结果集逐块渲染**并**删除「仅预览第 1 条」提示**；② **M12-016** ⚠️部分 → **🔧已修复**、③ **M2-058** ⚠️部分 → **✅通过**（同一次修复：测试改用**产品自身落库路径**注入真实 MySQL 连接后以 `connectionId` 真跑导入+导出）；④ **M5-007** ⚠️部分 → **✅通过**（补「是否选中该表」逐项断言，8/8 PASS）。
> 2. **M12 复核补充项（v5：共 19 条）** = 🔧已修复 **16**（M12-002/003/004/008/009/010/011/012/013/014/015/017/018/019/**001**/**016**）+ ✅已落地 **3**（M12-005/006/007）+ ⚠️部分 **0** + ❌**未修 0**。
> 3. **M0～M11 的 230 条（v5）**：✅**230** / ⚠️**0** / ❌**0** / ⏭️**0** —— **首次全部为 ✅通过**。M12 仍为独立模块，不计入该统计。
> 4. **待补验清单已归零**：原余下 2 项（#4 M5-007、#5 M2-058）本次双双闭合，**7 项全部闭合**。
> 5. **本版证据强度（关键，勿只看 ✅ 数字）**：M5-007 与 M2-058 均附**负向验证**——M5-007 在"未选表"场景下同一套断言 **4/4 FAIL**（排除只打印不比较的假绿）；M2-058 通过 **mutation 测试**（污染 `rowsWritten`、污染导出 `rows`、替换为不存在的 `connectionId`）三例**全部被捕获**。M12-001 经独立探针 **20 项边界断言 19 PASS**（唯一 FAIL 为发现并已清理的一张残留临时表 `qa5_jump`），10 item 响应 0.03 s。
> 6. **仍未闭合/未能验证（如实列出）**：① **M12-001 的 `.preview-block` 未加分隔样式**（`public/styles.css` 属他人 WIP，本轮刻意未改，**纯视觉、不影响功能**）；② **QA 未能独立核对 4 项（沿用 v3/v4）**：exe 内代码静态不可验、watchdog `--once` "服务未运行"分支、VBS 登录自启、MySQL char/索引受限时的 ALTER 失败提示；③ **本次独立复核由主理人执行**（原计划的独立 QA 子代理因**额度 429 限流未能启动**，将于 13:40 后恢复）—— 相关结论的独立性弱于 v3/v4 的独立 QA 轮次，**建议下一轮补一次独立 QA 复核**。
> 7. **已登记残留风险（均已量化，非阻断）**：① **M12-011** 看门狗关的是"基线后新出现的**任意** `#32770`"，**不限定本请求** → 实测存在误伤（基线后新窗口被关 1/1、基线前预存窗口存活 1/1），单用户桌面影响极低，并发多对话框理论上会交叉关窗；② **M12-019** 让开失败残留 `dist\DataConverterTool.partial`、`.staging` 被锁文件保留、"子目录内部半途失败 → 并回嵌套"路径**未触发未验**；③ **M12-018** 失败后可能留空 `.staging`。
> 8. **产品限制 4 项（非缺陷、设计如此，未变）**：查询页/表管理页仅支持 MySQL；连接页多数据库类型待开发；同步模块未开放；失败邮件提醒未开放。详见「产品限制」节。
> 9. **验收强度提醒（未变）**：「口径三」的 ✅ 230 中，**B 层 75 项 + C 层 119 项 = 194 条沿用 v1 结论、本版未复跑** —— 「未重跑 ≠ 已确认无误」。详见「数据来源分层」。
> 10. **代码状态（v5）**：**本轮仅改动 3 个文件** —— `server.py`（`preview_export_job`）、`public/export.js`（多结果集渲染）、`test_export_engine.py`（连接式覆盖），已按修改时间核验，**未误碰他人 WIP**。全部改动仍**未提交**，提交时需 **blob 级隔离**。测试用 MySQL 为 `127.0.0.1:3306 / dc_p2_test`（与文档 v2 登记的 `192.168.7.173` 为同一实例），临时表已全部 drop。
