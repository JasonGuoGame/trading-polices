import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def analyze_intraday_surge(symbol, date_str):
    """
    分析分时数据，检测是否有‘主力脉冲拉升’记录
    """
    query = f"""
    SELECT trade_time, close, volume, amount 
    FROM stk_min_kline 
    WHERE symbol = '{symbol}' AND DATE(trade_time) = '{date_str}'
    ORDER BY trade_time ASC
    """
    df_min = pd.read_sql(query, engine)
    if len(df_min) < 100: return None

    # 1. 计算每分钟的涨幅
    df_min['ret'] = df_min['close'].pct_change()
    # 2. 计算每分钟成交量相对于前 10 分钟均量的倍数
    df_min['vol_ma10'] = df_min['volume'].rolling(10).mean()
    df_min['vol_ratio'] = df_min['volume'] / (df_min['vol_ma10'].shift(1) + 1)

    # 3. 寻找主力脉冲：单分钟涨幅 > 0.8% 且 成交量放大 5 倍以上
    surges = df_min[(df_min['ret'] > 0.008) & (df_min['vol_ratio'] > 5.0)]
    
    if not surges.empty:
        return {
            'surge_count': len(surges),
            'max_surge_ret': round(surges['ret'].max() * 100, 2),
            'surge_times': surges['trade_time'].dt.strftime('%H:%M').tolist()
        }
    return None

def run_capital_monitor():
    print(f"[{datetime.datetime.now()}] 启动资金异动扫描...")

    # 1. 第一步：日线初筛 (利用因子表找今日爆量 2.5 倍以上的股票)
    # 这样可以缩小分时扫描的范围，大幅提高速度
    latest_date = pd.read_sql("SELECT MAX(trade_date) FROM stk_factors", engine).iloc[0,0]
    
    initial_query = f"""
    SELECT symbol, f_vol_ratio 
    FROM stk_factors 
    WHERE trade_date = '{latest_date}' 
      AND f_vol_ratio > 2.5 
      AND (symbol LIKE '60%%' OR symbol LIKE '00%%')
    """
    candidates = pd.read_sql(initial_query, engine)
    print(f"找到日线爆量个股 {len(candidates)} 只，开始深入分析分时脉冲...")

    results = []
    for i, row in candidates.iterrows():
        sym = row['symbol']
        # 深入分析分时
        surge_info = analyze_intraday_surge(sym, latest_date)
        
        if surge_info:
            # 关联股票名称
            name = pd.read_sql(f"SELECT name FROM stocks WHERE symbol='{sym}'", engine).iloc[0,0]
            
            results.append({
                '代码': sym,
                '名称': name,
                '爆量倍数': round(row['f_vol_ratio'], 2),
                '分时脉冲次数': surge_info['surge_count'],
                '单分最大涨幅%': surge_info['max_surge_ret'],
                '异动时间点': ", ".join(surge_info['surge_times'][:3]) # 取前三个时间点
            })

    # 2. 输出展示
    if results:
        df_res = pd.DataFrame(results).sort_values('分时脉冲次数', ascending=False)
        print("\n" + "🚨" * 15)
        print(f"🔥 今日主力【资金异动】深度扫描报告 ({latest_date})")
        print("-" * 80)
        print(df_res.to_string(index=False))
        print("-" * 80)
        print("💡 研判依据：日线爆量是‘大资金进场’，分时脉冲是‘主力暴力拉升’。两者结合即为强力异动。")
        print("🚨" * 15)
    else:
        print("\n今日暂未发现显著的资金抢筹异动。")

if __name__ == "__main__":
    run_capital_monitor()