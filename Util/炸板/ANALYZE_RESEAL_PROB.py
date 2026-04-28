import pandas as pd
import numpy as np
from sqlalchemy import create_engine
import datetime

# --- 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def analyze_reseal_potential(symbol, date_str):
    """
    量化评估炸板后的回封潜力 (单位修正版)
    """
    # 1. 获取分钟数据
    query = f"SELECT trade_time, high, close, volume, amount FROM stk_min_kline WHERE symbol='{symbol}' AND DATE(trade_time)='{date_str}' ORDER BY trade_time ASC"
    df = pd.read_sql(query, engine).dropna()
    if df.empty: return None

    # 2. 获取昨日收盘价算涨停
    prev_close_q = f"SELECT close FROM stk_daily_kline WHERE symbol='{symbol}' AND trade_date < '{date_str}' ORDER BY trade_date DESC LIMIT 1"
    prev_close = pd.read_sql(prev_close_q, engine).iloc[0,0]
    limit_p = round(prev_close * 1.10 + 0.0001, 2)

    # --- 关键修正 A：判断单位并计算均价线 ---
    # 逻辑：在 A 股，如果 Amount / Volume > 5000，通常说明 Volume 的单位是“手”，需要 * 100
    # 我们取第一分钟的数据做测试
    sample_amount = df['amount'].iloc[0]
    sample_volume = df['volume'].iloc[0]
    
    # 判定单位因子 (unit_factor)
    if sample_volume > 0 and (sample_amount / sample_volume) > (limit_p * 5):
        # 说明 Volume 是“手”，计算时需要转为“股”
        unit_factor = 100
    else:
        # 说明 Volume 已经是“股”
        unit_factor = 1
        
    # --- 关键修正 B：防止重复累加 ---
    # 检查数据库里的数据是否已经是累计值
    # 如果最后一行的 amount 远大于第一行，且中间没有明显回落，可能是累计值
    is_already_cumulative = (df['amount'].iloc[-1] > df['amount'].iloc[0] * 10) 
    
    if is_already_cumulative:
        # 如果已经是累计值，直接相除
        df['vwap'] = df['amount'] / (df['volume'] * unit_factor + 0.001)
    else:
        # 如果是分钟增量值，则需要累加后再相除
        df['cum_amt'] = df['amount'].cumsum()
        df['cum_vol'] = df['volume'].cumsum()
        df['vwap'] = df['cum_amt'] / (df['cum_vol'] * unit_factor + 0.001)

    # 3. 找到炸板点
    limit_hits = df[df['high'] >= limit_p]
    if limit_hits.empty: return None
    
    first_hit_idx = limit_hits.index[0]
    after_break_df = df.loc[first_hit_idx:].copy()
    
    if len(after_break_df) < 1: return None

    # 4. 指标计算
    lowest_after = after_break_df['close'].min()
    max_drop = (limit_p - lowest_after) / limit_p * 100
    
    current_price = after_break_df['close'].iloc[-1]
    current_vwap = after_break_df['vwap'].iloc[-1]

    # --- 修正后的评分逻辑 ---
    # 回落评分 (40分)
    depth_score = 0
    if max_drop <= 1.2: depth_score = 40
    elif max_drop <= 3.0: depth_score = 20
    
    # 均线评分 (30分) - 均线是短线生命线
    vwap_score = 30 if current_price >= current_vwap else 0
    
    # 承接评分 (30分)
    avg_vol_after = after_break_df['volume'].mean()
    avg_vol_before = df.loc[:first_hit_idx, 'volume'].mean()
    vol_score = 30 if avg_vol_after > avg_vol_before else 15

    total_score = depth_score + vwap_score + vol_score

    return {
        '代码': symbol,
        '当前价': current_price,
        '均价线': round(current_vwap, 2),
        '最高回落': f"{round(max_drop, 2)}%",
        '均线上方': "是" if current_price >= current_vwap else "否",
        '回封潜力分': total_score
    }

if __name__ == "__main__":
    # 示例：分析今天的某只炸板股
    # 你可以从你的 FIND_LIMIT_UP_FAILURE.py 结果中挑一个代码
    res = analyze_reseal_potential('000782.SZ', '2026-04-27') 
    if res:
        print(f"\n📊 炸板回封价值评估报告")
        print("-" * 40)
        for k, v in res.items():
            print(f"{k}: {v}")
        
        if res['回封潜力分'] >= 70:
            print("🔥 结论：回封概率极大，主力强力洗盘，建议重点关注。")
        elif res['回封潜力分'] >= 40:
            print("⚖️ 结论：回封概率中等，属于烂板震荡，需观察板块带动。")
        else:
            print("❄️ 结论：走势已坏，上方套牢盘重，避开。")