import pandas as pd
import numpy as np
from sqlalchemy import create_engine
import datetime

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def analyze_tail_logic(df):
    """
    增加了严格空值处理的版本
    """
    # 1. 基础清洗：删除任何包含空价格或空量的行
    df = df.dropna(subset=['close', 'amount', 'volume']).copy()
    
    # 确保全天数据点足够（240分钟为满额，这里设200容错）
    if len(df) < 200: 
        return None

    try:
        # 2. 计算分时均线 (VWAP)
        # 注意：使用 np.where 或 fillna 确保没有除以0的情况
        df['cum_amount'] = df['amount'].cumsum()
        df['cum_vol'] = df['volume'].cumsum()
        df['vwap'] = df['cum_amount'] / (df['cum_vol'] + 0.0001)

        # 3. 提取时间段
        df['time'] = df['trade_time'].dt.time
        mid_mask = (df['time'] >= datetime.time(10, 30)) & (df['time'] < datetime.time(14, 30))
        tail_mask = (df['time'] >= datetime.time(14, 30))
        
        midday_df = df[mid_mask]
        tail_df = df[tail_mask]
        
        if midday_df.empty or tail_df.empty: 
            return None

        # --- 获取关键数值并进行空值检测 ---
        last_close = tail_df['close'].iloc[-1]
        last_vwap = tail_df['vwap'].iloc[-1]
        p_1430 = tail_df['close'].iloc[0]
        
        # 如果计算结果是 NaN，直接跳过这只股票
        if pd.isna(last_vwap) or pd.isna(last_close) or pd.isna(p_1430):
            return None

        # --- 核心指标判断 ---
        
        # 指标1：收盘价高于全天均线
        is_above_vwap = float(last_close) > float(last_vwap)
        
        # 指标2：尾盘放量倍数
        mid_vol_avg = midday_df['volume'].mean()
        tail_vol_avg = tail_df['volume'].mean()
        if mid_vol_avg == 0: return None # 避免除以0
        vol_multiple = tail_vol_avg / mid_vol_avg
        is_vol_ok = vol_multiple > 1.2 # 放宽到 1.2 倍
        
        # 指标3：趋势向上
        is_rising = last_close > p_1430

        # --- 最终判断 ---
        if is_above_vwap and is_vol_ok and is_rising:
            return {
                'close': round(float(last_close), 2),
                'above_vwap_pct': round((float(last_close) - float(last_vwap)) / float(last_vwap) * 100, 2),
                'vol_multi': round(float(vol_multiple), 2),
                'tail_rise_pct': round((float(last_close) - float(p_1430)) / float(p_1430) * 100, 2)
            }
            
    except Exception as e:
        # 记录错误原因，但不中断程序
        # print(f"计算出错: {e}")
        return None
        
    return None

# run_scanner 函数保持不变...

def run_scanner():
    # 获取数据库里最新的日期
    try:
        latest_date_query = "SELECT MAX(DATE(trade_time)) FROM stk_min_kline"
        latest_date = pd.read_sql(latest_date_query, engine).iloc[0, 0]
        print(f"正在扫描日期: {latest_date} 的分时形态...")
    except:
        print("数据库无数据。")
        return

    # 获取全市场代码
    symbols_query = f"SELECT DISTINCT symbol FROM stk_min_kline WHERE DATE(trade_time) = '{latest_date}'"
    symbols = pd.read_sql(symbols_query, engine)['symbol'].tolist()
    
    results = []
    print(f"共监控 {len(symbols)} 只股票，开始计算...")

    for i, sym in enumerate(symbols):
        query = f"""
        SELECT trade_time, close, amount, volume 
        FROM stk_min_kline 
        WHERE symbol = '{sym}' AND DATE(trade_time) = '{latest_date}'
        ORDER BY trade_time ASC
        """
        df_stock = pd.read_sql(query, engine)
        
        res = analyze_tail_logic(df_stock)
        if res:
            res['symbol'] = sym
            results.append(res)
        
        if (i+1) % 500 == 0:
            print(f"已扫描 {i+1} 只...")

    if results:
        res_df = pd.DataFrame(results).sort_values('vol_multi', ascending=False)
        print("\n" + "✅" * 15)
        print(f"🚀 {latest_date} 尾盘抢筹名单：")
        print(res_df.head(20).to_string(index=False))
    else:
        print("\n❌ 未能筛选出股票。可能原因：今日行情普遍低迷，或数据库中该日期的数据不完整。")

if __name__ == "__main__":
    run_scanner()