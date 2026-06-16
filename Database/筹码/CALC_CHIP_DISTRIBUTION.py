import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import warnings

warnings.filterwarnings("ignore")

# =========================
# DB
# =========================
engine = create_engine(
    "mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db",
    pool_pre_ping=True
)

# =========================
# 行为标签
# =========================
def behavior_label(x):
    return {
        0: "震荡不明",
        1: "主力吸筹",
        2: "震仓洗盘",
        3: "主升拉升",
        4: "主力出货"
    }.get(x, "未知")


# =========================
# 最新交易日
# =========================
def get_latest_date():
    return pd.read_sql(
        "SELECT MAX(trade_date) FROM stk_daily_kline",
        engine
    ).iloc[0, 0]


# =========================
# 行情数据
# =========================
def load_kline(latest_date):
    sql = text("""
        SELECT symbol, trade_date, close, high, low, turnover_rate
        FROM stk_daily_kline
        WHERE trade_date >= DATE_SUB(:d, INTERVAL 180 DAY)
        ORDER BY symbol, trade_date
    """)
    return pd.read_sql(sql, engine, params={"d": latest_date})


# =========================
# 筹码模型（升级版：含 peak_move_pct）
# =========================
def calc_chip(df):
    df = df.copy()
    peak_20d_ago = 0.0

    df["low"] = df["low"].astype(float)
    df["high"] = df["high"].astype(float)
    df["close"] = df["close"].astype(float)
    df["turnover_rate"] = df["turnover_rate"].fillna(0).astype(float)

    min_p = df["low"].min()
    max_p = df["high"].max()

    if min_p <= 0 or min_p == max_p:
        return None

    bins = np.linspace(min_p, max_p, 200)
    chips = np.zeros_like(bins)

    for i, (_, row) in enumerate(df.iterrows()):

        turnover = min(row["turnover_rate"] / 100, 0.99)

        chips *= (1 - turnover)

        mask = (bins >= row["low"]) & (bins <= row["high"])
        if mask.sum() > 0:
            chips[mask] += turnover / mask.sum()

        # ✅ 20日筹码峰快照
        if i == len(df) - 21:
            peak_20d_ago = bins[np.argmax(chips)]

    total = chips.sum()
    if total <= 0:
        return None

    current_price = df["close"].iloc[-1]
    peak = bins[np.argmax(chips)]

    profit_ratio = chips[bins <= current_price].sum() / total * 100

    cumsum = np.cumsum(chips) / total

    def get_range(p):
        try:
            l = np.where(cumsum >= (1 - p) / 2)[0][0]
            r = np.where(cumsum >= (1 + p) / 2)[0][0]
            return bins[l], bins[r]
        except:
            return min_p, max_p

    c70_low, c70_high = get_range(0.7)

    chip_width = (c70_high - c70_low) / (c70_high + c70_low + 1e-6)
    peak_distance = (current_price - peak) / (peak + 1e-6)

    chip_score = 0
    if chip_width < 0.12:
        chip_score += 40
    if profit_ratio > 85:
        chip_score += 40
    if abs(peak_distance) < 0.04:
        chip_score += 20

    cost = (c70_low + c70_high) / 2
    cost_profit = (current_price - cost) / (cost + 1e-6) * 100

    # =========================
    # peak_move_pct（核心修复）
    # =========================
    peak_move_pct = 0.0
    if peak_20d_ago > 0:
        peak_move_pct = (peak - peak_20d_ago) / (peak_20d_ago + 1e-6)

    return {
        "chip_peak_price": float(peak),
        "current_price": float(current_price),
        "profit_ratio": float(profit_ratio),
        "chip70_low": float(c70_low),
        "chip70_high": float(c70_high),
        "chip90_low": float(min_p),
        "chip90_high": float(max_p),
        "chip_width70": float(chip_width),
        "peak_distance": float(peak_distance),
        "chip_score": int(chip_score),
        "peak_move_pct": float(peak_move_pct),
        "estimated_main_cost": float(cost),
        "cost_profit_pct": float(cost_profit),
    }


# =========================
# 主力行为（升级版 V2）
# =========================
def detect_behavior(row):

    profit = row["profit_ratio"]
    peak_dist = abs(row["peak_distance"])
    cost_profit = row["cost_profit_pct"]
    chip_width = row["chip_width70"]
    capital = row["capital_control_score"]
    peak_move = row["peak_move_pct"]

    # =========================
    # 吸筹
    # =========================
    if (
        chip_width < 0.12
        and profit < 60
        and cost_profit < 10
        and capital >= 5
        and peak_move < 0.01
    ):
        return 1

    # =========================
    # 洗盘
    # =========================
    if (
        60 <= profit < 85
        and peak_dist > 0.03
        and peak_move <= 0.01
    ):
        return 2

    # =========================
    # 主升浪（优化关键）
    # =========================
    if (
        profit >= 75
        and cost_profit > 3
        and 0.01 < peak_move < 0.05
    ):
        return 3

    # =========================
    # 出货（真实机构行为）
    # =========================
    if (
        profit > 85
        and cost_profit > 10
        and peak_move < 0.01
    ):
        return 4

    return 0


# =========================
# 强度评分
# =========================
def calc_strength(row):
    score = 0
    score += min(row["capital_control_score"], 10)
    score += min(row["chip_score"] / 10, 10)
    score += max(0, 10 - abs(row["peak_distance"]) * 100)
    score += min(row["profit_ratio"] / 10, 10)
    return round(score, 2)


# =========================
# 主流程
# =========================
def run():

    latest = get_latest_date()
    if not latest:
        return

    df_all = load_kline(latest)

    results = []

    for symbol, df in df_all.groupby("symbol"):
        if len(df) < 60:
            continue

        r = calc_chip(df)
        if r:
            r["symbol"] = symbol
            r["trade_date"] = latest
            results.append(r)

    if not results:
        print("无数据")
        return

    df = pd.DataFrame(results)

    # =========================
    # 资金流
    # =========================
    fund = pd.read_sql(
        text("""
            SELECT symbol, capital_score, inflow_days, main_net_ratio, inflow_5d
            FROM stk_stock_fund_flow
            WHERE trade_date = :d
        """),
        engine,
        params={"d": latest},
    )

    df = df.merge(fund, on="symbol", how="left").fillna(0)

    # =========================
    # 资金评分
    # =========================
    df["capital_control_score"] = (
        (df["capital_score"] / 100 * 8).clip(0, 8)
        + (df["inflow_days"] / 5 * 4).clip(0, 4)
        + (df["main_net_ratio"].clip(0, 20) / 20 * 4)
        + (df["inflow_5d"].clip(lower=0) / 10000 * 4).clip(0, 4)
    ).round(2)

    # =========================
    # 行为
    # =========================
    df["behavior"] = df.apply(detect_behavior, axis=1)
    df["behavior_strength"] = df.apply(calc_strength, axis=1)
    df["behavior_label"] = df["behavior"].apply(behavior_label)

    # =========================
    # 控盘评分
    # =========================
    df["main_force_control_score"] = (
        df["chip_score"]
        + df["capital_control_score"]
        + (100 - df["chip_width70"] * 100).clip(0, 30)
    ).clip(0, 100).astype(int)

    df["control_level"] = pd.cut(
        df["main_force_control_score"],
        bins=[-1, 50, 70, 85, 100],
        labels=["无控盘", "弱控盘", "中度控盘", "高度控盘"]
    )

    # =========================
    # 入库（完全匹配你的表）
    # =========================
    sql = """
    INSERT INTO stk_chip_factor (
        trade_date, symbol,
        chip_peak_price, current_price, profit_ratio,
        chip70_low, chip70_high, chip90_low, chip90_high,
        chip_width70, peak_distance, chip_score,
        peak_move_pct, estimated_main_cost,
        cost_profit_pct,
        main_force_control_score,
        control_level,
        capital_control_score,
        behavior,
        behavior_strength,
        behavior_label
    )
    VALUES (
        :trade_date, :symbol,
        :chip_peak_price, :current_price, :profit_ratio,
        :chip70_low, :chip70_high, :chip90_low, :chip90_high,
        :chip_width70, :peak_distance, :chip_score,
        :peak_move_pct, :estimated_main_cost,
        :cost_profit_pct,
        :main_force_control_score,
        :control_level,
        :capital_control_score,
        :behavior,
        :behavior_strength,
        :behavior_label
    )
    ON DUPLICATE KEY UPDATE
        chip_score=VALUES(chip_score),
        profit_ratio=VALUES(profit_ratio),
        capital_control_score=VALUES(capital_control_score),
        main_force_control_score=VALUES(main_force_control_score),
        control_level=VALUES(control_level),
        behavior=VALUES(behavior),
        behavior_strength=VALUES(behavior_strength),
        behavior_label=VALUES(behavior_label),
        peak_move_pct=VALUES(peak_move_pct)
    """

    with engine.begin() as conn:
        conn.execute(text(sql), df.to_dict(orient="records"))

    print(f"✅ 完成：{len(df)} 条写入")


# =========================
# RUN
# =========================
if __name__ == "__main__":
    run()