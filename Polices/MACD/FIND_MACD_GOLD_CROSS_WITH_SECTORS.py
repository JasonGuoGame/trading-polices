import pandas as pd
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def find_macd_cross_with_sectors():
    print(f"[{datetime.datetime.now()}] 启动主板“0轴金叉”深度筛选（带板块信息）...")

    # 1. 获取因子库中最新的两个交易日
    with engine.connect() as conn:
        date_query = text("SELECT DISTINCT trade_date FROM stk_factors ORDER BY trade_date DESC LIMIT 2")
        dates = [row[0] for row in conn.execute(date_query).fetchall()]
    
    if len(dates) < 2:
        print("错误：数据不足，无法计算交叉。")
        return

    today = dates[0]
    yesterday = dates[1]
    print(f"分析周期：昨日({yesterday}) -> 今日({today})")

    # 2. 核心 SQL：金叉逻辑 + 四重过滤 + 板块聚合
    # 使用 GROUP_CONCAT 将多个板块名合并，并过滤掉无意义的大板块（如“沪深A股”）
    query_sql = text(f"""
        SELECT 
            t.symbol as '代码', 
            s.name as '名称', 
            GROUP_CONCAT(DISTINCT r.sector_name SEPARATOR ', ') as '所属板块',
            t.f_macd_dif as DIF, 
            t.f_macd_dea as DEA,
            t.f_macd_hist as HIST
        FROM stk_factors t
        JOIN stk_factors y ON t.symbol = y.symbol AND y.trade_date = '{yesterday}'
        JOIN stocks s ON t.symbol = s.symbol
        LEFT JOIN stock_sector_relation r ON t.symbol = r.symbol 
            AND (r.sector_name LIKE '概念-%%' OR r.sector_name LIKE 'THY%%' OR r.sector_name LIKE 'SW%%')
        WHERE t.trade_date = '{today}'
          AND (t.symbol LIKE '60%%' OR t.symbol LIKE '00%%') -- 只要主板
          AND s.name NOT LIKE '%%ST%%'                       -- 剔除 ST
          AND s.name NOT LIKE '%%退%%'                       -- 剔除退市
          AND t.f_macd_dif > t.f_macd_dea                    -- 今日金叉
          AND y.f_macd_dif <= y.f_macd_dea                  -- 昨天未金叉
          AND t.f_macd_dif > 0                              -- DIF 在 0 轴上方
        GROUP BY t.symbol, s.name                            -- 按股票分组以合并板块
        ORDER BY t.f_macd_hist DESC;
    """)

    with engine.connect() as conn:
        df_results = pd.read_sql(query_sql, conn)

    # 3. 输出报告
    if not df_results.empty:
        print("\n" + "⭐" * 30)
        print(f"🚀 筛选结果清单（共 {len(df_results)} 只）")
        print("-" * 100)
        
        # 调整显示宽度，防止板块信息被截断
        pd.set_option('display.max_colwidth', 60)
        
        # 只显示前几列关键信息和板块
        print(df_results.to_string(index=False))
        
        print("-" * 100)
        print("💡 研判建议：重点观察板块字段中包含‘今日热力主线’的个股，共振效应更强。")
        print("⭐" * 30)
    else:
        print("\n今日主板市场未发现符合条件的 0 轴金叉个股。")

if __name__ == "__main__":
    find_macd_cross_with_sectors()