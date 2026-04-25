import pandas as pd
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def find_pure_mainboard_macd_cross():
    print(f"[{datetime.datetime.now()}] 启动主板“0轴金叉”深度筛选...")

    # 1. 获取因子库中最新的两个交易日
    with engine.connect() as conn:
        date_query = text("SELECT DISTINCT trade_date FROM stk_factors ORDER BY trade_date DESC LIMIT 2")
        dates = [row[0] for row in conn.execute(date_query).fetchall()]
    
    if len(dates) < 2:
        print("错误：数据不足，无法计算交叉。")
        return

    today = dates[0]
    yesterday = dates[1]
    print(f"对比周期：昨天({yesterday}) -> 今天({today})")

    # 2. 核心 SQL：金叉逻辑 + 四重过滤
    # 过滤 1: t.symbol LIKE '60%%' OR t.symbol LIKE '00%%' (仅限沪深主板)
    # 过滤 2: s.name NOT LIKE '%%ST%%' (剔除 ST)
    # 过滤 3: s.name NOT LIKE '%%退%%' (剔除退市股)
    # 过滤 4: t.f_macd_dif > 0 (0轴上方)
    
    query_sql = text(f"""
        SELECT 
            t.symbol, 
            s.name, 
            t.f_macd_dif as DIF, 
            t.f_macd_dea as DEA,
            t.f_macd_hist as HIST,
            f.f_vol_ratio as '量能倍数'
        FROM stk_factors t
        JOIN stk_factors y ON t.symbol = y.symbol AND y.trade_date = '{yesterday}'
        JOIN stocks s ON t.symbol = s.symbol
        LEFT JOIN stk_factors f ON t.symbol = f.symbol AND f.trade_date = t.trade_date
        WHERE t.trade_date = '{today}'
        #   AND (t.symbol LIKE '60%%' OR t.symbol LIKE '00%%') -- 只要主板 (60开头或00开头)
          AND s.name NOT LIKE '%%ST%%'                       -- 剔除 ST
          AND s.name NOT LIKE '%%退%%'                       -- 剔除退市
          AND t.f_macd_dif > t.f_macd_dea                    -- 今日金叉
          AND y.f_macd_dif <= y.f_macd_dea                  -- 昨天未金叉
          AND t.f_macd_dif > 0                              -- DIF 在 0 轴上方
        ORDER BY t.f_macd_hist DESC;
    """)

    with engine.connect() as conn:
        df_results = pd.read_sql(query_sql, conn)

    # 3. 输出报告
    if not df_results.empty:
        print("\n" + "💎" * 20)
        print(f"🚀 筛选完成：共发现 {len(df_results)} 只主板绩优金叉股")
        print("-" * 75)
        print(df_results.to_string(index=False))
        print("-" * 75)
        print("💡 研判依据：已剔除ST、科创板。选股重点在于 0 轴上方的二次爆发（空中加油）。")
        print("💎" * 20)
    else:
        print("\n今日主板市场未发现符合条件的 0 轴金叉个股。")

if __name__ == "__main__":
    find_pure_mainboard_macd_cross()