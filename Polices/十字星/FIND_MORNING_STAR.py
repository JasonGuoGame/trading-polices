import pandas as pd
from sqlalchemy import create_engine
import datetime

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def screen_morning_star():
    print(f"[{datetime.datetime.now()}] 正在全市场扫描“早晨之星”反转形态...")
    
    # 1. 加载最近 30 天数据 (需要至少 3 天连续数据)
    query = """
    SELECT k.*, s.name 
    FROM stk_daily_kline k
    JOIN stocks s ON k.symbol = s.symbol
    WHERE k.trade_date >= DATE_SUB(CURDATE(), INTERVAL 40 DAY)
      AND (k.symbol LIKE '60%%' OR k.symbol LIKE '00%%')
      AND s.name NOT LIKE '%%ST%%'
    ORDER BY k.symbol, k.trade_date ASC
    """
    df_all = pd.read_sql(query, engine)
    
    if df_all.empty: return

    results = []
    
    # 2. 遍历股票分析形态
    for symbol, df in df_all.groupby('symbol'):
        if len(df) < 5: continue
        
        # 提取最后三日数据
        # day1: 前天, day2: 昨天, day3: 今天
        day1 = df.iloc[-3]
        day2 = df.iloc[-2]
        day3 = df.iloc[-1]
        
        # --- A. 形态量化定义 ---
        
        # 1. 前天(Day1)：必须是大阴线
        # 跌幅 > 2% 且 实体较长
        body1 = day1['open'] - day1['close']
        is_day1_bearish = (day1['close'] < day1['open']) and (body1 / day1['open'] > 0.02)
        
        # 2. 昨天(Day2)：必须是星线 (实体极小)
        # 实体大小小于前天阴线实体的 30%
        body2 = abs(day2['close'] - day2['open'])
        is_day2_star = body2 < (body1 * 0.3)
        # 且昨天的最低价通常是近三日的低点
        is_day2_low = day2['low'] <= min(day1['low'], day3['low'])
        
        # 3. 今天(Day3)：必须是放量强力阳线
        # 收盘价必须穿入到第一天阴线实体的 50% 以上 (核心反转确认)
        is_day3_bullish = (day3['close'] > day3['open'])
        penetration_limit = day1['close'] + (body1 * 0.5)
        is_reversal_confirmed = day3['close'] > penetration_limit
        
        # 4. 成交量辅助 (今天放量)
        is_vol_up = day3['volume'] > day2['volume']

        # --- B. 综合逻辑 ---
        if is_day1_bearish and is_day2_star and is_day3_bullish and is_reversal_confirmed:
            # 额外计算一个强度分：阳线包入阴线越多越强
            strength = (day3['close'] - day1['close']) / body1
            
            results.append({
                '代码': symbol,
                '名称': day3['name'],
                '当前价': day3['close'],
                '今日涨幅': f"{round((day3['close']-day2['close'])/day2['close']*100, 2)}%",
                '反转强度': round(strength, 2),
                '成交量比(今/昨)': round(day3['volume'] / day2['volume'], 2)
            })

    # 3. 输出结果
    if results:
        res_df = pd.DataFrame(results).sort_values('反转强度', ascending=False)
        print("\n" + "🌅" * 10 + " 发现【早晨之星】反转个股 " + "🌅" * 10)
        print("-" * 85)
        print(res_df.to_string(index=False))
        print("-" * 85)
        print("💡 操盘建议：")
        print("1. 买入：今日尾盘或明日早盘回踩不破星线(昨日)中轴时介入。")
        print("2. 止损：设定在星线(昨日)的最低价，跌破即形态失败。")
        print("3. 目标：近期波段高点或 20 日均线处。")
    else:
        print("\n今日全市场未发现标准的早晨之星反转形态。")

if __name__ == "__main__":
    screen_morning_star()