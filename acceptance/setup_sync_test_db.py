"""同步模块的测试库夹具（幂等，可反复跑）。

业主 2026-10-08 授权"你可以建立测试库"。这里在本机 MySQL 建两个库：

    dc_sync_src   源库：模拟生产库 lcdp_fe 的真实特征
    dc_sync_dst   目标库：故意造出与源库的差异，用来测字段映射与容错

为什么要照着生产库的特征造，而不是随便建两张干净表：
实测 lcdp_fe（本机有完整副本）后发现它有三个"反常识"特征，直接用干净表测会全部漏掉：

  1. 全库没有任何 `ON UPDATE CURRENT_TIMESTAMP` 的列，也没有"更新时间/修改时间"这类列
     → 不存在"行最后修改时间"，增量同步无法靠它
  2. 核心表没有主键、也没有唯一索引（连 会员小票表 / 云购商城销售数据表 都是）
     → upsert 的"键"只能让用户手工指定业务键
  3. 部分"时间"列是字符串：会员小票表.提单时间 是 varchar(100) 且只到"天"（`2025-01-30`）

用法：
    ./.venv/Scripts/python.exe acceptance/setup_sync_test_db.py
    ./.venv/Scripts/python.exe acceptance/setup_sync_test_db.py --rows 200000   # 造大表测性能
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

import pymysql

HOST = "127.0.0.1"
PORT = 3306
USER = "root"
PASSWORD = "123456"
SRC_DB = "dc_sync_src"
DST_DB = "dc_sync_dst"


def connect(db: str | None = None) -> pymysql.connections.Connection:
    return pymysql.connect(
        host=HOST, port=PORT, user=USER, password=PASSWORD,
        database=db, charset="utf8mb4", autocommit=True,
    )


def create_databases(conn) -> None:
    for name in (SRC_DB, DST_DB):
        conn.cursor().execute(
            f"create database if not exists `{name}` "
            "default character set utf8mb4 collate utf8mb4_general_ci"
        )
    print(f"  库就绪：{SRC_DB} / {DST_DB}")


SRC_DDL = [
    # ---- 1) 标准场景：有主键 + 有 datetime 时间戳列 + 有"会变的列" ----
    ("""
    create table if not exists `订单表` (
        `id`            bigint       not null auto_increment,
        `订单号`        varchar(50)  not null comment '业务键，唯一',
        `商品名称`      varchar(255) null,
        `订单状态`      varchar(50)  null comment '会变：待发货→已发货→已完成',
        `商品退款状态`  varchar(50)  null comment '会变：无→部分退款→已退款',
        `订单创建时间`  datetime     null comment '带时分秒，可做水位；但只是下单时间，不是修改时间',
        `买家手机号`    varchar(20)  null,
        `订单实付金额`  decimal(10,2) null,
        `备注`          text         null,
        primary key (`id`),
        unique key `uk_order_no` (`订单号`),
        key `idx_created` (`订单创建时间`)
    ) engine=InnoDB default charset=utf8mb4
    """, "订单表"),
    # ---- 2) 贴近真实：varchar 日期列（只到天）+ 无主键 + 一行多商品 ----
    ("""
    create table if not exists `小票表` (
        `提单时间`      varchar(100) null comment '只有日期，没有时分秒',
        `提单编号`      varchar(100) null comment '一行多商品 → 会重复，不唯一',
        `渠道`          varchar(100) null,
        `市场`          varchar(100) null,
        `门店名称`      varchar(255) null,
        `消费者手机号`  varchar(20)  null,
        `积分`          decimal(15,2) null,
        `小票金额`      decimal(15,2) null,
        `商品名称`      varchar(255) null,
        `商品编码`      varchar(100) null,
        `数量`          decimal(10,2) null,
        `活动名称`      text         null,
        key `idx_receipt_time` (`提单时间`),
        key `idx_receipt_no` (`提单编号`)
    ) engine=InnoDB default charset=utf8mb4
    """, "小票表"),
    # ---- 3) 边界：空值 / 中文 / 超长 / 小数 / 日期 / 空字符串 ----
    ("""
    create table if not exists `边界表` (
        `id`         int          not null,
        `空值列`     varchar(50)  null,
        `中文列`     varchar(255) null,
        `长文本列`   text         null,
        `小数列`     decimal(18,6) null,
        `日期列`     date         null,
        `布尔列`     tinyint(1)   null,
        `空串列`     varchar(50)  null default '',
        `超长列`     varchar(20)  null comment '造 30 字符，测 autoExpand 扩列',
        primary key (`id`)
    ) engine=InnoDB default charset=utf8mb4
    """, "边界表"),
]

DST_DDL = [
    # 目标表刻意与源表不同：列名大小写不同 + 少一列 + 列顺序不同 → 测字段映射
    ("""
    create table if not exists `订单表` (
        `id`            bigint       not null,
        `OrderNo`       varchar(50)  not null comment '源表叫 订单号，大小写不同',
        `订单创建时间`  datetime     null,
        `商品名称`      varchar(255) null,
        `订单状态`      varchar(50)  null,
        `商品退款状态`  varchar(50)  null,
        `买家手机号`    varchar(20)  null,
        `订单实付金额`  decimal(10,2) null,
        `目标端独有列`  varchar(50)  null comment '目标多一列，源端没有',
        primary key (`id`)
    ) engine=InnoDB default charset=utf8mb4
    """, "订单表"),
    # 目标表用窄列 → 测 autoExpand 自动拓宽
    ("""
    create table if not exists `订单表_窄列` (
        `id`         bigint      not null,
        `订单号`     varchar(10) null comment '故意只有 10，测自动拓宽',
        `商品名称`   varchar(10) null,
        primary key (`id`)
    ) engine=InnoDB default charset=utf8mb4
    """, "订单表_窄列"),
    ("""
    create table if not exists `小票表` (
        `提单时间`      varchar(100) null,
        `提单编号`      varchar(100) null,
        `渠道`          varchar(100) null,
        `市场`          varchar(100) null,
        `门店名称`      varchar(255) null,
        `消费者手机号`  varchar(20)  null,
        `积分`          decimal(15,2) null,
        `小票金额`      decimal(15,2) null,
        `商品名称`      varchar(255) null,
        `商品编码`      varchar(100) null,
        `数量`          decimal(10,2) null,
        `活动名称`      text         null
    ) engine=InnoDB default charset=utf8mb4
    """, "小票表"),
    ("""
    create table if not exists `边界表` (
        `id`         int          not null,
        `空值列`     varchar(50)  null,
        `中文列`     varchar(255) null,
        `长文本列`   text         null,
        `小数列`     decimal(18,6) null,
        `日期列`     date         null,
        `布尔列`     tinyint(1)   null,
        `空串列`     varchar(50)  null default '',
        `超长列`     varchar(255) null comment '目标更宽，测不会缩短',
        primary key (`id`)
    ) engine=InnoDB default charset=utf8mb4
    """, "边界表"),
]


def build_tables(conn, ddl_list: list[tuple[str, str]], label: str) -> None:
    for ddl, name in ddl_list:
        conn.cursor().execute(ddl)
    print(f"  {label}建表完成：{[n for _, n in ddl_list]}")


def seed_orders(conn, total: int) -> None:
    """订单表：时间跨度 120 天。

    刻意包含三类行，覆盖增量的三种情形：
      · 正常历史行（不该被"最近 N 天"窗口碰到）
      · 最近 3 天的行（增量窗口内）
      · **老的创建时间 + 最近改过状态的行** ← 这就是"时间戳增量漏更新"的复现样本，
        用 订单创建时间 做水位时，它永远不会再被同步到
    """
    cur = conn.cursor()
    cur.execute("delete from `订单表`")
    today = dt.date(2026, 10, 8)
    statuses = ["待发货", "已发货", "已完成", "已取消"]
    refunds = ["无", "无", "无", "部分退款", "已退款"]
    rows = []
    for i in range(1, total + 1):
        days_ago = (i * 7) % 120
        created = dt.datetime.combine(today - dt.timedelta(days=days_ago), dt.time(0, 0, 0)) \
            + dt.timedelta(seconds=(i * 37) % 86400)
        # 每 50 行挑一行造"老的创建时间、但状态最近被改过"的样本
        if i % 50 == 0:
            created = dt.datetime.combine(today - dt.timedelta(days=90), dt.time(0, 0, 0)) \
                + dt.timedelta(seconds=(i * 11) % 86400)
        rows.append((
            f"SO{20260000 + i}",
            f"商品{i % 17}号",
            statuses[i % len(statuses)],
            refunds[i % len(refunds)],
            created,
            f"138{i:08d}"[:11],
            round((i % 997) + 0.35, 2),
            None if i % 11 == 0 else f"备注{i}",  # 每 11 行一个 NULL，测空值
        ))
    cur.executemany(
        "insert into `订单表` (`订单号`,`商品名称`,`订单状态`,`商品退款状态`,"
        "`订单创建时间`,`买家手机号`,`订单实付金额`,`备注`) values (%s,%s,%s,%s,%s,%s,%s,%s)",
        rows,
    )
    print(f"  订单表：{total} 行（含 {total // 50} 行『老创建时间但状态已变』的复现样本）")


def seed_receipts(conn, receipts: int) -> None:
    """小票表：无主键、一行多商品（同一个提单编号出现多次）。"""
    cur = conn.cursor()
    cur.execute("delete from `小票表`")
    today = dt.date(2026, 10, 8)
    rows = []
    for r in range(1, receipts + 1):
        receipt_no = f"TD{202600000 + r}"
        day = today - dt.timedelta(days=(r * 3) % 60)
        for line in range(1, (r % 3) + 2):  # 每张单 1~3 行商品
            rows.append((
                day.isoformat(),  # 只到天，和真实表一致
                receipt_no,
                ["直营", "加盟", "云购"][r % 3],
                ["华东", "华南", "华北"][r % 3],
                f"门店{r % 23}号",
                f"139{r:08d}"[:11],
                round(line * 1.5, 2),
                round(line * 88.8, 2),
                f"商品{line}号",
                f"SKU{r % 31:04d}",
                line,
                "中秋试饮活动" if r % 4 == 0 else None,
            ))
    cur.executemany(
        "insert into `小票表` (`提单时间`,`提单编号`,`渠道`,`市场`,`门店名称`,`消费者手机号`,"
        "`积分`,`小票金额`,`商品名称`,`商品编码`,`数量`,`活动名称`) "
        "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        rows,
    )
    print(f"  小票表：{receipts} 张单 / {len(rows)} 行（无主键，提单编号故意重复）")


def seed_edges(conn) -> None:
    """边界表：空值 / 中文与符号 / 超长文本 / 小数 / 日期 / 空字符串 / 超长列。"""
    cur = conn.cursor()
    cur.execute("delete from `边界表`")
    rows = [
        (1, None, "张三·李四", "x" * 5000, 123456789012.123456, dt.date(2026, 10, 8), 1, "", "短"),
        (2, "有值", "emoji 与符号 ✓ ★ ① <script>", "多行\n文本\ttab", -0.000001, dt.date(1999, 1, 1), 0, "", "这是三十个字符用来测自动拓宽列长度"),
        (3, "", "全角：ＡＢＣ　１２３", "", 0.000000, None, None, "", "刚好二十个字符的测试值"),
        (4, "  空格  ", "引号 \" ' \\ 反斜杠", "换行结尾\n", 99999999999.999999, dt.date(2024, 2, 29), 1, "非空", "a"),
        (5, None, None, None, None, None, None, None, None),
    ]
    cur.executemany(
        "insert into `边界表` (`id`,`空值列`,`中文列`,`长文本列`,`小数列`,`日期列`,"
        "`布尔列`,`空串列`,`超长列`) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        rows,
    )
    print(f"  边界表：{len(rows)} 行（空值 / 中文 / emoji / 超长 / 负数小数 / 空串 / 非法日期）")


def seed_big_table(conn, rows: int) -> None:
    """大表：测内存占用与吞吐，不得一次 fetchall 全量。"""
    cur = conn.cursor()
    cur.execute("""
    create table if not exists `大表` (
        `id` bigint not null auto_increment,
        `业务号` varchar(50) null,
        `金额` decimal(12,2) null,
        `说明` varchar(255) null,
        `创建时间` datetime null,
        primary key (`id`)
    ) engine=InnoDB default charset=utf8mb4
    """)
    cur.execute("select count(*) from `大表`")
    have = cur.fetchone()[0]
    if have >= rows:
        print(f"  大表：已有 {have} 行，跳过（幂等）")
        return
    batch, base = [], have
    now = dt.datetime(2026, 10, 8, 12, 0, 0)
    for i in range(base + 1, rows + 1):
        batch.append((f"B{i:09d}", round(i % 9999 + 0.5, 2), f"说明{i}", now - dt.timedelta(seconds=i)))
        if len(batch) >= 5000:
            cur.executemany("insert into `大表` (`业务号`,`金额`,`说明`,`创建时间`) values (%s,%s,%s,%s)", batch)
            batch = []
    if batch:
        cur.executemany("insert into `大表` (`业务号`,`金额`,`说明`,`创建时间`) values (%s,%s,%s,%s)", batch)
    print(f"  大表：{rows} 行（性能与内存测试用）")


def seed_dst_noise(conn) -> None:
    """目标库预置"陈旧 + 重复"的脏数据，用来验证覆盖/幂等是否真的生效。"""
    cur = conn.cursor()
    cur.execute("delete from `小票表`")
    cur.executemany(
        "insert into `小票表` (`提单时间`,`提单编号`,`渠道`,`消费者手机号`,`商品名称`,`数量`) "
        "values (%s,%s,%s,%s,%s,%s)",
        [
            ("2026-01-01", "TD_STALE_1", "旧渠道", "13000000000", "应该被覆盖掉的旧行", 1),
            ("2026-01-01", "TD_STALE_1", "旧渠道", "13000000000", "应该被覆盖掉的旧行", 2),
            ("2026-01-02", "TD_STALE_2", "旧渠道", "13000000001", "应该被覆盖掉的旧行", 1),
        ],
    )
    cur.execute("delete from `订单表`")
    print("  目标库小票表：预置 3 行陈旧数据（验证覆盖/幂等）")


def report(conn) -> None:
    pairs = [(SRC_DB, "订单表"), (SRC_DB, "小票表"), (SRC_DB, "边界表"), (SRC_DB, "大表"),
             (DST_DB, "订单表"), (DST_DB, "订单表_窄列"), (DST_DB, "小票表"), (DST_DB, "边界表")]
    print("\n  === 最终状态 ===")
    for db, table in pairs:
        try:
            cur = conn.cursor()
            cur.execute(f"select count(*) from `{db}`.`{table}`")
            count = cur.fetchone()[0]
        except Exception as exc:
            count = f"(不存在：{exc.__class__.__name__})"
        print(f"    {db}.{table:<14} {count} 行")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=1000, help="订单表行数")
    ap.add_argument("--receipts", type=int, default=300, help="小票表张数")
    ap.add_argument("--big", type=int, default=50000, help="大表行数（0 = 不建）")
    args = ap.parse_args()

    print("同步模块测试库夹具")
    root = connect()
    try:
        create_databases(root)
    finally:
        root.close()

    src = connect(SRC_DB)
    try:
        build_tables(src, SRC_DDL, "源库")
        seed_orders(src, args.rows)
        seed_receipts(src, args.receipts)
        seed_edges(src)
        if args.big > 0:
            seed_big_table(src, args.big)
    finally:
        src.close()

    dst = connect(DST_DB)
    try:
        build_tables(dst, DST_DDL, "目标库")
        seed_dst_noise(dst)
    finally:
        dst.close()

    check = connect()
    try:
        report(check)
    finally:
        check.close()
    print("\n完成。源 dc_sync_src / 目标 dc_sync_dst，可反复执行（幂等）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
