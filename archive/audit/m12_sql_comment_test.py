# -*- coding: utf-8 -*-
"""M12-0xx：验证查询模块对「带注释/带字面量分号」的只读 SQL 不再误拒。

覆盖与导出模块的行为一致性：
- 正向：前导注释（-- / # / /* */）、字符串内分号、注释内分号、尾分号等均应通过
- 负向：非只读命令、真正多语句、空/纯注释 仍应被正确拒绝
"""
import json
import sys
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = "http://127.0.0.1:51978"
DIRECT = {
    "targetDbType": "mysql", "dbHost": "127.0.0.1", "dbPort": "3306",
    "dbUser": "root", "dbPassword": "123456", "dbName": "dc_p2_test",
}


def q(sql):
    req = urllib.request.Request(
        BASE + "/api/query/run",
        data=json.dumps({**DIRECT, "sql": sql}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, None
    except Exception as e:
        return 0, {"error": "%s: %s" % (type(e).__name__, e)}


POSITIVE = [
    ("纯 SELECT", "select 1 as a"),
    ("行注释 -- + SELECT", "-- 我的查询\nselect 1 as a"),
    ("行注释 # + SELECT", "# 我的查询\nselect 1 as a"),
    ("行注释 -- 带井号 + SELECT", "-- note # inner\nselect 1 as a"),
    ("块注释 + SELECT", "/* 我的查询 */ select 1 as a"),
    ("块注释换行 + SELECT", "/* 我的查询 */\nselect 1 as a"),
    ("多行块注释 + SELECT", "/*\n 多行\n 注释\n*/\nselect 1 as a"),
    ("连续两种注释 + SELECT", "-- a\n/* b */\nselect 1 as a"),
    ("括号包裹 SELECT", "(select 1 as a)"),
    ("WITH", "with t as (select 1 as a) select * from t"),
    ("字符串内分号", "select ';' as a"),
    ("字符串内双分号", "select 'a;b;c' as a"),
    ("注释内分号", "select 1 /* a;b */ as a"),
    ("行注释内分号", "select 1 -- a;b\n"),
    ("尾部行注释", "select 1 as a -- 说明"),
    ("尾分号", "select 1 as a;"),
    ("尾分号 + 尾注释", "select 1 as a; -- tail"),
    ("注释 + 尾分号", "/* c */ select 1 as a;"),
    ("中间块注释", "select /* c */ 1 as a"),
    ("SHOW TABLES", "show tables"),
    ("EXPLAIN", "explain select 1"),
    ("DESCRIBE", "describe dc_p2_test.pm_pk_t"),
]

NEGATIVE = [
    ("非只读 DROP", "drop table if exists __m12never__", "只允许"),
    ("非只读 DELETE", "delete from export_people where 1=0", "只允许"),
    ("非只读 UPDATE", "update export_people set id=id where 1=0", "只允许"),
    ("注释 + 非只读", "-- c\ndrop table if exists __m12never__", "只允许"),
    ("真多语句", "select 1; select 2", "一次只能执行一条"),
    ("真多语句带注释", "/* c */ select 1; select 2", "一次只能执行一条"),
    ("前导注释的真多语句", "-- c\nselect 1;\nselect 2", "一次只能执行一条"),
    ("空", "", "请输入"),
    ("纯空白", "   ", "请输入"),
    ("纯行注释", "-- 只有注释", "请输入"),
    ("纯块注释", "/* 只有注释 */", "请输入"),
    ("纯注释 + 分号", "-- c\n;", "请输入"),
]

print("=" * 78)
print("A. 正向用例（全部应 status=200）")
print("=" * 78)
pos_pass = pos_fail = 0
for label, sql in POSITIVE:
    st, js = q(sql)
    ok = st == 200 and bool((js or {}).get("columns") is not None)
    if ok:
        pos_pass += 1
        cols = (js or {}).get("columns")
        rows = (js or {}).get("rows")
        print("  PASS %-26s cols=%-22s rows=%s" % (label, cols, rows))
    else:
        pos_fail += 1
        err = (js or {}).get("error") if isinstance(js, dict) else js
        print("  FAIL %-26s status=%s error=%s" % (label, st, str(err)[:70]))

print()
print("=" * 78)
print("B. 负向用例（全部应被拒绝且文案匹配）")
print("=" * 78)
neg_pass = neg_fail = 0
for label, sql, expect in NEGATIVE:
    st, js = q(sql)
    err = (js or {}).get("error", "") if isinstance(js, dict) else ""
    ok = st != 200 and expect in str(err)
    if ok:
        neg_pass += 1
        print("  PASS %-24s status=%-3s error=%s" % (label, st, str(err)[:52]))
    else:
        neg_fail += 1
        print("  FAIL %-24s status=%-3s error=%s (期望含「%s」)" % (label, st, str(err)[:52], expect))

print()
print("=" * 78)
print("C. 与导出模块一致性抽查（同一条带注释 SQL 在两边都应可用）")
print("=" * 78)
SAME = [
    ("块注释 + SELECT", "/* c */ select 1 as a"),
    ("行注释 + SELECT", "-- c\nselect 1 as a"),
    ("注释内分号", "select 1 /* a;b */ as a"),
]
for label, sql in SAME:
    st_q, js_q = q(sql)
    req = urllib.request.Request(
        BASE + "/api/export/preview",
        data=json.dumps({**DIRECT, "items": [{"type": "query", "name": "一致性", "sql": sql}]}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            st_e, js_e = r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        st_e, js_e = e.code, json.loads(e.read().decode("utf-8"))
    pv = ((js_e or {}).get("previews") or [{}])[0]
    both = st_q == 200 and st_e == 200 and pv.get("ok")
    print("  %-6s %-20s 查询=%s 导出预览 ok=%s" % ("PASS" if both else "FAIL", label, st_q, pv.get("ok")))

print()
print("=" * 78)
print("正向 %d/%d 通过 | 负向 %d/%d 通过" % (pos_pass, len(POSITIVE), neg_pass, len(NEGATIVE)))
print("=" * 78)
sys.exit(0 if (pos_fail == 0 and neg_fail == 0) else 1)
