import pandas as pd
import pandas_ta as ta
from sqlalchemy import create_engine
import datetime

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def run_swing_strategy():
    print(f"[{datetime.datetime.now()}] 正在全市场执行波段交易筛选（含筹码干净度评分）...")
    
    # 1. 加载代码与名称映射
    query_names = "SELECT symbol, name FROM stocks"
    df_names = pd.read_sql(query_names, engine)
    name_map = dict(zip(df_names['symbol'], df_names['name']))

    # 2. 加载最近 120 天日线数据
    query_kline = """
    SELECT * FROM stk_daily_kline 
    WHERE trade_date >= (
        SELECT MIN(t.trade_date) FROM (
            SELECT DISTINCT trade_date FROM stk_daily_kline 
            ORDER BY trade_date DESC LIMIT 120
        ) AS t
    ) 
    ORDER BY symbol, trade_date ASC
    """
    df_all = pd.read_sql(query_kline, engine)
    
    if df_all.empty: return

    # 3. 加载板块信息
    query_relation = "SELECT symbol, sector_name FROM stock_sector_relation"
    df_relation = pd.read_sql(query_relation, engine)

    results = []
    
    # 4. 按股票分组计算
    for symbol, df in df_all.groupby('symbol'):
        if len(df) < 60: continue # 计算筹码分需要更长数据
        
        # 只做沪深主板
        if not symbol.startswith(('60', '00')): continue

        # --- A. 技术指标计算 ---
        df['MA5'] = ta.sma(df['close'], length=5)
        df['MA10'] = ta.sma(df['close'], length=10)
        df['MA20'] = ta.sma(df['close'], length=20)
        df['V_MA5'] = ta.sma(df['volume'], length=5)
        df['V_MA60'] = ta.sma(df['volume'], length=60)
        df['rolling_high_20'] = df['high'].shift(1).rolling(20).max()
        df['RSI'] = ta.rsi(df['close'], length=14)
        
        curr = df.iloc[-1]
        
        # --- B. 基础策略条件判断 ---
        # 1. 均线粘合度
        ma_list = [curr['MA5'], curr['MA10'], curr['MA20']]
        cohesion = (max(ma_list) - min(ma_list)) / curr['MA20']
        is_cohesion = cohesion < 0.03
        
        # 2. 启动信号：放量突破20日平台
        is_breakout = curr['close'] > curr['rolling_high_20']
        is_vol_up = curr['volume'] > 2.0 * curr['V_MA5']
        
        # 3. 多头排列雏形
        is_bullish = curr['MA5'] > curr['MA10'] > curr['MA20']
        
        # --- C. 筹码干净度深度评分 (0-100分) ---
        chip_score = 0
        
        # 维度1：地量程度 (今日量/60日均量) - 越小说明抛压越轻
        vol_ratio = curr['volume'] / curr['V_MA60']
        if vol_ratio < 0.6: chip_score += 30
        elif vol_ratio < 1.0: chip_score += 15
        
        # 维度2：振幅收敛 (最近20天波动范围) - 越窄说明筹码越稳定
        recent_20 = df.tail(20)
        box_range = (recent_20['high'].max() - recent_20['low'].min()) / curr['close']
        if box_range < 0.12: chip_score += 30
        elif box_range < 0.18: chip_score += 15
        
        # 维度3：换手充分度 (最近60天累计换手) - 越高说明洗盘越彻底
        total_turnover_60 = df['turnover_rate'].tail(60).sum()
        if total_turnover_60 > 150: chip_score += 30
        elif total_turnover_60 > 80: chip_score += 15
        
        # 维度4：均线粘合加分
        if cohesion < 0.015: chip_score += 10
        elif cohesion < 0.03: chip_score += 5

        # --- D. 综合逻辑判定 ---
        if is_breakout and is_vol_up and is_cohesion and is_bullish:
            # 查找所属板块
            sectors = df_relation[df_relation['symbol'] == symbol]['sector_name'].tolist()
            # 过滤掉一些大而全的板块名
            filtered_sectors = [s for s in sectors if '概念' in s or 'THY' in s or 'SW' in s]
            sector_str = ",".join(filtered_sectors[:3])

            stock_name = name_map.get(symbol, "未知")

            results.append({
                '代码': symbol,
                '名称': stock_name,
                '收盘价': curr['close'],
                '成交量倍数': round(curr['volume'] / curr['V_MA5'], 2),
                '均线粘合': f"{round(cohesion*100, 2)}%",
                '筹码得分': chip_score,  # <--- 新增得分字段
                '60日换手': round(total_turnover_60, 2),
                'RSI': round(curr['RSI'], 2),
                '所属板块': sector_str
            })

    # 5. 输出结果
    if results:
        res_df = pd.DataFrame(results)
        # 优先按照筹码得分降序，其次按均线粘合度升序
        res_df = res_df.sort_values(by=['筹码得分', '均线粘合'], ascending=[False, True])
        
        cols = ['代码', '名称', '收盘价', '筹码得分', '成交量倍数', '均线粘合', '60日换手', 'RSI', '所属板块']
        res_df = res_df[cols]
        
        print("\n" + "💎" * 10 + " 发现波段起爆潜力股（筹码精选版） " + "💎" * 10)
        print("-" * 125)
        print(res_df.to_string(index=False))
        print("-" * 125)
        print("💡 研判：筹码得分 > 70 且成交量倍数 > 2.0 的个股，属于主力高度控盘后的暴力突破。")
    else:
        print("\n今日未发现符合波段起爆条件的个股。")

if __name__ == "__main__":
    run_swing_strategy()