import pandas as pd
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def get_sector_small_dragon(target_sector):
    print(f"[{datetime.datetime.now()}] 正在探测【{target_sector}】板块的潜力小票...")

    # 1. 查找该板块下所有的沪深主板个股 (过滤掉创业板和科创板)
    # 关联 stocks 表获取名称，关联 relation 表确定板块
    query_base = text("""
        SELECT s.symbol, s.name 
        FROM stocks s
        JOIN stock_sector_relation r ON s.symbol = r.symbol
        WHERE r.sector_name = :sector
        AND (s.symbol LIKE '60%%' OR s.symbol LIKE '00%%')
    """)
    
    with engine.connect() as conn:
        df_stocks = pd.read_sql(query_base, conn, params={"sector": target_sector})

    if df_stocks.empty:
        print(f"未找到板块 {target_sector} 或该板块无主板股票。")
        return

    symbols = df_stocks['symbol'].tolist()

    # 2. 读取这些股票最近 5 个交易日的行情
    query_kline = f"""
    SELECT symbol, trade_date, open, close, amount, turnover_rate 
    FROM stk_daily_kline 
    WHERE symbol IN ({str(symbols)[1:-1]})
    AND trade_date >= (
        SELECT MIN(t.trade_date) FROM (
            SELECT DISTINCT trade_date FROM stk_daily_kline 
            ORDER BY trade_date DESC LIMIT 5
        ) AS t
    )
    ORDER BY trade_date ASC
    """
    df_kline = pd.read_sql(query_kline, engine)

    # 3. 计算指标：3日累积涨幅、平均成交额、平均换手
    analysis = []
    for symbol, df in df_kline.groupby('symbol'):
        # 基础行情
        curr = df.iloc[-1]
        start = df.iloc[0]
        
        # 3日累积涨幅
        total_ret = (curr['close'] - start['open']) / start['open'] * 100
        # 3日平均成交额 (用于判断是否是“小票”)
        avg_amount = df['amount'].mean() / 1e8
        # 3日平均换手率 (用于判断活跃度)
        avg_turnover = df['turnover_rate'].mean()
        
        analysis.append({
            'symbol': symbol,
            '3日累计%': round(total_ret, 2),
            '日均成交(亿)': round(avg_amount, 2),
            '平均换手%': round(avg_turnover, 2),
            '最新收盘': curr['close']
        })

    df_res = pd.DataFrame(analysis)
    df_res = pd.merge(df_res, df_stocks, on='symbol')

    # --- 核心过滤逻辑：筛选龙头小票 ---
    # 1. 体量限制：日均成交额在 1亿 到 8亿 之间 (太大是中军，太小没流动性)
    # 2. 活跃度：平均换手率 > 5% (说明有游资在玩)
    # 3. 强度：3日累计涨幅排名靠前
    
    condition = (df_res['日均成交(亿)'] > 1.0) & (df_res['日均成交(亿)'] < 8.0) & (df_res['平均换手%'] > 5.0)
    dragons = df_res[condition].sort_values('3日累计%', ascending=False)

    # 4. 结果展示
    print("\n" + "🔥" * 20)
    print(f"🚀 【{target_sector}】板块潜力龙头小票筛选结果")
    print("标准：主板+成交额1-8亿+高换手+强动能")
    print("-" * 70)
    
    if not dragons.empty:
        print(dragons[['symbol', 'name', '3日累计%', '日均成交(亿)', '平均换手%']].head(10).to_string(index=False))
        
        top_one = dragons.iloc[0]
        print("-" * 70)
        print(f"💡 深度建议：重点关注【{top_one['name']}】。")
        print(f"该股 3 日涨幅达 {top_one['3日累计%']}%，换手极度活跃，是典型的先锋小票形态。")
    else:
        print("未发现符合“小票龙头”标准的个股，可能该板块目前由大票统领，或处于沉寂期。")
    print("🔥" * 20 + "\n")

if __name__ == "__main__":
    # 你可以替换为你感兴趣的板块名称，如 '概念-算力概念' 或 'THY3锂'
    get_sector_small_dragon('SW1建筑装饰')