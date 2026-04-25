import pandas as pd
import numpy as np
from sqlalchemy import create_engine
import datetime

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def analyze_tail_action(df):
    """
    分析分时数据是否符合：尾盘高于均线、逐步向上、温和放量 (增强版)
    """
    # 1. 预处理：删除任何包含空值的行，确保计算不会因 None 崩溃
    df = df.dropna(subset=['close', 'volume', 'amount']).copy()
    
    # 确保全天至少有 200 分钟的数据（允许少量缺失，但不能太离谱）
    if len(df) < 200: return None

    # 2. 计算当日分时均线 (VWAP)
    df['cum_amount'] = df['amount'].cumsum()
    df['cum_vol'] = df['volume'].cumsum()
    df['vwap'] = df['cum_amount'] / (df['cum_vol'] + 1) # 防止除以0

    # 3. 提取时间段
    df['time'] = df['trade_time'].dt.time
    
    # 盘中阶段 (10:30 - 14:30) 的成交量作为基准
    midday_mask = (df['time'] > datetime.time(10, 30)) & (df['time'] < datetime.time(14, 30))
    midday_vol = df[midday_mask]['volume'].mean()
    if pd.isna(midday_vol) or midday_vol == 0: return None
    
    # 尾盘阶段 (14:30 - 15:00)
    tail_df = df[df['time'] >= datetime.time(14, 30)].copy()
    if len(tail_df) < 15: return None # 最后半小时至少得有 15 分钟的数据

    # --- 条件判断逻辑 ---
    
    # A. 价格始终在均线之上 (允许 10% 的误差波动)
    above_vwap_ratio = (tail_df['close'] >= tail_df['vwap']).mean()
    is_above_vwap = above_vwap_ratio > 0.85
    
    # B. 逐步向上 (使用线性回归斜率判断，比对比三点更科学)
    # y = ax + b, 我们看 a 是否大于 0
    y = tail_df['close'].values
    x = np.arange(len(y))
    if len(y) > 1:
        slope = np.polyfit(x, y, 1)[0]
    else:
        slope = 0
    is_trending_up = slope > 0
    
    # C. 温和放量
    tail_vol_avg = tail_df['volume'].mean()
    # 判定标准：尾盘量是盘中均量的 1.5~5 倍
    is_gentle_volume = (midday_vol * 1.5 <= tail_vol_avg <= midday_vol * 5.0)

    # D. 涨幅确认 (最后30分钟要有真实的升幅，比如 > 0.3%)
    p_start = tail_df['close'].iloc[0]
    p_end = tail_df['close'].iloc[-1]
    actual_rise = (p_end - p_start) / p_start

    if is_above_vwap and is_trending_up and is_gentle_volume and actual_rise > 0.003:
        return {
            'final_price': round(p_end, 2),
            'rising_pct': round(actual_rise * 100, 2),
            'vol_multiple': round(tail_vol_avg / midday_vol, 2),
            'slope': round(slope, 5)
        }
    return None

    """
    分析分时数据是否符合：尾盘高于均线、逐步向上、温和放量
    """
    if len(df) < 240: return False # 确保有全天数据

    # 1. 计算当日分时均线 (VWAP)
    df['cum_amount'] = df['amount'].cumsum()
    df['cum_vol'] = df['volume'].cumsum()
    df['vwap'] = df['cum_amount'] / df['cum_vol']

    # 2. 提取时间段
    # 盘中阶段 (10:30 - 14:30) 用于计算基准成交量
    df['time'] = df['trade_time'].dt.time
    midday_vol = df[(df['time'] > datetime.time(10, 30)) & (df['time'] < datetime.time(14, 30))]['volume'].mean()
    
    # 尾盘阶段 (14:30 - 15:00)
    tail_df = df[df['time'] >= datetime.time(14, 30)].copy()
    
    if len(tail_df) < 30: return False

    # --- 条件判断 ---
    
    # A. 价格始终在均线之上 (最后30分钟内，90%的时间在均线上方)
    above_vwap = (tail_df['close'] > tail_df['vwap']).mean() > 0.9
    
    # B. 逐步向上 (比较 14:30, 14:45, 15:00 三个点的价格)
    p_1430 = tail_df['close'].iloc[0]
    p_1445 = tail_df['close'].iloc[15]
    p_1500 = tail_df['close'].iloc[-1]
    is_trending_up = p_1500 > p_1445 > p_1430
    
    # C. 温和放量
    tail_vol_avg = tail_df['volume'].mean()
    # 判定标准：尾盘量是盘中均量的 1.5~4 倍
    is_gentle_volume = (midday_vol * 1.5 <= tail_vol_avg <= midday_vol * 4.0)

    if above_vwap and is_trending_up and is_gentle_volume:
        return {
            'final_price': p_1500,
            'rising_pct': round((p_1500 - p_1430) / p_1430 * 100, 2),
            'vol_multiple': round(tail_vol_avg / midday_vol, 2)
        }
    return None

def run_tail_scanner():
    # 1. 获取数据库最新交易日
    latest_date = pd.read_sql("SELECT MAX(DATE(trade_time)) FROM stk_min_kline", engine).iloc[0,0]
    print(f"正在扫描日期: {latest_date} 的尾盘抢筹形态...")

    # 2. 获取当天所有股票代码
    symbols = pd.read_sql(f"SELECT DISTINCT symbol FROM stk_min_kline WHERE DATE(trade_time) = '{latest_date}'", engine)['symbol'].tolist()
    
    results = []
    for i, sym in enumerate(symbols):
        # 逐个读取股票全天分时数据
        query = f"SELECT trade_time, close, amount, volume FROM stk_min_kline WHERE symbol = '{sym}' AND DATE(trade_time) = '{latest_date}' ORDER BY trade_time ASC"
        df_stock = pd.read_sql(query, engine)
        
        res = analyze_tail_action(df_stock)
        if res:
            res['symbol'] = sym
            results.append(res)
        
        if i % 100 == 0: print(f"已扫描 {i}/{len(symbols)} 只股票...")

    # 3. 展示结果
    if results:
        final_df = pd.DataFrame(results).sort_values('rising_pct', ascending=False)
        print("\n" + "🔥" * 15)
        print(f"🚀 {latest_date} 尾盘抢筹（温和放量且站稳均线）名单：")
        print("-" * 60)
        print(final_df[['symbol', 'final_price', 'rising_pct', 'vol_multiple']].to_string(index=False))
        print("🔥" * 15)
    else:
        print("\n今日暂未发现符合尾盘抢筹形态的个股。")

if __name__ == "__main__":
    run_tail_scanner()