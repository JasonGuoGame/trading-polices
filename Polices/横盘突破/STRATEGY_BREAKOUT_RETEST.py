import pandas as pd
import pandas_ta as ta
from sqlalchemy import create_engine
import datetime

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def screen_breakout_retest():
    print(f"[{datetime.datetime.now()}] 正在全市场扫描“突破回踩”机会...")
    
    # 1. 获取最近 120 天的数据
    query = """
    SELECT * FROM stk_daily_kline 
    WHERE trade_date >= (
        SELECT MIN(t.trade_date) FROM (
            SELECT DISTINCT trade_date FROM stk_daily_kline 
            ORDER BY trade_date DESC LIMIT 120
        ) AS t
    )
    ORDER BY symbol, trade_date ASC
    """
    df_all = pd.read_sql(query, engine)
    
    if df_all.empty:
        print("数据库中没有行情数据。")
        return

    results = []
    
    # 2. 遍历股票进行形态识别
    for symbol, df in df_all.groupby('symbol'):
        if len(df) < 80: continue # 数据量不足
        
        # --- 策略核心参数 ---
        # 1. 寻找前期高点 (以过去 60 天的最高价作为阻力位，但要排除最近 10 天，寻找稍远一点的高点)
        df['prev_60d_high'] = df['high'].shift(10).rolling(60).max()
        
        # 2. 均线系统 (确认上涨行情)
        df['ma60'] = ta.sma(df['close'], length=60)
        
        # 3. 提取最新数据
        curr = df.iloc[-1]
        prev_3d = df.iloc[-5:-1] # 最近几天的表现
        
        # --- 核心判断逻辑 ---
        
        # A. 趋势判断：股价必须在 60 日均线之上（上涨行情）
        is_bullish = curr['close'] > curr['ma60']
        
        # B. 突破确认：最近 10 天内，股价必须曾经大幅突破过这个 prev_60d_high
        # 突破幅度至少 3%
        has_broken_out = (df['close'].tail(10) > curr['prev_60d_high'] * 1.03).any()
        
        # C. 当前是回踩：当前价格正在向那个“前期高点”靠近
        # 距离前期高点在 [-1%, +2%] 之间（即刚好踩在支撑线上）
        support_level = curr['prev_60d_high']
        dist_to_support = (curr['close'] - support_level) / support_level
        is_retesting = -0.01 <= dist_to_support <= 0.02
        
        # D. 缩量回踩：今天的成交量应该小于突破时的成交量，或者小于近期均量
        # 这代表回踩时抛压不重
        vol_ma10 = ta.sma(df['volume'], length=10).iloc[-1]
        is_low_volume = curr['volume'] < vol_ma10 * 1.2 # 回踩不放巨量

        # --- 综合筛选 ---
        if is_bullish and has_broken_out and is_retesting and is_low_volume:
            results.append({
                '代码': symbol,
                '当前价': curr['close'],
                '前期高点(支撑)': round(support_level, 2),
                '回踩距离': f"{round(dist_to_support * 100, 2)}%",
                '60日均线': round(curr['ma60'], 2),
                '状态': '回踩确认中'
            })

    # 3. 输出结果
    if results:
        res_df = pd.DataFrame(results)
        print(f"\n✅ 筛选完成！共发现 {len(res_df)} 只符合“突破回踩”形态的个股：")
        print("-" * 60)
        print(res_df.to_string(index=False))
        print("-" * 60)
        print("💡 操作建议：在支撑位附近企稳（出现小阳线）时介入，止损设在支撑位下方 3%。")
    else:
        print("\n今日全市场未发现符合“突破回踩”形态的个股。")

if __name__ == "__main__":
    screen_breakout_retest()