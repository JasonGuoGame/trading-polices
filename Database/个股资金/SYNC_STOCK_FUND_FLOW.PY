import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime
import warnings

warnings.filterwarnings('ignore')

engine = create_engine(
    'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db',
    pool_pre_ping=True,
    pool_recycle=3600
)

def calculate_stock_fund_flow():
    print(f"[{datetime.datetime.now()}] 启动个股资金流向模型（优化版）...")

    # =========================
    # 1. 获取交易日
    # =========================
    with engine.connect() as conn:
        today = conn.execute(
            text("SELECT MAX(DATE(trade_time)) FROM stk_min_kline")
        ).fetchone()[0]

    if not today:
        print("无交易日数据")
        return

    print(f"分析目标日: {today}")

    # =========================
    # 2. 拉取分钟数据（只拉必要字段）
    # =========================
    query = text("""
        SELECT symbol, trade_time, open, close, amount
        FROM stk_min_kline
        WHERE DATE(trade_time) = :t
    """)

    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params={"t": today})

    if df.empty:
        print("今日分钟数据为空")
        return

    # =========================
    # 3. 向量化资金方向（核心优化）
    # =========================

    # 更合理的“资金方向”
    # 用价格变化替代 open-close
    df = df.sort_values(["symbol", "trade_time"])

    df["price_change"] = df.groupby("symbol")["close"].diff()
    df["direction"] = np.sign(df["price_change"]).fillna(0)

    # 成交额单位：万元
    df["amount_w"] = df["amount"] / 10000.0

    # 资金流
    df["net_flow"] = df["amount_w"] * df["direction"]

    # =========================
    # 4. 分位数分类（向量化替代 apply）
    # =========================
    def calc_group(g):
        total_amt = g["amount_w"].sum()
        if total_amt <= 0:
            return pd.Series()

        q95 = g["amount_w"].quantile(0.95)
        q80 = g["amount_w"].quantile(0.80)
        q50 = g["amount_w"].quantile(0.50)

        super_large = g.loc[g["amount_w"] >= q95, "net_flow"].sum()
        large = g.loc[(g["amount_w"] < q95) & (g["amount_w"] >= q80), "net_flow"].sum()
        medium = g.loc[(g["amount_w"] < q80) & (g["amount_w"] >= q50), "net_flow"].sum()
        small = g.loc[g["amount_w"] < q50, "net_flow"].sum()

        main = super_large + large

        return pd.Series({
            "main_net_inflow": main,
            "super_large_net_inflow": super_large,
            "large_net_inflow": large,
            "medium_net_inflow": medium,
            "small_net_inflow": small,
            "main_net_ratio": (main / total_amt * 100) if total_amt else 0,
            "total_amount": total_amt
        })

    df_daily = df.groupby("symbol").apply(calc_group).dropna().reset_index()
    
    df_daily["trade_date"] = today
    
    # =========================
    # 🌟 5. 计算多日累计与连续天数 (新增核心逻辑)
    # =========================
    print("计算 3/5/10日累计净流入与连续净流入天数...")
    
    # 1. 从数据库获取过去 15 天的历史主力净流入数据 (用于计算10日累计和连续天数)
    hist_query = text("""
        SELECT trade_date, symbol, main_net_inflow
        FROM stk_stock_fund_flow
        WHERE trade_date >= DATE_SUB(:t, INTERVAL 15 DAY) AND trade_date < :t
    """)
    with engine.connect() as conn:
        df_hist = pd.read_sql(hist_query, conn, params={"t": today})
        
    # 2. 提取当日计算出的主力净流入
    df_today_flow = df_daily[['trade_date', 'symbol', 'main_net_inflow']].copy()
    
    # 3. 合并历史与当日数据，并按时间排序
    df_all_flow = pd.concat([df_hist, df_today_flow], ignore_index=True)
    df_all_flow = df_all_flow.sort_values(['symbol', 'trade_date'])
    
    # 4. 计算 3日、5日、10日 滚动累计净流入 (min_periods=1 防止新股前几天出现 NaN)
    df_all_flow['inflow_3d'] = df_all_flow.groupby('symbol')['main_net_inflow'].transform(lambda x: x.rolling(window=3, min_periods=1).sum())
    df_all_flow['inflow_5d'] = df_all_flow.groupby('symbol')['main_net_inflow'].transform(lambda x: x.rolling(window=5, min_periods=1).sum())
    df_all_flow['inflow_10d'] = df_all_flow.groupby('symbol')['main_net_inflow'].transform(lambda x: x.rolling(window=10, min_periods=1).sum())
    
    # 5. 计算连续净流入天数 (inflow_days) - 使用高效的 Pandas 分组累加技巧
    is_pos = (df_all_flow['main_net_inflow'] > 0).astype(int)
    # 遇到 <=0 的天数，cumsum 加 1，从而产生新的分组 ID
    group_id = (~is_pos.astype(bool)).groupby(df_all_flow['symbol']).cumsum()
    # 在每个分组内累加 is_pos
    consecutive = is_pos.groupby([df_all_flow['symbol'], group_id]).cumsum()
    # 将结果乘回 is_pos，确保断点当天(<=0)的连续天数为 0
    df_all_flow['inflow_days'] = consecutive * is_pos
    
    # 6. 提取当日的数据，并合并回主表 df_daily
    df_today_stats = df_all_flow[df_all_flow['trade_date'] == today][['symbol', 'inflow_3d', 'inflow_5d', 'inflow_10d', 'inflow_days']]
    df_daily = df_daily.merge(df_today_stats, on='symbol', how='left')
    
    # 7. 填充缺失值 (如果是刚上市的新股，没有历史数据，则 N日累计 等于当日，连续天数根据当日正负决定)
    df_daily['inflow_3d'] = df_daily['inflow_3d'].fillna(df_daily['main_net_inflow'])
    df_daily['inflow_5d'] = df_daily['inflow_5d'].fillna(df_daily['main_net_inflow'])
    df_daily['inflow_10d'] = df_daily['inflow_10d'].fillna(df_daily['main_net_inflow'])
    df_daily['inflow_days'] = df_daily['inflow_days'].fillna(0).astype(int)

    # =========================
    # 5. 评分系统（稳定版）
    # =========================
    print("计算资金评分...")

    df_daily["capital_score"] = (
        0.6 * df_daily["main_net_ratio"].rank(pct=True) +
        0.4 * df_daily["main_net_inflow"].rank(pct=True)
    ) * 100

    df_daily["capital_score"] = df_daily["capital_score"].fillna(0).astype(int)

    # 市场排名
    df_daily["rank_market"] = df_daily["main_net_inflow"] \
        .rank(ascending=False, method="min") \
        .astype(int)


    # =========================
    # 6. 股票名称映射
    # =========================
    with engine.connect() as conn:
        df_name = pd.read_sql(
            "SELECT symbol, name AS stock_name FROM stocks",
            conn
        )

    df_final = df_daily.merge(df_name, on="symbol", how="left")

    # =========================
    # 7. 入库（优化 UPSERT）
    # =========================
    if df_final.empty:
        print("无结果")
        return

    print(f"写入 {len(df_final)} 条记录...")

    insert_sql = text("""
        INSERT INTO stk_stock_fund_flow (
            trade_date, symbol, stock_name,
            main_net_inflow,
            super_large_net_inflow,
            large_net_inflow,
            medium_net_inflow,
            small_net_inflow,
            main_net_ratio,
            inflow_3d, inflow_5d, inflow_10d, inflow_days,
            rank_market,
            capital_score
        )
        VALUES (
            :trade_date, :symbol, :stock_name,
            :main_net_inflow,
            :super_large_net_inflow,
            :large_net_inflow,
            :medium_net_inflow,
            :small_net_inflow,
            :main_net_ratio,
            :inflow_3d, :inflow_5d, :inflow_10d, :inflow_days,
            :rank_market,
            :capital_score
        )
        ON DUPLICATE KEY UPDATE
            main_net_inflow = VALUES(main_net_inflow),
            super_large_net_inflow = VALUES(super_large_net_inflow),
            large_net_inflow = VALUES(large_net_inflow),
            medium_net_inflow = VALUES(medium_net_inflow),
            small_net_inflow = VALUES(small_net_inflow),
            main_net_ratio = VALUES(main_net_ratio),
            inflow_3d = VALUES(inflow_3d),
            inflow_5d = VALUES(inflow_5d),
            inflow_10d = VALUES(inflow_10d),
            inflow_days = VALUES(inflow_days),
            rank_market = VALUES(rank_market),
            capital_score = VALUES(capital_score)
    """)

    
    # ===== 1. 修复 NaN =====
    df_final["stock_name"] = df_final["stock_name"].fillna(df_final["symbol"])

    df_final = df_final.replace([np.inf, -np.inf], np.nan)
    df_final = df_final.where(pd.notnull(df_final), None)

    records = df_final.to_dict("records")

    with engine.begin() as conn:
        conn.execute(insert_sql, records)

    print("✅ 个股资金流计算完成（优化版）")


if __name__ == "__main__":
    calculate_stock_fund_flow()