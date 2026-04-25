import pandas as pd
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def find_pure_mainboard_macd_cross():
    print(f"[{datetime.datetime.now()}] 启动主板“0轴金叉”深度筛选（按距离零轴近度排序）...")

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

    # 2. 核心 SQL：金叉逻辑 + 严格过滤 + 距离排序
    # 排序逻辑说明：ABS(t.f_macd_dif) 越小，代表快慢线越贴近零轴
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
          AND s.name NOT LIKE '%%退%%'                       -- 剔除退市风险
          AND t.f_macd_dif > t.f_macd_dea                    -- 今日金叉
          AND y.f_macd_dif <= y.f_macd_dea                  -- 昨天未金叉
          AND t.f_macd_dif > 0                              -- DIF 在 0 轴上方
        ORDER BY ABS(t.f_macd_dif) ASC                       -- 关键修改：距离零轴越近排越前
        LIMIT 50;                                            -- 取最贴线的前50只
    """)

    with engine.connect() as conn:
        df_results = pd.read_sql(query_sql, conn)

    # 3. 输出报告
    if not df_results.empty:
        print("\n" + "💎" * 20)
        print(f"🚀 筛选完成：发现 {len(df_results)} 只【贴线金叉】个股")
        print("-" * 80)
        # 打印完整表格
        print(df_results.to_string(index=False))
        print("-" * 80)
        print("💡 研判依据：这些股票刚从零轴上方起跳，位置极低，爆发力通常比高位金叉更强。")
        print("💎" * 20)
    else:
        print("\n今日主板市场未发现符合条件的 0 轴贴线金叉个股。")

if __name__ == "__main__":
    find_pure_mainboard_macd_cross()