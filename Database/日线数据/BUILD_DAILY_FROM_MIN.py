# -*- coding: utf-8 -*-

import pandas as pd
from sqlalchemy import create_engine, text
import datetime
import time


# ============================================================
# 配置
# ============================================================

DB_URL = "mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db"

MIN_TABLE = "stk_min_kline"
DAILY_TABLE = "stk_daily_kline"
INSTRUMENT_TABLE = "stk_instrument"

# 每批写入多少条
INSERT_BATCH_SIZE = 5000

engine = create_engine(
    DB_URL,
    pool_pre_ping=True,
    pool_recycle=3600
)


# ============================================================
# 1. 获取分钟数据库最新交易日
# ============================================================

def get_latest_trade_date():

    sql = text(f"""
        SELECT MAX(DATE(trade_time)) AS latest_date
        FROM {MIN_TABLE}
    """)

    with engine.connect() as conn:
        result = conn.execute(sql).fetchone()

    if result is None or result[0] is None:
        return None

    return result[0]


# ============================================================
# 1.5 获取股票股本信息 (用于计算换手率)
# ============================================================

def get_instrument_shares():
    """从 stk_instrument 读取流通股本，优先使用 free_float_shares，若无则使用 float_shares"""
    sql = text(f"""
        SELECT 
            symbol,
            COALESCE(NULLIF(free_float_shares, 0), float_shares) AS shares
        FROM {INSTRUMENT_TABLE}
    """)

    with engine.connect() as conn:
        df_shares = pd.read_sql(sql, conn)

    df_shares["shares"] = pd.to_numeric(df_shares["shares"], errors="coerce").fillna(0)
    return df_shares


# ============================================================
# 2. 获取最新交易日的分钟数据
# ============================================================

def get_latest_minute_data(trade_date):

    start_time = datetime.datetime.combine(
        trade_date,
        datetime.time.min
    )

    end_time = start_time + datetime.timedelta(days=1)

    print(f"[1/4] 正在读取 {trade_date} 的分钟数据...")

    sql = text(f"""
        SELECT
            symbol,
            trade_time,
            open,
            high,
            low,
            close,
            volume,
            amount
        FROM {MIN_TABLE}
        WHERE trade_time >= :start_time
          AND trade_time < :end_time
        ORDER BY symbol, trade_time
    """)

    with engine.connect() as conn:

        df = pd.read_sql(
            sql,
            conn,
            params={
                "start_time": start_time,
                "end_time": end_time
            }
        )

    return df


# ============================================================
# 3. 分钟线 → 日线 (含换手率计算)
# ============================================================

def calculate_daily(df, trade_date):

    if df.empty:
        print(f"❌ {trade_date} 没有分钟数据")
        return pd.DataFrame()

    print(
        f"[2/4] 分钟数据："
        f"{len(df):,} 行，"
        f"{df['symbol'].nunique():,} 只股票"
    )

    # --------------------------------------------------------
    # 数据类型转换
    # --------------------------------------------------------

    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount"
    ]

    for col in numeric_columns:

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    # --------------------------------------------------------
    # 删除无效数据
    # --------------------------------------------------------

    df = df.dropna(
        subset=[
            "symbol",
            "trade_time",
            "open",
            "high",
            "low",
            "close"
        ]
    )

    if df.empty:
        print(f"❌ {trade_date} 清洗后没有有效数据")
        return pd.DataFrame()

    # --------------------------------------------------------
    # 确保按照股票 + 时间排序
    # --------------------------------------------------------

    df = df.sort_values(
        ["symbol", "trade_time"]
    )

    # --------------------------------------------------------
    # 聚合成日线
    # --------------------------------------------------------

    daily = (
        df.groupby(
            "symbol",
            sort=False
        )
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
            amount=("amount", "sum")
        )
        .reset_index()
    )

    daily["trade_date"] = trade_date

    # --------------------------------------------------------
    # 计算换手率逻辑 (Turnover Rate)
    # --------------------------------------------------------
    df_shares = get_instrument_shares()
    daily = pd.merge(daily, df_shares, on="symbol", how="left")

    # 换手率 = (成交量 / 流通股本) * 100
    daily["shares"] = daily["shares"].fillna(0)
    daily["turnover_rate"] = daily.apply(
        lambda row: (row["volume"] / row["shares"] * 100) if row["shares"] > 0 else 0.0,
        axis=1
    )

    # --------------------------------------------------------
    # 调整字段顺序 (包含 turnover_rate)
    # --------------------------------------------------------

    daily = daily[
        [
            "symbol",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "turnover_rate"
        ]
    ]

    return daily


# ============================================================
# 4. 写入 stk_daily_kline
# ============================================================

def upsert_daily_data(daily):

    if daily.empty:
        return 0

    print(
        f"[3/4] 准备写入日线："
        f"{len(daily):,} 条"
    )

    upsert_sql = text(f"""
        INSERT INTO {DAILY_TABLE}
        (
            symbol,
            trade_date,
            open,
            high,
            low,
            close,
            volume,
            amount,
            turnover_rate
        )
        VALUES
        (
            :symbol,
            :trade_date,
            :open,
            :high,
            :low,
            :close,
            :volume,
            :amount,
            :turnover_rate
        )
        ON DUPLICATE KEY UPDATE

            open = VALUES(open),
            high = VALUES(high),
            low = VALUES(low),
            close = VALUES(close),
            volume = VALUES(volume),
            amount = VALUES(amount),
            turnover_rate = VALUES(turnover_rate)
    """)

    records = daily.to_dict("records")

    total = len(records)

    with engine.begin() as conn:

        for i in range(
            0,
            total,
            INSERT_BATCH_SIZE
        ):

            batch = records[
                i:i + INSERT_BATCH_SIZE
            ]

            conn.execute(
                upsert_sql,
                batch
            )

            print(
                f"    写入进度："
                f"{min(i + INSERT_BATCH_SIZE, total):,}"
                f"/{total:,}"
            )

    return total


# ============================================================
# 5. 主程序
# ============================================================

def update_latest_daily():

    start_time = time.time()

    print()
    print("=" * 70)
    print("        MIN → DAILY 最新交易日同步 (含换手率)")
    print("=" * 70)

    # --------------------------------------------------------
    # 获取最新交易日
    # --------------------------------------------------------

    latest_date = get_latest_trade_date()

    if latest_date is None:

        print(
            f"[{datetime.datetime.now()}] "
            f"❌ {MIN_TABLE} 没有任何分钟数据"
        )

        return

    print(
        f"最新分钟数据日期：{latest_date}"
    )

    # --------------------------------------------------------
    # 读取分钟数据
    # --------------------------------------------------------

    df = get_latest_minute_data(
        latest_date
    )

    if df.empty:

        print(
            f"❌ {latest_date} 没有分钟数据"
        )

        return

    # --------------------------------------------------------
    # 计算日线
    # --------------------------------------------------------

    daily = calculate_daily(
        df,
        latest_date
    )

    if daily.empty:
        return

    # --------------------------------------------------------
    # 写入 MySQL
    # --------------------------------------------------------

    count = upsert_daily_data(
        daily
    )

    elapsed = time.time() - start_time

    print()
    print("[4/4] 同步完成")
    print("-" * 70)
    print(f"交易日期：{latest_date}")
    print(f"股票数量：{count:,}")
    print(f"耗时：{elapsed:.2f} 秒")
    print("-" * 70)

    # --------------------------------------------------------
    # 简单检查
    # --------------------------------------------------------

    check_sql = text(f"""
        SELECT
            COUNT(*) AS total_count,
            COUNT(DISTINCT symbol) AS stock_count,
            MIN(trade_date) AS min_date,
            MAX(trade_date) AS max_date
        FROM {DAILY_TABLE}
        WHERE trade_date = :trade_date
    """)

    with engine.connect() as conn:

        result = conn.execute(
            check_sql,
            {
                "trade_date": latest_date
            }
        ).fetchone()

    print()
    print("日线检查结果：")
    print(f"  日线记录：{result[0]:,}")
    print(f"  股票数量：{result[1]:,}")
    print(f"  日期范围：{result[2]} → {result[3]}")

    print()
    print("=" * 70)
    print("                    完成")
    print("=" * 70)


# ============================================================
# 程序入口
# ============================================================

if __name__ == "__main__":

    update_latest_daily()