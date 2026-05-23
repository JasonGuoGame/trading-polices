import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime

# --- 1. 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def calculate_market_trend_metrics():
    print(f"[{datetime.datetime.now()}] 正在评估趋势股赚钱效应...")

    with engine.connect() as conn:
        # A. 获取最近两个交易日
        dates = conn.execute(text("SELECT DISTINCT trade_date FROM stk_daily_kline ORDER BY trade_date DESC LIMIT 2")).fetchall()
        if len(dates) < 2: return
        today, yesterday = dates[0][0], dates[1][0]

        # B. 核心逻辑：计算趋势股数量与收益
        # 我们定义“趋势股”为：收盘 > MA20 且 MACD_DIF > 0 (多头趋势)
        # 我们定义“均线多头”为：MA5 > MA10 > MA20
        # 我们定义“创新高”为：收盘价 > 过去20日最高价
        
        sql = text("""
            WITH RawMetrics AS (
                SELECT 
                    k.symbol, k.trade_date, k.close, k.high,
                    f.f_bb_m as ma20,
                    f.f_macd_dif as dif,
                    -- 计算 MA5 和 MA10 (如果因子表没有，直接用窗口函数算)
                    AVG(k.close) OVER(PARTITION BY k.symbol ORDER BY k.trade_date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) as ma5,
                    AVG(k.close) OVER(PARTITION BY k.symbol ORDER BY k.trade_date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW) as ma10,
                    -- 计算过去 20 日最高价
                    MAX(k.high) OVER(PARTITION BY k.symbol ORDER BY k.trade_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) as prev_20d_high
                FROM stk_daily_kline k
                JOIN stk_factors f ON k.symbol = f.symbol AND k.trade_date = f.trade_date
                WHERE k.trade_date >= DATE_SUB(:today, INTERVAL 40 DAY)
                  AND (k.symbol LIKE '60%' OR k.symbol LIKE '00%')
            )
            SELECT * FROM RawMetrics WHERE trade_date IN (:today, :yesterday)
        """)
        df_all = pd.read_sql(sql, conn, params={"today": today, "yesterday": yesterday})

    # 分离今天和昨天
    df_today = df_all[df_all['trade_date'] == today].copy()
    df_yest = df_all[df_all['trade_date'] == yesterday].copy()

    # --- 1. 计算今日指标 ---
    # 趋势股：站稳 20 日线 且 MACD 在 0 轴上
    trend_stocks = df_today[(df_today['close'] > df_today['ma20']) & (df_today['dif'] > 0)]
    trend_stock_count = len(trend_stocks)
    
    # 均线多头：MA5 > MA10 > MA20
    ma_multibull_count = len(df_today[(df_today['ma5'] > df_today['ma10']) & (df_today['ma10'] > df_today['ma20'])])
    
    # 创新高数量：今日收盘 > 过去20日最高
    new_high_count = len(df_today[df_today['close'] > df_today['prev_20d_high']])

    # --- 2. 计算趋势股平均收益 (核心回答：好不好做) ---
    # 逻辑：昨天符合“趋势股”定义的股票，在今天的平均涨幅
    yest_trend_symbols = df_yest[(df_yest['close'] > df_yest['ma20']) & (df_yest['dif'] > 0)]['symbol'].tolist()
    
    trend_avg_return = 0
    if yest_trend_symbols:
        # 匹配这些股票今天的表现
        today_perf = df_today[df_today['symbol'].isin(yest_trend_symbols)]
        # 关联昨收
        perf_merged = pd.merge(today_perf, df_yest[['symbol', 'close']], on='symbol', suffixes=('', '_yest'))
        trend_avg_return = (perf_merged['close'] / perf_merged['close_yest'] - 1).mean() * 100

    # --- 3. 汇总存入数据库 ---
    result = {
        'trade_date': today,
        'trend_stock_count': int(trend_stock_count),
        'new_high_count': int(new_high_count),
        'ma_multibull_count': int(ma_multibull_count),
        'trend_avg_return': float(round(trend_avg_return, 2))
    }

    df_save = pd.DataFrame([result])
    
    try:
        with engine.begin() as conn:
            df_save.to_sql('temp_trend_metrics', con=conn, if_exists='replace', index=False)
            upsert_sql = text("""
                INSERT INTO market_trend_metrics (trade_date, trend_stock_count, new_high_count, ma_multibull_count, trend_avg_return)
                SELECT * FROM temp_trend_metrics
                ON DUPLICATE KEY UPDATE 
                    trend_stock_count = VALUES(trend_stock_count),
                    new_high_count = VALUES(new_high_count),
                    ma_multibull_count = VALUES(ma_multibull_count),
                    trend_avg_return = VALUES(trend_avg_return);
            """)
            conn.execute(upsert_sql)
            conn.execute(text("DROP TABLE IF EXISTS temp_trend_metrics;"))
        
        # --- 输出报告 ---
        print("\n" + "🌊" * 10 + f" 今日趋势环境报告 ({today}) " + "🌊" * 10)
        print("-" * 75)
        print(f"📈 趋势股总数: {trend_stock_count:<6} | 🚀 创新高家数: {new_high_count}")
        print(f"🐂 均线多头数: {ma_multibull_count:<6} | 💰 趋势股今日平均收益: {trend_avg_return:+.2f}%")
        print("-" * 75)
        
        # 核心逻辑研判
        if trend_avg_return > 1.5 and trend_stock_count > 1000:
            status = "✅ 趋势主升浪！持股待涨是最佳选择，‘抱团’效应明显。"
        elif trend_avg_return > 0:
            status = "⚖️ 趋势缓和。机构资金小步慢跑，适合低吸趋势股。"
        else:
            status = "❌ 趋势杀跌！昨日强势趋势股今日集体回撤。操作：减仓，防范假突破。"
        print(f"💡 结论：{status}\n")

    except Exception as e:
        print(f"❌ 数据库写入失败: {e}")

if __name__ == "__main__":
    calculate_market_trend_metrics()