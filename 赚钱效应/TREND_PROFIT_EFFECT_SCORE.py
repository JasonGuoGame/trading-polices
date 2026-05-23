import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def analyze_trend_effect():
    print(f"[{datetime.datetime.now()}] 正在分析‘机构趋势’模式赚钱效应...")

    with engine.connect() as conn:
        # 1. 获取最近两个交易日
        date_res = conn.execute(text("SELECT DISTINCT trade_date FROM stk_daily_kline ORDER BY trade_date DESC LIMIT 2")).fetchall()
        if len(date_res) < 2: return
        today, yesterday = date_res[0][0], date_res[1][0]

        # 2. 提取全市场趋势指标数据 (利用因子表)
        # 统计：多头排列家数、MACD在0轴上方家数
        factor_sql = text("""
            SELECT 
                COUNT(*) as total_cnt,
                SUM(CASE WHEN f_macd_dif > 0 THEN 1 ELSE 0 END) as macd_up_cnt,
                SUM(CASE WHEN f_ma_cohesion < 0.04 THEN 1 ELSE 0 END) as cohesion_cnt
            FROM stk_factors 
            WHERE trade_date = :today
        """)
        f_res = conn.execute(factor_sql, {"today": today}).fetchone()
        
        # 3. 计算“追涨赚钱效应” (关键指标)
        # 逻辑：找出昨日创20日新高的股票，看它们今天的表现
        chase_logic_sql = text("""
            WITH YestNewHigh AS (
                SELECT symbol, close as yest_close
                FROM (
                    SELECT symbol, close, high, trade_date, -- 必须加上这一列
                           MAX(high) OVER(PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) as prev_20d_high
                    FROM stk_daily_kline
                    WHERE trade_date <= :yesterday
                ) t
                WHERE t.trade_date = :yesterday AND t.close > t.prev_20d_high
            )
            SELECT y.symbol, (k.close - y.yest_close)/y.yest_close as today_ret
            FROM YestNewHigh y
            JOIN stk_daily_kline k ON y.symbol = k.symbol AND k.trade_date = :today
        """)
        df_chase = pd.read_sql(chase_logic_sql, conn, params={"today": today, "yesterday": yesterday})
        
        # 4. 统计核心赛道（AI、机器人、算力）的整体强度
        sector_sql = text("""
            SELECT AVG((k.close - k.open)/k.open) as avg_pct
            FROM stk_daily_kline k
            JOIN stock_sector_relation r ON k.symbol = r.symbol
            WHERE k.trade_date = :today
              AND (r.sector_name LIKE '%AI%' OR r.sector_name LIKE '%机器人%' OR r.sector_name LIKE '%算力%')
        """)
        sector_ret = conn.execute(sector_sql, {"today": today}).fetchone()[0] or 0

    # --- 3. 评分模型 (100分制) ---
    
    # A. 趋势广度分 (30分)
    # 修正：将 Decimal 转换为 float
    macd_ratio = float(f_res[1]) / float(f_res[0]) 
    score_breadth = np.clip(macd_ratio / 0.4 * 30, 0, 30)
    
    # B. 追涨溢价分 (40分)
    if not df_chase.empty:
        # Pandas 通常会自动处理，但为了保险，强制转为 float
        avg_chase_ret = float(df_chase['today_ret'].mean()) * 100
        score_chase = np.clip((avg_chase_ret + 2) / 4 * 40, 0, 40)
    else:
        avg_chase_ret = 0
        score_chase = 0
    
    # C. 赛道强度分 (30分)
    # 修正：将 sector_ret 转换为 float
    val_sector_ret = float(sector_ret) if sector_ret is not None else 0
    score_sector = np.clip(val_sector_ret * 100 / 3 * 30, 0, 30)

    total_score = score_breadth + score_chase + score_sector

    # --- 4. 结果输出 ---
    # 打印时也使用格式化确保不报错
    print("\n" + "🌊" * 15)
    print(f"📊 A股【趋势模式】赚钱效应分析 ({today})")
    print("-" * 40)
    print(f"📈 趋势广度 (MACD>0): {macd_ratio*100:.1f}% (得分: {score_breadth:.1f}/30)")
    print(f"🚀 追涨溢价 (昨日新高今日收益): {avg_chase_ret:+.2f}% (得分: {score_chase:.1f}/40)")
    print(f"🤖 核心赛道 (AI/机器人) 平均涨幅: {val_sector_ret*100:+.2f}% (得分: {score_sector:.1f}/30)")
    print("-" * 40)
    print(f"🌡️ 趋势模式综合得分: {total_score:.1f}")

    if total_score >= 70:
        conclusion = "🟢 强：趋势抱团火热！机构资金疯狂介入。策略：顺势而为，守住 10 日线持股。"
    elif total_score >= 45:
        conclusion = "🟡 中：趋势分化。板块内部开始轮动，策略：不宜追高，等回踩支撑位买入。"
    else:
        conclusion = "🔴 弱：趋势撤退，“一追就套”！机构正在兑现利润。策略：减仓，规避高位放量股。"

    print(f"🚩 结论: {conclusion}")
    print("🌊" * 15 + "\n")

if __name__ == "__main__":
    analyze_trend_effect()