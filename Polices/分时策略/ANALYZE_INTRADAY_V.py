import pandas as pd
import numpy as np
from sqlalchemy import create_engine
import datetime

# --- 配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def analyze_v_shape(symbol, date_str):
    """
    分析单只股票某一天的分时形态
    """
    # 1. 从数据库读取该股当天的分钟数据
    query = f"""
    SELECT trade_time, close, amount, volume 
    FROM stk_min_kline 
    WHERE symbol = '{symbol}' AND DATE(trade_time) = '{date_str}'
    ORDER BY trade_time ASC
    """
    df = pd.read_sql(query, engine)
    
    if len(df) < 200: # 交易时间不完整的（如停牌、新股）跳过
        return None

    # 2. 计算分时均线 (VWAP)
    df['cum_amount'] = df['amount'].cumsum()
    df['cum_volume'] = df['volume'].cumsum()
    df['avg_price'] = df['cum_amount'] / df['cum_volume']
    
    # 3. 提取关键时间段
    df['time'] = df['trade_time'].dt.time
    morning_df = df[(df['time'] >= datetime.time(9, 30)) & (df['time'] <= datetime.time(10, 30))]
    midday_df = df[(df['time'] > datetime.time(10, 30)) & (df['time'] < datetime.time(14, 30))]
    last_30m_df = df[(df['time'] >= datetime.time(14, 30)) & (df['time'] <= datetime.time(15, 0))]

    # --- 逻辑判断 ---
    
    # A. 早盘曾大幅拉升 (最高价比均线高 1.5%)
    has_morning_rally = (morning_df['close'] > morning_df['avg_price'] * 1.015).any()
    
    # B. 盘中曾跌破均线
    has_dipped_below = (midday_df['close'] < midday_df['avg_price']).any()
    
    # C. 尾盘收回并在均线上方收盘
    is_closing_above = (last_30m_df['close'] > last_30m_df['avg_price']).all()
    
    # D. 温和放量 (最后30分钟均量 > 盘中均量的 1.5 倍，但小于早盘均量的 0.8 倍)
    vol_morning = morning_df['volume'].mean()
    vol_midday = midday_df['volume'].mean()
    vol_last = last_30m_df['volume'].mean()
    
    is_gentle_volume = (vol_last > vol_midday * 1.5) and (vol_last < vol_morning * 0.8)

    # 结果输出
    if has_morning_rally and has_dipped_below and is_closing_above and is_gentle_volume:
        return {
            'symbol': symbol,
            'date': date_str,
            'last_price': df['close'].iloc[-1],
            'avg_price': df['avg_price'].iloc[-1]
        }
    return None

def screen_today_v_shape():
    # 获取数据库里最新的日期
    latest_date = pd.read_sql("SELECT MAX(DATE(trade_time)) FROM stk_min_kline", engine).iloc[0,0]
    latest_date = '2026-04-07'
    print(f"正在分析日期: {latest_date} 的分时形态...")

    # 获取当天所有的股票代码
    symbols = pd.read_sql(f"SELECT DISTINCT symbol FROM stk_min_kline WHERE DATE(trade_time) = '{latest_date}'", engine)['symbol'].tolist()
    
    results = []
    for sym in symbols:
        res = analyze_v_shape(sym, latest_date)
        if res:
            results.append(res)
    
    if results:
        print(f"\n✅ 筛选完成！发现 {len(results)} 只符合“均线收复”形态的股票：")
        print(pd.DataFrame(results))
    else:
        print("\n今日暂无符合该分时形态的个股。")

if __name__ == "__main__":
    screen_today_v_shape()