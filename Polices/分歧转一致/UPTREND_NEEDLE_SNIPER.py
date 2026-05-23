import pandas as pd
import pandas_ta as ta
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def screen_uptrend_needle_polished():
    print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 启动 V3.0 深度趋势+分时洗盘扫描...")

    # 1. 步骤一：通过 SQL 提取【严格上升趋势】的股票清单
    # 逻辑：收盘 > MA20 且 MA20 趋势向上 且 DIF > 0
    # 我们从日线表提取最近一段时间数据来判定趋势
    print("正在扫描‘全市场上升趋势’通道...")
    
    # 这一步通过 SQL 直接算出均线斜率，极大减轻 Python 压力
    trend_sql = text("""
        SELECT symbol, close, name 
        FROM (
            SELECT k.symbol, k.close, s.name,
                   AVG(k.close) OVER(PARTITION BY k.symbol ORDER BY k.trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) as ma20,
                   AVG(k.close) OVER(PARTITION BY k.symbol ORDER BY k.trade_date ROWS BETWEEN 24 PRECEDING AND 5 PRECEDING) as ma20_prev_5d,
                   f.f_macd_dif as dif
            FROM stk_daily_kline k
            JOIN stocks s ON k.symbol = s.symbol
            JOIN stk_factors f ON k.symbol = f.symbol AND k.trade_date = f.trade_date
            WHERE k.trade_date = (SELECT MAX(trade_date) FROM stk_daily_kline)
              AND (k.symbol LIKE '60%' OR k.symbol LIKE '00%')
              AND s.name NOT LIKE '%ST%'
        ) t
        WHERE close > ma20               -- 价格在均线上方
          AND ma20 > ma20_prev_5d       -- 均线斜率向上 (5天对比)
          AND dif > 0                   -- 动能多头
    """)

    with engine.connect() as conn:
        df_trend = pd.read_sql(trend_sql, conn)
        # 获取昨收映射用于今日红盘判定
        yest_date_res = conn.execute(text("SELECT DISTINCT trade_date FROM stk_daily_kline ORDER BY trade_date DESC LIMIT 1 OFFSET 1")).fetchone()
        yest_date = yest_date_res[0]
        df_prev = pd.read_sql(text(f"SELECT symbol, close as prev_close FROM stk_daily_kline WHERE trade_date = '{yest_date}'"), conn)
    
    prev_close_map = dict(zip(df_prev['symbol'], df_prev['prev_close']))
    trend_symbols = df_trend['symbol'].tolist()
    name_map = dict(zip(df_trend['symbol'], df_trend['name']))
    
    print(f"发现处于‘健康上升通道’的个股共 {len(trend_symbols)} 只。开始分析今日分时形态...")

    # 2. 步骤二：分析 14:40 的分时“深 V”特征
    # 我们关注：今天是否跌下去后又被主力强行收回，并最终红盘
    latest_min_date = pd.read_sql("SELECT MAX(DATE(trade_time)) FROM stk_min_kline", engine).iloc[0,0]
    
    results = []
    batch_size = 100
    for i in range(0, len(trend_symbols), batch_size):
        chunk = trend_symbols[i : i + batch_size]
        symbols_str = "','".join(chunk)
        
        # 提取今日统计：开盘价、最高价、最低价、最新价
        min_query = f"""
            SELECT symbol, 
                   MIN(low) as t_low, MAX(high) as t_high,
                   (SELECT close FROM stk_min_kline WHERE symbol=t.symbol AND DATE(trade_time)='{latest_min_date}' ORDER BY trade_time ASC LIMIT 1) as t_open,
                   (SELECT close FROM stk_min_kline WHERE symbol=t.symbol AND DATE(trade_time)='{latest_min_date}' ORDER BY trade_time DESC LIMIT 1) as t_curr
            FROM stk_min_kline t
            WHERE symbol IN ('{symbols_str}') AND DATE(trade_time)='{latest_min_date}'
            GROUP BY symbol
        """
        df_today = pd.read_sql(min_query, engine)

        for _, row in df_today.iterrows():
            sym = row['symbol']
            curr_p = row['t_curr']
            open_p = row['t_open']
            low_p = row['t_low']
            high_p = row['t_high']
            yest_p = prev_close_map.get(sym)

            if not curr_p or not yest_p or high_p == low_p: continue

            # --- 精研判定逻辑 ---
            
            # A. 红盘上涨确认 (Polished: 既要阳线也要涨幅)
            is_truly_strong = (curr_p > yest_p) and (curr_p > open_p)
            
            # B. 长下影形态 (Polished: 针长、比例、实体位置)
            body_size = abs(curr_p - open_p)
            # 支撑点到针尖的长度
            lower_shadow = min(open_p, curr_p) - low_p
            # 全天总振幅
            total_range = high_p - low_p
            
            # 严格筛选：下影线 > 实体 3 倍 且 占据全天振幅的 65% 以上
            is_needle = (lower_shadow > body_size * 3.0) and (lower_shadow / total_range > 0.65)
            
            # C. 价格位置 (Polished: 收盘在全天的高位区，说明抢筹反击彻底)
            is_near_high = (high_p - curr_p) / total_range < 0.2

            if is_truly_strong and is_needle and is_near_high:
                results.append({
                    '代码': sym,
                    '名称': name_map.get(sym),
                    '现价': curr_p,
                    '今日涨幅%': round((curr_p - yest_p) / yest_p * 100, 2),
                    '下影占比%': round(lower_shadow / total_range * 100, 1),
                    '振幅%': round(total_range / yest_p * 100, 2)
                })

    # 3. 输出报告
    if results:
        res_df = pd.DataFrame(results).sort_values('今日涨幅%', ascending=False)
        print("\n" + "🚀" * 12 + " 上升趋势 · 探底回升 · 抢筹精选 " + "🚀" * 12)
        print("-" * 100)
        print(res_df.to_string(index=False))
        print("-" * 100)
        print("💡 操盘逻辑：")
        print("1. 均线确认：这些股票 MA20 趋势向上，大环境安全。")
        print("2. 盘中洗盘：今日出现暴力下杀后又被主力高位收回，长影线证明下方支撑极强。")
        print("3. 套利点：14:50 介入，博取明天早盘的主力惯性拉升溢价。")
    else:
        print("\n今日全市场未发现符合‘强势通道+长下影红盘’的高质量标的。")

if __name__ == "__main__":
    screen_uptrend_needle_polished()