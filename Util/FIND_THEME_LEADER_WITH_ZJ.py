import pandas as pd
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def find_main_themes_with_zhongjun():
    print(f"[{datetime.datetime.now()}] 正在扫描主线并识别板块中军...")

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
    df_today['pct_chg'] = (df_today['close'] - df_today['open']) / df_today['open'] * 100

    # 3. 读取板块映射并关联股票名称（核心修改：JOIN stocks 获取中军名字）
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
    
    if df_relation.empty:
        print("错误：未找到板块关联数据。")
        return

    # 4. 合并行情与板块/名称信息
    df_merged = pd.merge(df_relation, df_today, on='symbol', how='inner')

    theme_results = []
    
    # 5. 按板块分组计算
    for sector_name, sector_df in df_merged.groupby('sector_name'):
        if len(sector_df) < 6:
            continue
            
        # --- A. 板块综合指标 ---
        breadth = (len(sector_df[sector_df['pct_chg'] > 2.5]) / len(sector_df)) * 100
        total_amt = sector_df['amount'].sum() / 1e8 # 板块总成交额(亿元)
        
        # 3日持续性
        stocks_in_sector = sector_df['symbol'].unique()
        avg_3d_ret = ((df_all[df_all['symbol'].isin(stocks_in_sector)]['close'] - 
                       df_all[df_all['symbol'].isin(stocks_in_sector)]['open']) / 
                      df_all[df_all['symbol'].isin(stocks_in_sector)]['open']).mean() * 100

        # --- B. 识别中军 (成交额最大的股票) ---
        # 在该板块内按成交额排序，取第一名
        zhongjun_row = sector_df.sort_values('amount', ascending=False).iloc[0]
        zj_name = zhongjun_row['name']
        zj_amt = zhongjun_row['amount'] / 1e8 # 中军成交额

        # --- C. 评分模型 ---
        score = (breadth * 0.4) + (avg_3d_ret * 3.0) + (min(total_amt/10, 10) * 2) + (sector_df['pct_chg'].mean() * 1.0)
        
        theme_results.append({
            '题材': sector_name.split('-')[-1],
            '综合热力': round(score, 2),
            '广度%': round(breadth, 1),
            '3日持续%': round(avg_3d_ret, 2),
            '板块成交(亿)': round(total_amt, 2),
            '核心中军': zj_name,
            '中军成交(亿)': round(zj_amt, 2),
            '样本数': len(sector_df)
        })

    # 6. 输出排行榜
    if theme_results:
        result_df = pd.DataFrame(theme_results).sort_values('综合热力', ascending=False)
        
        print("\n" + "🚀" * 30)
        print(f"🔥 今日 A 股主线题材与【核心中军】排行榜 ({latest_date})")
        print("-" * 95)
        # 调整列顺序，让中军信息更显眼
        display_cols = ['题材', '综合热力', '广度%', '3日持续%', '板块成交(亿)', '核心中军', '中军成交(亿)']
        print(result_df[display_cols].head(15).to_string(index=False))
        print("-" * 95)
        
        # 结果研判
        top_theme = result_df.iloc[0]['题材']
        top_zj = result_df.iloc[0]['核心中军']
        print(f"💡 结论：今日最强主线是【{top_theme}】，核心中军是【{top_zj}】。")
        print(f"建议：中军【{top_zj}】不走弱，该题材波段机会就可持续。")
        print("🚀" * 30 + "\n")
    else:
        print("扫描完成，未发现活跃板块。")

if __name__ == "__main__":
    find_main_themes_with_zhongjun()