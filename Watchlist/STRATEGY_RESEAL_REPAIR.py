import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text, bindparam
import datetime
import json
import sys
import warnings

warnings.filterwarnings('ignore')

# --- config ---
sys.path.append(r"C:\ws\trading-polices\config")
import config

engine_quant = create_engine(
    'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
)

engine_review = create_engine(
    'mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review'
)

# -------------------------
# 板块处理
# -------------------------
def clean_and_pick_sectors(symbol):
    query = text("""
        SELECT GROUP_CONCAT(DISTINCT sector_name)
        FROM stock_sector_relation
        WHERE symbol = :s
    """)

    with engine_quant.connect() as conn:
        res = conn.execute(query, {"s": symbol}).fetchone()[0]

    if not res:
        return "其他"

    raw_list = res.split(',')

    filtered = [
        s.replace('行业-', '').replace('概念-', '')
        for s in raw_list
        if not any(noise in s for noise in config.SECTOR_BLACKLIST)
    ]

    return " / ".join(filtered[:2]) if filtered else "综合题材"


# -------------------------
# score
# -------------------------
def calculate_repair_score(row):

    s1 = np.clip(row['repair_depth'] * 0.5, 0, 50)
    s2 = np.clip(row['vol_ratio'] * 10, 0, 30)
    s3 = np.clip(row['today_pct'] * 2, 0, 20)

    return int(s1 + s2 + s3)


# -------------------------
# save pool
# -------------------------
def save_to_stock_pool(df_results, trade_date):

    if df_results.empty:
        return

    now = datetime.datetime.now()

    records = []

    for _, row in df_results.iterrows():

        tags_dict = {
            "repair_depth": f"{row['repair_depth']}%",
            "yest_vol": row['y_vol_ratio'],
            "status": "Divergence_Confirm"
        }

        watch_lvl = 3 if row['repair_depth'] >= 80 else 2

        records.append({
            'symbol': row['symbol'],
            'trade_date': trade_date,
            'stock_name': row['name'],
            'pool_type': 'short',
            'sector_name': clean_and_pick_sectors(row['symbol']),
            'score': calculate_repair_score(row),
            'status': '分歧反包',
            'tags': json.dumps(tags_dict, ensure_ascii=False),
            'notes': f"放量分歧修复{row['repair_depth']}%",
            'is_watch_focus': 0,
            'watch_level': watch_lvl,
            'created_at': now,
            'updated_at': now
        })

    df_save = pd.DataFrame(records)

    try:
        with engine_review.begin() as conn:

            conn.execute(
                text("""
                    DELETE FROM stock_pools
                    WHERE trade_date=:d
                    AND status='分歧反包'
                """),
                {"d": trade_date}
            )

            df_save.to_sql(
                'stock_pools',
                con=conn,
                if_exists='append',
                index=False
            )

        print(f"✅ 写入股票池成功: {len(df_save)} 只")

    except Exception as e:
        print(f"❌ 写入失败: {e}")


# -------------------------
# repair depth
# -------------------------
def calc_repair_depth(df):

    denom = (df['y_high'] - df['y_close'])

    denom = denom.replace(0, np.nan)

    repair = (
        (df['t_close'] - df['y_close']) / denom
    )

    return (repair.clip(0, 1) * 100).round(1)


# -------------------------
# main
# -------------------------
def run_divergence_pipeline():

    print(f"[{datetime.datetime.now()}] 启动分歧反包策略...")

    with engine_quant.connect() as conn:

        dates = conn.execute(text("""
            SELECT DISTINCT trade_date
            FROM stk_daily_kline
            ORDER BY trade_date DESC
            LIMIT 3
        """)).fetchall()

        if len(dates) < 3:
            return

        today, yesterday, prev_day = dates[0][0], dates[1][0], dates[2][0]

        # -------------------------
        # 昨日分歧
        # -------------------------
        sql_yest = text("""
            SELECT
                k.symbol,
                s.name,
                k.high AS y_high,
                k.close AS y_close,
                k.open AS y_open,
                k.volume AS y_vol,
                ky.volume AS prev_vol,

                (k.volume / NULLIF(ky.volume,0)) AS y_vol_ratio

            FROM stk_daily_kline k

            JOIN stocks s
                ON k.symbol = s.symbol

            JOIN stk_daily_kline ky
                ON k.symbol = ky.symbol
               AND ky.trade_date = :prev

            WHERE k.trade_date = :yest

              AND k.amount > 500000000

              AND k.volume > ky.volume * 1.5

              AND (k.high - GREATEST(k.open, k.close))
                  > ABS(k.open - k.close)

              AND k.close > ky.close

              AND s.name NOT LIKE '%ST%'
        """)

        df_yest = pd.read_sql(sql_yest, conn, params={
            "yest": yesterday,
            "prev": prev_day
        })

        if df_yest.empty:
            print("无分歧标的")
            return

        symbols = df_yest['symbol'].tolist()

        # -------------------------
        # 今日数据（修复 IN）
        # -------------------------
        sql_today = text("""
            SELECT
                symbol,
                close AS t_close,
                volume AS t_vol
            FROM stk_daily_kline
            WHERE trade_date = :today
              AND symbol IN :symbols
        """).bindparams(
            bindparam("symbols", expanding=True)
        )

        df_today = pd.read_sql(sql_today, conn, params={
            "today": today,
            "symbols": symbols
        })

    # -------------------------
    # merge
    # -------------------------
    df = pd.merge(df_yest, df_today, on="symbol")

    df["repair_depth"] = calc_repair_depth(df)

    df["vol_ratio"] = df["t_vol"] / df["y_vol"].replace(0, np.nan)

    df["today_pct"] = (df["t_close"] / df["y_close"] - 1) * 100

    # -------------------------
    # filter
    # -------------------------
    final = df[
        (df["repair_depth"] >= 50) &
        (df["today_pct"] > 0)
    ]

    if final.empty:
        print("今日无有效修复")
        return

    final = final.sort_values("repair_depth", ascending=False)

    print(final[[
        "symbol",
        "name",
        "repair_depth",
        "today_pct",
        "y_vol_ratio"
    ]])

    save_to_stock_pool(final, today)


if __name__ == "__main__":
    run_divergence_pipeline()