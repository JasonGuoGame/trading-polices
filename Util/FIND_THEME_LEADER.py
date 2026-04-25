import pandas as pd
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def find_main_themes_from_db():
    print(f"[{datetime.datetime.now()}] 正在基于数据库关联进行题材主线扫描...")

    # 1. 从数据库读取最新的 3 日行情数据
    # 这部分保持不变，用于计算涨幅和持续性
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
        print("错误：数据库中没有行情数据，请先同步日线。")
        return

    # 2. 获取最新一天的日期并计算涨幅
    latest_date = df_all['trade_date'].max()
    df_today = df_all[df_all['trade_date'] == latest_date].copy()
    df_today['pct_chg'] = (df_today['close'] - df_today['open']) / df_today['open'] * 100

    # 3. 从数据库读取股票与板块的关联关系 (关键修改点)
    # 我们可以通过 SQL 直接把我们需要分析的板块（行业、概念等）取出来
    query_relation = """
    SELECT r.symbol, r.sector_name 
    FROM stock_sector_relation r
    WHERE r.sector_name LIKE '概念-%%' 
       OR r.sector_name LIKE 'THY%%' 
       OR r.sector_name LIKE 'SW1%%'
       OR r.sector_name LIKE 'TGN%%'
    """
    # ------------------------------------------

    try:
        df_relation = pd.read_sql(query_relation, engine)
    except Exception as e:
        print(f"读取板块关联失败: {e}")
        return
    
    if df_relation.empty:
        print("错误：数据库中没有板块关联数据，请先导入板块信息。")
        return

    # 4. 合并行情与板块数据
    # 将今日行情与板块映射表进行左连接
    df_merged = pd.merge(df_relation, df_today, on='symbol', how='inner')

    # 5. 按板块分组计算指标
    theme_results = []
    print("正在聚合计算各板块热力值...")
    
    for sector_name, sector_df in df_merged.groupby('sector_name'):
        if len(sector_df) < 6: # 样本太少的题材不看
            continue
            
        # --- A. 赚钱广度 (Breadth) ---
        breadth = (len(sector_df[sector_df['pct_chg'] > 2.5]) / len(sector_df)) * 100
        
        # --- B. 资金热度 (Amount) ---
        total_amt = sector_df['amount'].sum() / 1e8 # 亿元
        
        # --- C. 持续性 (Persistence) ---
        # 获取该板块下所有股的 3 日平均表现
        stocks_in_sector = sector_df['symbol'].unique()
        avg_3d_ret = ((df_all[df_all['symbol'].isin(stocks_in_sector)]['close'] - 
                       df_all[df_all['symbol'].isin(stocks_in_sector)]['open']) / 
                      df_all[df_all['symbol'].isin(stocks_in_sector)]['open']).mean() * 100

        # --- D. 综合评分 ---
        # 评分公式：广度(40%) + 持续性(30%) + 成交额权重(20%) + 平均涨幅(10%)
        score = (breadth * 0.4) + (avg_3d_ret * 3.0) + (min(total_amt/10, 10) * 2) + (sector_df['pct_chg'].mean() * 1.0)
        
        theme_results.append({
            '题材': sector_name.split('-')[-1], # 清理前缀
            '综合热力': round(score, 2),
            '广度%': round(breadth, 1),
            '3日持续%': round(avg_3d_ret, 2),
            '成交(亿)': round(total_amt, 2),
            '股数': len(sector_df)
        })

    # 6. 排序输出
    if theme_results:
        result_df = pd.DataFrame(theme_results).sort_values('综合热力', ascending=False)
        
        print("\n" + "🚀" * 20)
        print(f"🔥 今日 A 股【数据库级】题材探测结果 ({latest_date})")
        print("-" * 65)
        print(result_df.head(15).to_string(index=False))
        print("-" * 65)
        
        # 结果分析
        top_name = result_df.iloc[0]['题材']
        print(f"💡 结论：当前最强主线为【{top_name}】")
        print(f"建议：在该板块内寻找形态好的个股。")
        print("🚀" * 20 + "\n")
    else:
        print("分析结束，今日无明显活跃板块。")

if __name__ == "__main__":
    find_main_themes_from_db()