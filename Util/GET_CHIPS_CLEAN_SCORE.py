import pandas as pd
import pandas_ta as ta
from sqlalchemy import create_engine

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def analyze_chips_cleanliness(symbol):
    print(f"\n--- 正在分析股票: {symbol} 的筹码状态 ---")
    
    # 1. 从数据库读取最近 120 天的数据
    query = f"""
    SELECT * FROM stk_daily_kline 
    WHERE symbol = '{symbol}' 
    ORDER BY trade_date DESC LIMIT 120
    """
    df = pd.read_sql(query, engine)
    
    if df.empty or len(df) < 60:
        print("数据量不足，无法分析。")
        return None

    # 时间顺序正序排列（计算指标需要）
    df = df.sort_values('trade_date').reset_index(drop=True)

    # --- 维度 A: 地量测试 (Volume Drying Up) ---
    # 计算当前成交量与过去 60 天平均成交量的比值
    df['vol_ma60'] = df['volume'].rolling(60).mean()
    # 最后一天的缩量程度
    vol_ratio = df['volume'].iloc[-1] / df['vol_ma60'].iloc[-1]
    
    # --- 维度 B: 波动率收敛 (Price Volatility) ---
    # 计算最近 20 天的振幅（最高-最低）占比
    recent_high = df['high'].tail(20).max()
    recent_low = df['low'].tail(20).min()
    price_box_range = (recent_high - recent_low) / df['close'].iloc[-1]

    # --- 维度 C: 筹码交换充分度 (Turnover Sufficiency) ---
    # 最近 60 天的累计换手率
    total_turnover_60 = df['turnover_rate'].tail(60).sum()

    # --- 维度 D: 上方压力测试 (Resistance) ---
    # 当前价与 120 日最高价的距离
    high_120 = df['close'].max()
    distance_to_high = (high_120 - df['close'].iloc[-1]) / high_120

    # --- 维度 E: 均线粘合度 (MA Cohesion) ---
    ma5 = df['close'].tail(5).mean()
    ma10 = df['close'].tail(10).mean()
    ma20 = df['close'].tail(20).mean()
    ma_diff = max(ma5, ma10, ma20) - min(ma5, ma10, ma20)
    cohesion = ma_diff / ma20

    # --- 综合评分逻辑 (满分 100) ---
    score = 0
    # 1. 地量加分 (越缩量说明抛压越轻)
    if vol_ratio < 0.5: score += 25
    elif vol_ratio < 0.8: score += 15
    
    # 2. 波动收敛加分 (横盘越久筹码越干)
    if price_box_range < 0.10: score += 25  # 20天振幅在10%以内
    elif price_box_range < 0.15: score += 15
    
    # 3. 换手率加分 (换手>150%说明老庄已走，新庄控盘)
    if total_turnover_60 > 150: score += 20
    elif total_turnover_60 > 100: score += 10
    
    # 4. 压力位加分 (离历史高点越近说明上方无套牢盘)
    if distance_to_high < 0.05: score += 20
    elif distance_to_high < 0.10: score += 10

    # 5. 均线粘合加分
    if cohesion < 0.02: score += 10

    # --- 结果打印 ---
    print(f"1. 成交量比(今日/60日均): {vol_ratio:.2f} ({'极度缩量' if vol_ratio < 0.6 else '成交活跃'})")
    print(f"2. 20日价格波动区间: {price_box_range*100:.2f}% ({'横盘收敛' if price_box_range < 0.12 else '波动剧烈'})")
    print(f"3. 60日累计换手率: {total_turnover_60:.2f}% ({'交换充分' if total_turnover_60 > 120 else '交换不足'})")
    print(f"4. 距120日高点距离: {distance_to_high*100:.2f}% ({'临近突破' if distance_to_high < 0.05 else '上方有压力'})")
    print(f"5. 均线粘合度: {cohesion*100:.2f}%")
    print(f"======================================")
    print(f"最终筹码干净得分: {score} 分")
    
    if score >= 80:
        print("结论: 筹码极度干净，随时可能爆发。")
    elif score >= 60:
        print("结论: 筹码较干净，具备起爆潜力。")
    else:
        print("结论: 筹码较乱，尚需洗盘。")

if __name__ == "__main__":
    # 测试一只股票，比如“贵州茅台”或你筛选出来的股票
    analyze_chips_cleanliness('603336.SH')