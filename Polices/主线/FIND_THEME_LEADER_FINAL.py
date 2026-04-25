import pandas as pd
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def find_main_themes_pro():
    print(f"[{datetime.datetime.now()}] 正在扫描主线并识别【龙头】与【中军】...")

    # 1. 从数据库读取最新的 3 日行情数据
    query_kline = """
    SELECT symbol, trade_date, open, close, amount 
    FROM stk_daily_kline 
    WHERE trade_date >= (
        SELECT MIN(t.trade_date) FROM (
            SELECT DISTINCT trade_date FROM stk_daily_kline 
            ORDER BY trade_date DESC LIMIT 3
        ) AS t
    )
    """
    df_all = pd.read_sql(query_kline, engine)
    
    if df_all.empty:
        print("错误：行情数据库为空。")
        return

    # 2. 获取最新日期并计算涨幅
    latest_date = df_all['trade_date'].max()
    df_today = df_all[df_all['trade_date'] == latest_date].copy()
    # 计算今日真实涨幅 (收盘 vs 开盘)
    df_today['pct_chg'] = (df_today['close'] - df_today['open']) / df_today['open'] * 100

    # 3. 读取板块映射并关联股票名称
    query_relation = text("""
        SELECT r.symbol, s.name, r.sector_name 
        FROM stock_sector_relation r
        JOIN stocks s ON r.symbol = s.symbol
        WHERE r.sector_name LIKE '概念-%%' 
           OR r.sector_name LIKE 'THY%%' 
           OR r.sector_name LIKE 'SW1%%'
           OR r.sector_name LIKE 'TGN%%'
    """)
    
    with engine.connect() as conn:
        df_relation = pd.read_sql(query_relation, conn)
    
    # 4. 合并行情与板块信息
    df_merged = pd.merge(df_relation, df_today, on='symbol', how='inner')

    theme_results = []
    
    # 5. 分组计算每个板块的各项指标
    for sector_name, sector_df in df_merged.groupby('sector_name'):
        if len(sector_df) < 6:
            continue
            
        # --- A. 板块基础统计 ---
        breadth = (len(sector_df[sector_df['pct_chg'] > 2.5]) / len(sector_df)) * 100
        total_amt = sector_df['amount'].sum() / 1e8 # 亿元
        
        # 计算3日持续性
        stocks_in_sector = sector_df['symbol'].unique()
        avg_3d_ret = ((df_all[df_all['symbol'].isin(stocks_in_sector)]['close'] - 
                       df_all[df_all['symbol'].isin(stocks_in_sector)]['open']) / 
                      df_all[df_all['symbol'].isin(stocks_in_sector)]['open']).mean() * 100

        # --- B. 识别【龙头】(今日涨幅最大) ---
        dragon_row = sector_df.sort_values('pct_chg', ascending=False).iloc[0]
        dragon_name = dragon_row['name']
        dragon_pct = dragon_row['pct_chg']

        # --- C. 识别【中军】(今日成交额最大) ---
        zj_row = sector_df.sort_values('amount', ascending=False).iloc[0]
        zj_name = zj_row['name']
        zj_amt = zj_row['amount'] / 1e8

        # --- D. 综合热力评分 ---
        score = (breadth * 0.4) + (avg_3d_ret * 3.0) + (min(total_amt/10, 10) * 2) + (sector_df['pct_chg'].mean() * 1.0)
        
        theme_results.append({
            '题材': sector_name.split('-')[-1],
            '热力评分': round(score, 2),
            '广度%': round(breadth, 1),
            '3日持续%': round(avg_3d_ret, 2),
            '核心龙头': dragon_name,
            '龙头涨幅%': round(dragon_pct, 2),
            '核心中军': zj_name,
            '中军成交(亿)': round(zj_amt, 2),
            '总成交(亿)': round(total_amt, 2)
        })

    # 6. 输出分析报告
    if theme_results:
        result_df = pd.DataFrame(theme_results).sort_values('热力评分', ascending=False)
        
        print("\n" + "⭐" * 45)
        print(f"🔥 A股主线雷达：【龙头】与【中军】全景图 ({latest_date})")
        print("-" * 110)
        
        # 调整显示顺序
        display_cols = ['题材', '热力评分', '广度%', '3日持续%', '核心龙头', '龙头涨幅%', '核心中军', '中军成交(亿)', '总成交(亿)']
        print(result_df[display_cols].head(15).to_string(index=False))
        print("-" * 110)
        
        # 深度研判
        top = result_df.iloc[0]
        print(f"💡 研判结论：今日最强题材是【{top['题材']}】。")
        print(f"   - 情绪标杆（龙头）：【{top['核心龙头']}】涨幅 {top['龙头涨幅%']}%，观察其是否连板。")
        print(f"   - 资金底牌（中军）：【{top['核心中军']}】成交 {top['中军成交(亿)']} 亿，观察其趋势稳定性。")
        print("⭐" * 45 + "\n")
    else:
        print("扫描完成，未发现符合条件的活跃板块。")

if __name__ == "__main__":
    find_main_themes_pro()