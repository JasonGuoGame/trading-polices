import pandas as pd
import pandas_ta as ta
from sqlalchemy import create_engine
import datetime

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def screen_flat_breakout():
    print(f"[{datetime.datetime.now()}] 正在扫描“横盘起爆”个股...")

    # 1. 获取最近 120 天数据 (需要足够长的历史判断横盘)
    query = """
    SELECT * FROM stk_daily_kline 
    WHERE trade_date >= DATE_SUB(CURDATE(), INTERVAL 6 MONTH)
    ORDER BY symbol, trade_date ASC
    """
    df_all = pd.read_sql(query, engine)
    
    results = []

    # 2. 遍历每一只股票
    for symbol, df in df_all.groupby('symbol'):
        if len(df) < 60: continue
        
        # --- 核心量化指标 ---
        
        # A. 判断横盘 (最近 20 到 60 天的价格箱体)
        # 逻辑：过去 30 天的最高价与最低价之差，波动范围在 15% 以内
        lookback_period = 30
        df['box_high'] = df['high'].shift(1).rolling(lookback_period).max()
        df['box_low'] = df['low'].shift(1).rolling(lookback_period).min()
        # 计算箱体振幅
        df['amplitude'] = (df['box_high'] - df['box_low']) / df['box_low']
        
        # B. 判断成交量放大
        # 逻辑：今天的成交量是过去 20 天平均成交量的 2.5 倍以上
        df['vol_ma20'] = df['volume'].shift(1).rolling(20).mean()
        
        # C. 提取当前数据
        curr = df.iloc[-1]
        
        # --- 策略筛选条件 ---
        
        # 1. 箱体限制：过去 30 天波动率极低 (小于 12%，弹簧压缩得越紧，爆发力越强)
        is_flat = curr['amplitude'] < 0.12
        
        # 2. 突破前高：今日收盘价突破了过去 30 天的最高点
        is_breakout = curr['close'] > curr['box_high']
        
        # 3. 量能剧增：今日成交量 > 2.5倍均量
        is_vol_spike = curr['volume'] > 2.5 * curr['vol_ma20']
        
        # 4. 实体要求：今日必须是大阳线 (涨幅 > 4%)
        is_strong_candle = (curr['close'] - curr['open']) / curr['open'] > 0.04

        # --- 综合判断 ---
        if is_flat and is_breakout and is_vol_spike and is_strong_candle:
            # 过滤掉创业板和科创板
            if not symbol.startswith(('60', '00')): continue
            
            results.append({
                '代码': symbol,
                '收盘价': curr['close'],
                '箱体振幅': f"{round(curr['amplitude']*100, 2)}%",
                '成交量倍数': round(curr['volume'] / curr['vol_ma20'], 2),
                '今日涨幅': f"{round((curr['close']-curr['open'])/curr['open']*100, 2)}%"
            })

    # 3. 输出结果
    if results:
        res_df = pd.DataFrame(results).sort_values('成交量倍数', ascending=False)
        print("\n" + "🚀" * 15)
        print(f"发现 {len(res_df)} 只符合“横盘起爆”形态的个股：")
        print("-" * 60)
        print(res_df.to_string(index=False))
        print("-" * 60)
        print("💡 操作逻辑：")
        print("1. 这种形态属于强力拉升，第二天通常会有高开溢价。")
        print("2. 止损位建议设在今日阳线实体的中轴。")
    else:
        print("\n今日全市场未发现标准的横盘放量起爆个股。")

if __name__ == "__main__":
    screen_flat_breakout()