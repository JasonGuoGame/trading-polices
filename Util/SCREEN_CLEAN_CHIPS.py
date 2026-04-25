import pandas as pd
import pandas_ta as ta
from sqlalchemy import create_engine
import numpy as np

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def screen_clean_chips():
    print("正在从数据库加载历史数据进行筹码深度分析...")
    
    # 1. 获取最近 120 个交易日的数据（为了计算筹码分布和均线）
    # 修正了之前报错的子查询语法
    query = """
    SELECT * FROM stk_daily_kline 
    WHERE trade_date >= (
        SELECT MIN(t.trade_date) FROM (
            SELECT DISTINCT trade_date FROM stk_daily_kline 
            ORDER BY trade_date DESC LIMIT 120
        ) AS t
    ) 
    ORDER BY symbol, trade_date ASC
    """
    df_all = pd.read_sql(query, engine)
    
    if df_all.empty:
        print("数据库为空，请先同步数据。")
        return

    print(f"数据读取完毕，共 {len(df_all)} 行。开始分股扫描...")

    results = []
    
    # 2. 按股票分组分析
    for symbol, df in df_all.groupby('symbol'):
        # --- 过滤：只要沪深主板 (60, 00) ---
        if not symbol.startswith(('60', '00')):
            continue
            
        if len(df) < 60: # 数据太短的不要
            continue

        # --- A. 基础指标计算 ---
        df['MA5'] = df['close'].rolling(5).mean()
        df['MA10'] = df['close'].rolling(10).mean()
        df['MA20'] = df['close'].rolling(20).mean()
        df['V_MA60'] = df['volume'].rolling(60).mean()
        
        curr = df.iloc[-1]
        
        # 如果均线没算出来，跳过
        if pd.isna(curr['MA20']): continue

        # --- B. 维度计算 ---
        
        # 1. 地量度 (今日成交量 / 60日均量)
        # 越小说明抛压越轻
        vol_ratio = curr['volume'] / curr['V_MA60']
        
        # 2. 振幅收敛度 (最近20天振幅)
        # 越小说明波动越收敛，散户不参与了
        high_20 = df['high'].tail(20).max()
        low_20 = df['low'].tail(20).min()
        range_20 = (high_20 - low_20) / curr['close']
        
        # 3. 换手充分度 (最近60天累计换手率)
        # 越高说明老套牢盘洗得越彻底
        turnover_60 = df['turnover_rate'].tail(60).sum()
        
        # 4. 压力位 (距120日高点距离)
        # 越小说明上方无大山压顶
        high_120 = df['close'].max()
        distance_high = (high_120 - curr['close']) / high_120
        
        # 5. 均线粘合度 (MA5/10/20 离散度)
        mas = [curr['MA5'], curr['MA10'], curr['MA20']]
        cohesion = (max(mas) - min(mas)) / curr['MA20']

        # --- C. 综合打分逻辑 ---
        # 满分 100 分
        score = 0
        if vol_ratio < 0.6: score += 25    # 地量加分
        elif vol_ratio < 0.9: score += 10
        
        if range_20 < 0.12: score += 25   # 窄幅横盘加分
        elif range_20 < 0.18: score += 10
        
        if turnover_60 > 150: score += 20 # 换手充分加分
        elif turnover_60 > 80: score += 10
        
        if distance_high < 0.08: score += 20 # 临近突破且无压力加分
        
        if cohesion < 0.03: score += 10    # 均线粘合加分

        # --- D. 筛选门槛 ---
        # 我们只要评分高于 70 分的“筹码干净”股
        if score >= 70:
            results.append({
                '代码': symbol,
                '日期': curr['trade_date'],
                '收盘价': curr['close'],
                '地量比': round(vol_ratio, 2),
                '20日振幅': f"{round(range_20*100, 2)}%",
                '60日换手': round(turnover_60, 2),
                '距高点': f"{round(distance_high*100, 2)}%",
                '均线粘合': f"{round(cohesion*100, 2)}%",
                '筹码得分': score
            })

    # 3. 输出并排序
    if results:
        res_df = pd.DataFrame(results).sort_values('筹码得分', ascending=False)
        print(f"\n--- 筛选完成：今日共发现 {len(res_df)} 只筹码干净的股票 ---")
        print(res_df.to_string(index=False))
    else:
        print("\n今日全市场未发现筹码高度干净的个股。")

if __name__ == "__main__":
    screen_clean_chips()