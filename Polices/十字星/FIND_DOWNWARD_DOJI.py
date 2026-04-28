import pandas as pd
import pandas_ta as ta
from sqlalchemy import create_engine
import datetime

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def screen_downward_doji():
    print(f"[{datetime.datetime.now()}] 正在全市场扫描“下跌十字星”形态...")
    
    # 1. 加载最近 20 天数据（判断短期趋势即可）
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
        if len(df) < 10: continue
        
        # 提取最后两日数据
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        
        # --- A. 十字星量化定义 ---
        # 实体大小 = |收盘价 - 开盘价|
        body_size = abs(curr['close'] - curr['open'])
        # 全天振幅 = 最高价 - 最低价
        total_range = curr['high'] - curr['low']
        
        if total_range == 0: continue # 排除一字板
        
        # 判断标准：实体占全天振幅的比例小于 15%，且实体相对于价格极小（小于 0.2%）
        is_doji = (body_size / total_range < 0.15) or (body_size / curr['close'] < 0.002)
        
        # --- B. “下跌”趋势定义 ---
        # 1. 今日收盘价低于昨日收盘价
        # 2. 过去 5 日累计涨幅为负（处于调整周期）
        ret_5d = (curr['close'] - df['close'].iloc[-5]) / df['close'].iloc[-5]
        is_downward = (curr['close'] < prev['close']) and (ret_5d < -0.02)
        
        # --- C. 均线压制（可选，增加准确度） ---
        # 股价在 5 日均线下方，说明趋势未扭转
        ma5 = df['close'].rolling(5).mean().iloc[-1]
        is_under_ma = curr['close'] < ma5

        # --- 综合逻辑 ---
        if is_doji and is_downward and is_under_ma:
            results.append({
                '代码': symbol,
                '名称': curr['name'],
                '当前价': curr['close'],
                '今日跌幅': f"{round((curr['close']-prev['close'])/prev['close']*100, 2)}%",
                '5日跌幅': f"{round(ret_5d*100, 2)}%",
                '实体/振幅比': round(body_size / total_range, 2),
                '成交额(亿)': round(curr['amount'] / 1e8, 2)
            })

    # 3. 输出结果
    if results:
        res_df = pd.DataFrame(results).sort_values('5日跌幅', ascending=True)
        print("\n" + "🏮" * 10 + " 发现下跌十字星个股 " + "🏮" * 10)
        print("-" * 80)
        print(res_df.to_string(index=False))
        print("-" * 80)
        print("💡 研判：")
        print("1. 若出现在大幅下跌后且伴随地量，可能是【止跌信号】，关注次日放量反包。")
        print("2. 若出现在下跌途中且成交量不缩，多为【下跌中继】，建议继续观望。")
    else:
        print("\n今日未发现符合下跌十字星形态的个股。")

if __name__ == "__main__":
    screen_downward_doji()