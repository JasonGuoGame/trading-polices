import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def get_recent_dates(table, count=2):
    with engine.connect() as conn:
        res = conn.execute(text(f"SELECT DISTINCT trade_date FROM {table} ORDER BY trade_date DESC LIMIT {count}")).fetchall()
        return [row[0] for row in res]

def screen_short_term_acceleration():
    print(f"[{datetime.datetime.now()}] 启动 V6.0【板块资金加速+短线异动】模型...")
    
    # 1. 确定最近两个交易日
    dates = get_recent_dates('stk_sector_fund_flow', 2)
    if len(dates) < 2:
        print("资金流向数据不足，无法计算趋势。")
        return
    today, yesterday = dates[0], dates[1]
    print(f"分析周期：昨日({yesterday}) -> 今日({today})")

    # 2. 步骤一：锁定【连续流入且趋势增加】的板块
    # 逻辑：今日流入 > 0 AND 昨日流入 > 0 AND 今日流入 > 昨日流入
    sector_sql = text("""
        SELECT t.sector_name, t.net_inflow_amount as today_amt, y.net_inflow_amount as yest_amt
        FROM stk_sector_fund_flow t
        JOIN stk_sector_fund_flow y ON t.sector_name = y.sector_name AND y.trade_date = :yest
        WHERE t.trade_date = :today
          AND t.net_inflow_amount > 0 
          AND y.net_inflow_amount > 0
          AND t.net_inflow_amount > y.net_inflow_amount
        ORDER BY t.net_inflow_amount DESC
    """)
    
    with engine.connect() as conn:
        trending_sectors_df = pd.read_sql(sector_sql, conn, params={"today": today, "yest": yesterday})
    
    if trending_sectors_df.empty:
        print("💥 今日未发现满足‘双日流入且加速’的板块，市场动能较弱。")
        return
    
    # 获取加速板块名单及其排名
    acc_sectors = trending_sectors_df['sector_name'].tolist()
    print(f"🚀 识别到资金加速流入板块 ({len(acc_sectors)}个): {acc_sectors[:5]} ...")

    # 3. 步骤二：联表提取这些板块内的个股数据 (主板 + 创业板)
    query_sql = text("""
        SELECT 
            f.symbol, s.name, 
            flow.sector_name as sector,
            flow.net_inflow_amount as sector_money,
            COALESCE(ab.surge_count, 0) as surge_count,
            f.f_vol_ratio as vol_ratio,
            f.f_macd_dif, f.f_macd_dea,
            (k.close - k.open)/k.open * 100 as pct_chg
        FROM stk_factors f
        JOIN stocks s ON f.symbol = s.symbol
        JOIN stk_daily_kline k ON f.symbol = k.symbol AND k.trade_date = f.trade_date
        JOIN stock_sector_relation r ON f.symbol = r.symbol
        JOIN stk_sector_fund_flow flow ON (r.sector_name = CONCAT('行业-', flow.sector_name) OR r.sector_name = CONCAT('概念-', flow.sector_name))
        LEFT JOIN stk_capital_abnormal ab ON f.symbol = ab.symbol AND f.trade_date = ab.trade_date
        WHERE f.trade_date = :today AND flow.trade_date = :today
          AND (f.symbol LIKE '60%' OR f.symbol LIKE '00%' OR f.symbol LIKE '30%')
          AND s.name NOT LIKE '%ST%'
          AND flow.sector_name IN :acc_list
    """)
    
    with engine.connect() as conn:
        df = pd.read_sql(query_sql, conn, params={"today": today, "acc_list": acc_sectors})

    if df.empty:
        print("选定板块内未发现符合技术面基础要求的股票。")
        return

    # 去重：一只股票属于多个加速板块时，取流入额最大的板块
    df = df.sort_values('sector_money', ascending=False).drop_duplicates('symbol')

    # --- 4. 核心 V6.0 评分系统 (总分 100) ---
    
    # 指标 1：板块地位 (40分) - 仅在加速板块中排名
    # 加速板块中的第一名40，第二名35，第三名30，其余满足条件的25
    def score_sector(name):
        if name == acc_sectors[0]: return 40
        if name == acc_sectors[1]: return 35
        if name == acc_sectors[2]: return 30
        return 25
    df['s1'] = df['sector'].apply(score_sector)

    # 指标 2：分时脉冲 (20分) - 0-5次映射为0-20分
    df['s2'] = np.clip(df['surge_count'] * 4, 0, 20)

    # 指标 3：量能倍数 (20分) - 1.0-3.0倍映射为0-20分
    df['s3'] = np.clip((df['vol_ratio'] - 1.0) * 10, 0, 20)

    # 指标 4：MACD 金叉确认 (10分) - 0轴上方金叉10，否则0
    df['s4'] = np.where((df['f_macd_dif'] > df['f_macd_dea']) & (df['f_macd_dif'] > 0), 10, 0)

    # 指标 5：日内涨幅 (10分) - 0-5%映射为0-10分
    df['s5'] = np.clip(df['pct_chg'] * 2, 0, 10)

    df['total_score'] = df['s1'] + df['s2'] + df['s3'] + df['s4'] + df['s5']

    # --- 5. 结果过滤与输出 ---
    final_picks = df[df['total_score'] >= 80].sort_values('total_score', ascending=False)

    print("\n" + "🚀" * 10 + " V6.0 资金加速共振黑马名单 (Score > 80) " + "🚀" * 10)
    print("-" * 110)
    if not final_picks.empty:
        display_df = final_picks[['symbol', 'name', 'sector', 'total_score', 'surge_count', 'vol_ratio', 'pct_chg']]
        display_df.columns = ['代码', '名称', '加速板块', '综合分', '脉冲', '量比', '涨幅%']
        print(display_df.to_string(index=False))
        
        print("-" * 110)
        print("💡 操盘逻辑：这些板块的主力资金昨天在买，今天‘买得更多’，属于加速抢筹阶段。")
        print("💡 建议关注：综合分 > 90 且处于创业板（30开头）的标的，其日内弹性最大。")
    else:
        print("今日虽有加速板块，但板块内个股异动不明显，建议保持观望。")

if __name__ == "__main__":
    screen_short_term_acceleration()