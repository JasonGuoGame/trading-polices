import pandas as pd
import pandas_ta as ta
from sqlalchemy import create_engine
import numpy as np

# --- 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def screen_rsi_signals():
    print("开始从数据库读取数据并扫描 RSI 信号...")
    
    # 1. 读取最近 60 天的日线数据 (用于判断趋势和背离)
    # 过滤主板且非ST
    query = """
    SELECT k.symbol, s.name, k.trade_date, k.close, k.low
    FROM stk_daily_kline k
    JOIN stocks s ON k.symbol = s.symbol
    WHERE k.trade_date >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)
      AND (k.symbol LIKE '60%%' OR k.symbol LIKE '00%%')
      AND s.name NOT LIKE '%%ST%%'
      AND s.name NOT LIKE '%%退%%'
    ORDER BY k.symbol, k.trade_date ASC
    """
    df_all = pd.read_sql(query, engine)
    
    results = []

    # 2. 按股票分组分析
    for symbol, df in df_all.groupby('symbol'):
        if len(df) < 30: continue
        
        name = df['name'].iloc[0]
        
        # 计算 RSI-6 和 RSI-12
        df['rsi6'] = ta.rsi(df['close'], length=6)
        df['rsi12'] = ta.rsi(df['close'], length=12)
        
        # 提取最近三天的值用于判断
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        
        # --- 信号 1: RSI 低位金叉 ---
        # 逻辑：6日上穿12日，且6日RSI < 35 (低位)
        is_gold_cross = (curr['rsi6'] > curr['rsi12']) and \
                        (prev['rsi6'] <= prev['rsi12']) and \
                        (curr['rsi6'] < 40)

        # --- 信号 2: RSI 双重底 ---
        # 逻辑：在过去20天内，RSI两次触及低位(30以下)且数值相近(差异<2)
        # 这里使用局部最小值识别
        low_rsi_values = df['rsi6'].tail(20)
        # 寻找局部低点（简化逻辑：找到两个低于 30 的波谷）
        is_double_bottom = False
        if curr['rsi6'] < 40 and prev['rsi6'] < curr['rsi6']: # 正在从低位反弹
             recent_lows = low_rsi_values[low_rsi_values < 30]
             if len(recent_lows) >= 2:
                 # 检查最近两个低点的距离和价差
                 if abs(recent_lows.iloc[-1] - recent_lows.iloc[-2]) < 3:
                     is_double_bottom = True

        # --- 信号 3: RSI 底背离 ---
        # 逻辑：股价创新低（比前次低点更低），但 RSI 没创新低（比前次 RSI 低位更高）
        # 寻找最近两个波谷价格和对应的 RSI
        # 简化版：对比今日和5天前的状态
        past_10d_low_price = df['low'].tail(10).min()
        past_10d_low_rsi = df['rsi6'].tail(10).min()
        
        is_divergence = False
        # 如果今日股价是10日新低，但RSI不是10日新低，且RSI已经开始拐头
        if (curr['low'] <= past_10d_low_price) and \
           (curr['rsi6'] > past_10d_low_rsi) and \
           (curr['rsi6'] > prev['rsi6']):
            is_divergence = True

        # --- 汇总结果 ---
        if is_gold_cross or is_double_bottom or is_divergence:
            signal_type = []
            if is_gold_cross: signal_type.append("低位金叉")
            if is_double_bottom: signal_type.append("双重底")
            if is_divergence: signal_type.append("底背离")
            
            results.append({
                '代码': symbol,
                '名称': name,
                '信号': " + ".join(signal_type),
                'RSI6': round(curr['rsi6'], 2),
                '收盘价': curr['close']
            })

    # 3. 打印结果
    if results:
        res_df = pd.DataFrame(results)
        print("\n" + "🔥" * 20)
        print(f"🚀 RSI 技术指标买入预警清单")
        print("-" * 60)
        print(res_df.to_string(index=False))
        print("🔥" * 20)
    else:
        print("\n今日全市场未发现标准的 RSI 买入信号。")

if __name__ == "__main__":
    screen_rsi_signals()