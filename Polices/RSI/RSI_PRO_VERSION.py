import pandas as pd
import pandas_ta as ta
from sqlalchemy import create_engine

# --- 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def screen_rsi_pro():
    print("启动进阶版 RSI 信号筛选：趋势 + 量能 + 形态...")
    
    # 1. 获取大盘状态 (上证指数 000001.SH)
    idx_query = "SELECT close FROM stk_daily_kline WHERE symbol='000001.SH' ORDER BY trade_date DESC LIMIT 30"
    df_idx = pd.read_sql(idx_query, engine)
    idx_ma20 = df_idx['close'].mean()
    idx_now = df_idx['close'].iloc[0]
    
    if idx_now < idx_ma20:
        print("⚠️ 警告：大盘处于弱势区，策略将执行极其严格的过滤。")
        strict_mode = True
    else:
        strict_mode = False

    # 2. 读取个股数据
    query = """
    SELECT k.symbol, s.name, k.trade_date, k.close, k.low, k.volume
    FROM stk_daily_kline k
    JOIN stocks s ON k.symbol = s.symbol
    WHERE k.trade_date >= DATE_SUB(CURDATE(), INTERVAL 120 DAY)
      AND (k.symbol LIKE '60%%' OR k.symbol LIKE '00%%')
    ORDER BY k.symbol, k.trade_date ASC
    """
    df_all = pd.read_sql(query, engine)
    
    results = []

    for symbol, df in df_all.groupby('symbol'):
        if len(df) < 60: continue
        
        # --- A. 指标计算 ---
        df['rsi6'] = ta.rsi(df['close'], length=6)
        df['rsi12'] = ta.rsi(df['close'], length=12)
        df['ma60'] = ta.sma(df['close'], length=60)
        df['v_ma10'] = ta.sma(df['volume'], length=10)
        
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        
        # --- B. 三重安全锁判断 ---
        
        # 1. 趋势锁：股价必须在 60 日均线附近或上方 (拒绝阴跌股)
        is_trend_ok = curr['close'] > curr['ma60'] * 0.95
        
        # 2. 量能锁：今天必须比 10 日均量放大 1.2 倍以上
        is_vol_ok = curr['volume'] > curr['v_ma10'] * 1.2
        
        # 3. 信号锁：底背离 + 低位金叉 (组合信号胜率更高)
        # 股价10日新低，但RSI未创新低
        low_10 = df['low'].tail(10).min()
        rsi_low_10 = df['rsi6'].tail(10).min()
        is_divergence = (curr['low'] <= low_10) and (curr['rsi6'] > rsi_low_10)
        
        # 低位金叉
        is_gold_cross = (curr['rsi6'] > curr['rsi12']) and (prev['rsi6'] <= prev['rsi12']) and (curr['rsi6'] < 45)

        # --- C. 综合筛选 ---
        if is_trend_ok and is_vol_ok:
            if is_divergence or is_gold_cross:
                # 如果是大盘弱势，要求必须两个信号同时出现
                if strict_mode and not (is_divergence and is_gold_cross):
                    continue
                    
                results.append({
                    '代码': symbol,
                    '名称': df['name'].iloc[0],
                    '状态': "底背离" if is_divergence else "低位金叉",
                    'RSI6': round(curr['rsi6'], 2),
                    '放量倍数': round(curr['volume']/curr['v_ma10'], 2),
                    '距MA60': f"{round((curr['close']-curr['ma60'])/curr['ma60']*100, 2)}%"
                })

    # 3. 输出结果
    if results:
        print(pd.DataFrame(results).to_string(index=False))
    else:
        print("今日未发现高价值信号。")

if __name__ == "__main__":
    screen_rsi_pro()