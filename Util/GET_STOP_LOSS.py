import pandas as pd
import numpy as np
from sqlalchemy import create_engine

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def calculate_stop_loss(symbol):
    print(f"\n--- 正在为 {symbol} 计算下方支撑位与止损警戒线 ---")
    
    # 1. 获取最近 120 个交易日的数据
    query = f"""
    SELECT trade_date, close, high, low, volume, amount FROM stk_daily_kline 
    WHERE symbol = '{symbol}' 
    ORDER BY trade_date DESC LIMIT 120
    """
    df = pd.read_sql(query, engine)
    
    if df.empty or len(df) < 20:
        print("数据量不足。")
        return

    # 按时间正序排列以便计算指标
    df = df.sort_values('trade_date').reset_index(drop=True)
    current_price = df['close'].iloc[-1]

    # --- 维度一：近期波段低点 (Swing Lows) ---
    # 20日低点（短线支撑）和 60日低点（中线强支撑）
    support_20 = df['low'].tail(20).min()
    support_60 = df['low'].tail(60).min()

    # --- 维度二：成交密集区底部 (Volume Support) ---
    # 逻辑：下方哪里钱堆得最多，哪里就是最后撤退的防线
    min_p = df['low'].min()
    max_p = df['high'].max()
    bins = np.linspace(min_p, max_p, 50)
    df['price_bin'] = pd.cut(df['close'], bins=bins)
    volume_profile = df.groupby('price_bin', observed=True)['amount'].sum()
    
    # 寻找当前价格下方的最大成交堆积区
    lower_zones = []
    for interval, amt in volume_profile.items():
        if interval.right < current_price: # 只看当前价下方的
            lower_zones.append({'price': interval.left, 'strength': amt})
    
    # 选出最强的一个支撑位
    if lower_zones:
        strongest_zone = sorted(lower_zones, key=lambda x: x['strength'], reverse=True)[0]['price']
    else:
        strongest_zone = support_60

    # --- 维度三：ATR 波动率动态止损 (Chandelier Exit 简化版) ---
    # 逻辑：股价跌幅超过 2.5 倍的波动范围，说明趋势彻底反转
    df['tr'] = np.maximum(df['high'] - df['low'], 
                          np.maximum(abs(df['high'] - df['close'].shift(1)), 
                                     abs(df['low'] - df['close'].shift(1))))
    atr = df['tr'].tail(14).mean()
    atr_stop = current_price - (atr * 2.5)

    # --- 综合判断止损逻辑 ---
    # 建议止损线：取近期低点和成交密集区底部的较大值（保护利润），但不能低于 ATR 极值
    suggested_stop = max(support_20, strongest_zone)
    # 如果建议止损位离当前价太近（小于2%），则强制使用 60 日低点
    if (current_price - suggested_stop) / current_price < 0.02:
        suggested_stop = support_60

    # --- 结果展示 ---
    print(f"当前股价: {current_price}")
    print("-" * 40)
    print(f"【一级预警（20日低点）】: {support_20}  (跌幅: {round((support_20-current_price)/current_price*100, 2)}%)")
    print(f"【二级支撑（成交密集区）】: {round(strongest_zone, 2)} (强力支撑)")
    print(f"【极限防线（60日低点）】: {support_60}  (最后撤退位置)")
    print(f"【波动率止损（ATR 2.5）】: {round(atr_stop, 2)}")
    print("-" * 40)
    print(f"💡 最终建议止损位: {round(suggested_stop, 2)}")
    print(f"⚠️ 风险提示: 若收盘价有效跌破 {round(suggested_stop, 2)}，建议无条件离场。")

    return suggested_stop

if __name__ == "__main__":
    # 测试一只股票
    calculate_stop_loss('000833.SZ')