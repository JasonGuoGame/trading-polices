import pandas as pd
import numpy as np
from sqlalchemy import create_engine
import datetime

# --- 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def find_pattern_in_stock(symbol, date_str):
    """
    在单只股票中检索“放量上穿均线”形态
    """
    query = f"""
    SELECT trade_time, close, amount, volume 
    FROM stk_min_kline 
    WHERE symbol = '{symbol}' AND DATE(trade_time) = '{date_str}'
    ORDER BY trade_time ASC
    """
    df = pd.read_sql(query, engine)
    if len(df) < 60: return None

    # 1. 计算分时均价线 (VWAP)
    df['cum_amount'] = df['amount'].cumsum()
    df['cum_vol'] = df['volume'].cumsum()
    df['vwap'] = df['cum_amount'] / (df['cum_vol'] + 0.001)

    # 2. 计算成交量均线 (用于判断放量)
    df['vol_ma10'] = df['volume'].rolling(10).mean()

    signals = []
    
    # 3. 遍历每一分钟寻找突破点 (避开开盘前10分钟)
    for i in range(15, len(df)):
        curr = df.iloc[i]
        prev = df.iloc[i-1]
        
        # 条件 A: 上穿均线
        is_cross_up = (prev['close'] <= prev['vwap']) and (curr['close'] > curr['vwap'])
        
        # 条件 B: 之前 10 分钟大部分时间在均线下 (确保是横盘后的突破)
        lookback = df.iloc[i-10:i]
        is_was_below = (lookback['close'] < lookback['vwap']).sum() >= 7
        
        # 条件 C: 明显放量 (当前量 > 10分钟均量的2.5倍)
        is_vol_spike = curr['volume'] > (prev['vol_ma10'] * 2.5)
        
        # 条件 D: 价格力度 (这一分钟涨幅 > 0.3%)
        is_strong = (curr['close'] - prev['close']) / prev['close'] > 0.003

        if is_cross_up and is_was_below and is_vol_spike and is_strong:
            signals.append({
                'time': curr['trade_time'].strftime('%H:%M'),
                'price': curr['close'],
                'vol_ratio': round(curr['volume'] / prev['vol_ma10'], 2)
            })
            
    return signals if signals else None

def run_pattern_scanner():
    # 获取最近一个交易日
    latest_date = pd.read_sql("SELECT MAX(DATE(trade_time)) FROM stk_min_kline", engine).iloc[0,0]
    print(f"正在分析 {latest_date} 全市场‘放量过均线’形态...")

    # 只扫描主板股票
    symbols_query = f"SELECT DISTINCT symbol FROM stk_min_kline WHERE DATE(trade_time) = '{latest_date}' AND (symbol LIKE '60%%' OR symbol LIKE '00%%')"
    symbols = pd.read_sql(symbols_query, engine)['symbol'].tolist()
    
    match_count = 0
    for i, sym in enumerate(symbols):
        try:
            hits = find_pattern_in_stock(sym, latest_date)
            if hits:
                print(f"\n🎯 发现匹配股票: {sym}")
                for h in hits:
                    print(f"   时间: {h['time']} | 价格: {h['price']} | 放量: {h['vol_ratio']}倍")
                match_count += 1
        except:
            continue
            
        if (i+1) % 500 == 0:
            print(f"已扫描 {i+1} 只股票...")

    print(f"\n扫描结束，共找到 {match_count} 只形态匹配的股票。")

if __name__ == "__main__":
    run_pattern_scanner()