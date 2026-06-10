import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime
import warnings

warnings.filterwarnings('ignore')

# --- 1. 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def calculate_chips_for_stock(df):
    """
    计算单只股票的筹码分布指标 (健壮版)
    """
    # 强制转换类型，处理数据库返回的 Decimal 对象
    df['low'] = df['low'].astype(float)
    df['high'] = df['high'].astype(float)
    df['close'] = df['close'].astype(float)
    df['turnover_rate'] = df['turnover_rate'].astype(float).fillna(0)

    # 1. 建立价格轴 - 固定 200 个价格位，确保稳定性
    min_p = df['low'].min()
    max_p = df['high'].max()
    
    if min_p == max_p or min_p <= 0: return None
    
    price_bins = np.linspace(min_p, max_p, 200)
    chips = np.zeros_like(price_bins)

    # 2. 模拟筹码流动 (CYQ 算法)
    for _, row in df.iterrows():
        # A. 筹码衰减 (换手掉旧筹码)
        # 换手率通常为百分比(如 5.0)，转为小数(0.05)。上限封死在 0.99 避免计算溢出
        turnover = min(row['turnover_rate'] / 100.0, 0.99)
        chips *= (1 - turnover)
        
        # B. 填入今日新筹码
        # 找出今日价格区间对应的 bins
        mask = (price_bins >= row['low']) & (price_bins <= row['high'])
        hit_count = mask.sum()
        if hit_count > 0:
            chips[mask] += (turnover / hit_count)

    # 3. 提取指标
    total_chips = chips.sum()
    if total_chips <= 0: return None
    
    current_price = df['close'].iloc[-1]
    
    # 筹码峰价格
    chip_peak_price = price_bins[np.argmax(chips)]
    
    # 获利盘比例
    profit_ratio = float(chips[price_bins <= current_price].sum() / total_chips * 100)
    
    # 累积分布
    cumsum_chips = np.cumsum(chips) / total_chips
    
    def get_range(percent):
        """获取包含指定百分比筹码的价格区间"""
        try:
            low_bound = (1 - percent) / 2
            high_bound = (1 + percent) / 2
            
            low_idx_arr = np.where(cumsum_chips >= low_bound)[0]
            high_idx_arr = np.where(cumsum_chips >= high_bound)[0]
            
            if len(low_idx_arr) == 0 or len(high_idx_arr) == 0:
                return min_p, max_p
                
            return price_bins[low_idx_arr[0]], price_bins[high_idx_arr[0]]
        except:
            return min_p, max_p

    c70_low, c70_high = get_range(0.7)
    c90_low, c90_high = get_range(0.9)
    
    # 集中度计算
    chip_width70 = float((c70_high - c70_low) / (c70_high + c70_low + 0.001))
    peak_distance = float((current_price - chip_peak_price) / (chip_peak_price + 0.001))

    # 4. 筹码集中评分 (0-100)
    score = 0
    if chip_width70 < 0.12: score += 40  # 集中度高
    elif chip_width70 < 0.18: score += 20
    
    if profit_ratio > 85: score += 40    # 绝大部分获利
    elif profit_ratio > 70: score += 20
    
    if abs(peak_distance) < 0.04: score += 20 # 处于筹码峰
    
    return {
        'chip_peak_price': round(chip_peak_price, 2),
        'current_price': round(current_price, 2),
        'profit_ratio': round(profit_ratio, 2),
        'chip70_low': round(c70_low, 2),
        'chip70_high': round(c70_high, 2),
        'chip90_low': round(c90_low, 2),
        'chip90_high': round(c90_high, 2),
        'chip_width70': round(chip_width70, 4),
        'peak_distance': round(peak_distance, 4),
        'chip_score': int(score)
    }

def run_chip_calculation():
    print(f"[{datetime.datetime.now()}] 启动全市场筹码因子计算...")
    
    with engine.connect() as conn:
        res = conn.execute(text("SELECT MAX(trade_date) FROM stk_daily_kline")).fetchone()
        latest_date = res[0]
        if not latest_date: return

    # 获取行情 (过去 180 天，涵盖 120 个交易日)
    query = text("""
        SELECT symbol, trade_date, close, high, low, turnover_rate 
        FROM stk_daily_kline 
        WHERE trade_date >= DATE_SUB(:d, INTERVAL 180 DAY)
        ORDER BY symbol, trade_date ASC
    """)
    df_all = pd.read_sql(query, engine, params={"d": latest_date})
    
    results = []
    print(f"正在分析 {df_all['symbol'].nunique()} 只股票...")

    for symbol, df in df_all.groupby('symbol'):
        if len(df) < 60: continue
        
        try:
            chip_res = calculate_chips_for_stock(df)
            if chip_res:
                chip_res['symbol'] = symbol
                chip_res['trade_date'] = latest_date
                results.append(chip_res)
        except Exception as e:
            # print(f"计算 {symbol} 失败: {e}")
            continue
            
    # 批量入库 (UPSERT)
    if results:
        df_save = pd.DataFrame(results)
        print(f"计算完成，准备更新 {len(df_save)} 条筹码记录...")
        with engine.begin() as conn:
            df_save.to_sql('temp_chips', con=conn, if_exists='replace', index=False)
            upsert_sql = text("""
                INSERT INTO stk_chip_factor (trade_date, symbol, chip_peak_price, current_price, profit_ratio, chip70_low, chip70_high, chip90_low, chip90_high, chip_width70, peak_distance, chip_score)
                SELECT trade_date, symbol, chip_peak_price, current_price, profit_ratio, chip70_low, chip70_high, chip90_low, chip90_high, chip_width70, peak_distance, chip_score FROM temp_chips
                ON DUPLICATE KEY UPDATE 
                    chip_peak_price = VALUES(chip_peak_price), 
                    profit_ratio = VALUES(profit_ratio), 
                    chip_score = VALUES(chip_score),
                    current_price = VALUES(current_price),
                    chip_width70 = VALUES(chip_width70);
            """)
            conn.execute(upsert_sql)
            conn.execute(text("DROP TABLE IF EXISTS temp_chips"))
        print(f"✅ 筹码同步完成。")

if __name__ == "__main__":
    run_chip_calculation()