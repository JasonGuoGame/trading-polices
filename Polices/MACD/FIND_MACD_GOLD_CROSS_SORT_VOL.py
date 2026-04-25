import pandas as pd
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def find_pure_mainboard_macd_cross():
    print(f"[{datetime.datetime.now()}] 启动主板“0轴金叉”深度筛选（按量能倍数排序）...")

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

    # 2. 核心 SQL：金叉逻辑 + 四重过滤 + 量能排序
    # 核心修改点：ORDER BY f.f_vol_ratio DESC
    query_sql = text(f"""
        SELECT 
            t.symbol as '代码', 
            s.name as '名称', 
            t.f_macd_dif as DIF, 
            t.f_macd_dea as DEA,
            t.f_macd_hist as HIST,
            f.f_vol_ratio as '量能倍数'
        FROM stk_factors t
        JOIN stk_factors y ON t.symbol = y.symbol AND y.trade_date = '{yesterday}'
        JOIN stocks s ON t.symbol = s.symbol
        LEFT JOIN stk_factors f ON t.symbol = f.symbol AND f.trade_date = t.trade_date
        WHERE t.trade_date = '{today}'
          AND (t.symbol LIKE '60%%' OR t.symbol LIKE '00%%') -- 只要主板 (60/00开头)
          AND s.name NOT LIKE '%%ST%%'                       -- 剔除 ST
          AND s.name NOT LIKE '%%退%%'                       -- 剔除退市股
          AND t.f_macd_dif > t.f_macd_dea                    -- 今日金叉
          AND y.f_macd_dif <= y.f_macd_dea                  -- 昨天未金叉
          AND t.f_macd_dif > 0                              -- DIF 在 0 轴上方
        ORDER BY f.f_vol_ratio DESC                         -- 关键修改：按照量能倍数从大到小排序
        LIMIT 50;                                           -- 取前 50 只异动最明显的
    """)

    with engine.connect() as conn:
        df_results = pd.read_sql(query_sql, conn)

    # 3. 输出报告
    if not df_results.empty:
        print("\n" + "🔥" * 15)
        print(f"🚀 筛选完成：今日“量能爆表”的 0 轴金叉股清单")
        print("-" * 75)
        # 打印完整结果
        print(df_results.to_string(index=False))
        print("-" * 75)
        print("💡 研判依据：已剔除ST及双创板。优先展示金叉同时伴随大幅放量的个股（主力介入深）。")
        print("🔥" * 15)
    else:
        print("\n今日主板市场未发现符合条件的 0 轴金叉个股。")

if __name__ == "__main__":
    find_pure_mainboard_macd_cross()