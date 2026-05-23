import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def get_market_weather():
    print(f"[{datetime.datetime.now()}] 正在计算全市场【赚钱效应】打分模型...")

    with engine.connect() as conn:
        # 获取最近两个交易日
        dates = conn.execute(text("SELECT DISTINCT trade_date FROM stk_daily_kline ORDER BY trade_date DESC LIMIT 2")).fetchall()
        today, yesterday = dates[0][0], dates[1][0]

        # 1. --- 维度一：连板接力效应 (25分) ---
        # 统计涨停、连板、晋级率
        lu_query = text("""
            SELECT symbol, close, open, 
                   (SELECT close FROM stk_daily_kline WHERE symbol = k.symbol AND trade_date = :yest) as prev_close
            FROM stk_daily_kline k
            WHERE trade_date = :today AND (symbol LIKE '60%' OR symbol LIKE '00%')
        """)
        df_today = pd.read_sql(lu_query, conn, params={"today": today, "yest": yesterday})
        df_today['is_lu'] = df_today['close'] >= (df_today['prev_close'] * 1.098).round(2)
        
        lu_count = df_today['is_lu'].sum() # 今日涨停数
        # 简化版晋级率逻辑（实际应用中需结合昨日连板名单）
        promotion_score = np.clip(lu_count / 60 * 25, 0, 25) # 假设60家涨停为满分

        # 2. --- 维度二：趋势赚钱效应 (25分) ---
        # 逻辑：全市场 MACD > 0 且 价格 > MA20 的股票占比
        trend_query = text("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN f_macd_dif > 0 AND f_ma_cohesion < 0.05 THEN 1 ELSE 0 END) as trend_count
            FROM stk_factors WHERE trade_date = :today
        """)
        trend_res = conn.execute(trend_query, {"today": today}).fetchone()
        trend_ratio = trend_res[1] / trend_res[0]
        trend_score = np.clip(trend_ratio * 100, 0, 25) # 占比25%为满分

        # 3. --- 维度三：主线吸金效应 (25分) ---
        # 逻辑：前三大板块的资金流入强度
        flow_query = text("""
            SELECT SUM(net_inflow_amount) FROM stk_sector_fund_flow 
            WHERE trade_date = :today ORDER BY net_inflow_amount DESC LIMIT 3
        """)
        top3_money = conn.execute(flow_query, {"today": today}).fetchone()[0] or 0
        flow_score = np.clip(top3_money / 30 * 25, 0, 25) # 前三名流入30亿为满分

        # 4. --- 维度四：市场容错率与环境 (25分) ---
        # 逻辑：今日上涨家数比例 + 成交额
        env_query = text("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN close > prev_close THEN 1 ELSE 0 END) as up_count,
                SUM(amount)/1e8 as total_amt
            FROM (
                SELECT close, amount,
                (SELECT close FROM stk_daily_kline WHERE symbol=k.symbol AND trade_date=:yest) as prev_close
                FROM stk_daily_kline k WHERE trade_date = :today
            ) t
        """)
        env_res = conn.execute(env_query, {"today": today, "yest": yesterday}).fetchone()
        up_ratio = env_res[1] / env_res[0]
        amt_score = np.clip(env_res[2] / 10000 * 12.5, 0, 12.5) # 万亿成交给12.5分
        breadth_score = np.clip(up_ratio * 25, 0, 12.5)      # 普涨给12.5分
        market_env_score = amt_score + breadth_score

    # --- 最终汇总 ---
    total_score = promotion_score + trend_score + flow_score + market_env_score

    # --- 结果研判 ---
    print("\n" + "🏮" * 15)
    print(f"📊 A股赚钱效应量化报告 ({today})")
    print("-" * 40)
    print(f"1️⃣ 连板情绪分: {promotion_score:.1f}/25 (涨停 {lu_count} 家)")
    print(f"2️⃣ 趋势抱团分: {trend_score:.1f}/25 (多头占比 {trend_ratio*100:.1f}%)")
    print(f"3️⃣ 主线吸金分: {flow_score:.1f}/25 (Top3流入 {top3_money:.1f} 亿)")
    print(f"4️⃣ 市场容错分: {market_env_score:.1f}/25 (上涨家数 {env_res[1]})")
    print("-" * 40)
    print(f"🌡️ 综合赚钱效应得分: {total_score:.1f}")

    if total_score >= 75:
        weather = "🔥 盛夏模式：全场沸腾，此时不干更待何时？全力参与连板和主线！"
    elif total_score >= 55:
        weather = "☀️ 暖春模式：局部赚钱效应明显，聚焦最强的主线中军。"
    elif total_score >= 40:
        weather = "☁️ 初秋模式：存量博弈，一追就套，只适合低吸和套利。"
    else:
        weather = "❄️ 严冬模式：空仓观望。亏钱效应巨大，低吸都会被活埋。"

    print(f"🚩 交易建议: {weather}")
    print("🏮" * 15 + "\n")

if __name__ == "__main__":
    get_market_weather()