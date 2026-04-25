import pandas as pd
import numpy as np
from sqlalchemy import create_engine

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def get_resistance_levels(symbol):
    print(f"\n--- 正在分析 {symbol} 的向上压力位 ---")
    
    # 1. 获取最近 120 个交易日的数据 (筹码通常看半年以内的分布)
    query = f"""
    SELECT close, high, low, volume, amount FROM stk_daily_kline 
    WHERE symbol = '{symbol}' 
    ORDER BY trade_date DESC LIMIT 120
    """
    df = pd.read_sql(query, engine)
    
    if df.empty:
        print("未找到数据。")
        return

    # 当前价格
    current_price = df['close'].iloc[0]
    
    # --- 算法一：成交密集区 (Volume at Price) ---
    # 将最高价和最低价之间分成 50 个价格区间（箱体）
    min_p = df['low'].min()
    max_p = df['high'].max()
    bins = np.linspace(min_p, max_p, 50)
    
    # 统计每个价格区间的累计成交额 (Amount)
    # 使用成交额比成交量更准，因为它代表了真实的“钱”
    df['price_bin'] = pd.cut(df['close'], bins=bins)
    volume_profile = df.groupby('price_bin', observed=True)['amount'].sum()
    
    # 找出当前价格上方的成交密集区
    resistance_zones = []
    for interval, amt in volume_profile.items():
        if interval.left > current_price: # 只看当前价上方的
            resistance_zones.append({
                'price_level': round((interval.left + interval.right)/2, 2),
                'strength': amt,
                'distance': round((interval.left - current_price)/current_price * 100, 2)
            })
    
    # 按成交额（压力强度）排序，取前 3 个核心压力位
    resistance_zones = sorted(resistance_zones, key=lambda x: x['strength'], reverse=True)[:3]

    # --- 算法二：近期波段高点 (Swing Highs) ---
    # 过去 60 天内的最高价通常也是心理压力位
    swing_high_60 = df['high'].head(60).max()
    
    # --- 结果展示 ---
    print(f"当前股价: {current_price}")
    print("-" * 30)
    
    # 打印成交密集区压力
    if resistance_zones:
        print("【成交密集区压力（套牢盘密集区）】:")
        for r in resistance_zones:
            # 计算压力等级 (0-10)
            level = "★" * int(min(10, (r['strength'] / volume_profile.max() * 10)))
            print(f"价格: {r['price_level']} | 距离: {r['distance']}% | 压力强度: {level}")
    else:
        print("上方暂无明显的成交密集区（天空之城，无套牢盘）。")

    print("-" * 30)
    # 打印波段高点压力
    if swing_high_60 > current_price:
        dist_high = round((swing_high_60 - current_price)/current_price * 100, 2)
        print(f"【近期波段高点压力】: {swing_high_60} (距离: {dist_high}%)")
    
    return resistance_zones

if __name__ == "__main__":
    # 测试一只股票，比如“贵州茅台”或你自选的票
    get_resistance_levels('603060.SH')