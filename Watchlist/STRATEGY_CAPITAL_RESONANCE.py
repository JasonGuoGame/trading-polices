import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text, bindparam
import datetime
import json
import sys
import warnings

warnings.filterwarnings("ignore")

# -------------------------
# config
# -------------------------
sys.path.append(r"C:\ws\trading-polices\config")
import config

engine_quant = create_engine(
    "mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db"
)

engine_review = create_engine(
    "mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review"
)

# -------------------------
# sector clean
# -------------------------
def clean_and_pick_sectors(symbol):

    sql = text("""
        SELECT GROUP_CONCAT(DISTINCT sector_name)
        FROM stock_sector_relation
        WHERE symbol = :s
    """)

    with engine_quant.connect() as conn:
        res = conn.execute(sql, {"s": symbol}).fetchone()[0]

    if not res:
        return "其他"

    raw = res.split(",")

    filtered = [
        s.replace("行业-", "").replace("概念-", "")
        for s in raw
        if not any(x in s for x in config.SECTOR_BLACKLIST)
    ]

    return " / ".join(filtered[:2]) if filtered else "综合题材"


# -------------------------
# scoring
# -------------------------
def calc_score(row):

    score = 0

    score += min(row["avg_ratio"] * 4, 20)
    score += min(row["auction_amount"] / 1000, 10)

    score += min(row["vol_ratio"] * 4, 15)
    score += min(row["surge_count"] * 3, 15)

    score += min(row["max_surge_ret"] * 5, 10)

    score += min(row["net_inflow_amount"], 15)
    score += min(row["net_inflow_rate"], 5)

    score += min(row["amount_today"] / 4, 5)

    score += min(row["f_mom_20"] / 5, 3)

    if row["f_dist_high"] <= 8:
        score += 2

    return round(score, 2)


# -------------------------
# save to pool
# -------------------------
def save_to_stock_pool(df, trade_date):

    if df.empty:
        return

    now = datetime.datetime.now()
    records = []

    for _, row in df.iterrows():

        score = row["score"]

        if score >= 90:
            level = 3
        elif score >= 80:
            level = 2
        else:
            level = 1

        tags = {
            "avg_ratio": float(row["avg_ratio"]),
            "auction_amount": float(row["auction_amount"]),
            "vol_ratio": float(row["vol_ratio"]),
            "surge_count": int(row["surge_count"]),
            "max_surge_ret": float(row["max_surge_ret"]),
            "net_inflow_amount": float(row["net_inflow_amount"]),
            "net_inflow_rate": float(row["net_inflow_rate"]),
            "amount_today": float(row["amount_today"]),
            "mom20": float(row["f_mom_20"]),
            "dist_high": float(row["f_dist_high"])
        }

        records.append({
            "symbol": row["symbol"],
            "trade_date": trade_date,
            "stock_name": row["name"],
            "pool_type": "short",
            "sector_name": row["sector_name"],
            "score": score,
            "status": "GPT资金共振",
            "tags": json.dumps(tags, ensure_ascii=False),
            "notes": f"竞价量比{row['avg_ratio']} 资金脉冲{row['surge_count']}次 板块流入{row['net_inflow_amount']}亿",
            "is_watch_focus": 1,
            "watch_level": level,
            "created_at": now,
            "updated_at": now
        })

    df_save = pd.DataFrame(records)

    with engine_review.begin() as conn:

        conn.execute(
            text("""
                DELETE FROM stock_pools
                WHERE trade_date=:d
                AND status='GPT资金共振'
            """),
            {"d": trade_date}
        )

        df_save.to_sql(
            "stock_pools",
            conn,
            if_exists="append",
            index=False
        )

    print(f"✅ 写入资金共振股票池 {len(df_save)} 只")


# -------------------------
# main
# -------------------------
def run():

    print(f"[{datetime.datetime.now()}] 启动资金共振选股...")

    sql = text("""
    SELECT
        a.symbol,
        a.name,
        a.avg_ratio,
        a.auction_amount,

        c.vol_ratio,
        c.surge_count,
        c.max_surge_ret,

        m.amount_today,
        m.sector_name,

        f.net_inflow_amount,
        f.net_inflow_rate,

        fac.f_mom_20,
        fac.f_dist_high

    FROM stk_auction_signal a

    LEFT JOIN stk_capital_abnormal c
        ON a.symbol=c.symbol
        AND a.trade_date=c.trade_date

    LEFT JOIN stk_market_attack_log m
        ON a.symbol=m.symbol
        AND a.trade_date=m.trade_date

    LEFT JOIN stk_sector_fund_flow f
        ON m.sector_name=f.sector_name
        AND m.trade_date=f.trade_date

    LEFT JOIN stk_factors fac
        ON a.symbol=fac.symbol
        AND a.trade_date=fac.trade_date

    WHERE a.trade_date = (
        SELECT MAX(trade_date)
        FROM stk_auction_signal
    )
    """)

    with engine_quant.connect() as conn:
        df = pd.read_sql(sql, conn)

    if df.empty:
        print("无数据")
        return

    df = df.fillna(0)

    # -------------------------
    # filter
    # -------------------------
    df = df[
        (df["avg_ratio"] >= 3) &
        (df["surge_count"] >= 2) &
        (df["net_inflow_amount"] > 0) &
        (df["f_mom_20"] > 0.05)
    ]

    if df.empty:
        print("无符合条件股票")
        return

    # -------------------------
    # score
    # -------------------------
    df["score"] = df.apply(calc_score, axis=1)

    df = df.sort_values("score", ascending=False).head(20)

    print(df[[
        "symbol",
        "name",
        "sector_name",
        "score"
    ]])

    save_to_stock_pool(df, df["trade_date"].iloc[0] if "trade_date" in df.columns else datetime.date.today())


# -------------------------
# entry
# -------------------------
if __name__ == "__main__":
    run()