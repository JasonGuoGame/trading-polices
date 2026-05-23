import pandas as pd
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def find_pure_mainboard_macd_cross():
    print(f"[{datetime.datetime.now()}] 正在扫描【0轴上方·首日金叉】个股...")

    # 1. 获取因子库中最新的两个交易日
    with engine.connect() as conn:
        date_query = text("SELECT DISTINCT trade_date FROM stk_factors ORDER BY trade_date DESC LIMIT 2")
        dates = [row[0] for row in conn.execute(date_query).fetchall()]
    
    if len(dates) < 2:
        print("错误：因子库数据不足，无法进行昨日与今日对比。")
        return

    today = dates[0]
    yesterday = dates[1]
    print(f"对比周期：昨天({yesterday}) -> 今天({today})")

    # 2. 核心 SQL：精准金叉判定
    # 逻辑：
    # t (today) 必须 DIF > DEA
    # y (yesterday) 必须 DIF <= DEA (确保今日是金叉发生的第一天)
    # t.DIF > 0 (确保在0轴上方，属于强势区)
    query_sql = text(f"""
        SELECT 
            t.symbol as '代码', 
            s.name as '名称', 
            t.f_macd_dif as '今日DIF', 
            t.f_macd_dea as '今日DEA',
            t.f_macd_hist as '今日红柱',
            f.f_vol_ratio as '量能倍数'
        FROM stk_factors t
        JOIN stk_factors y ON t.symbol = y.symbol AND y.trade_date = :yest
        JOIN stocks s ON t.symbol = s.symbol
        LEFT JOIN stk_factors f ON t.symbol = f.symbol AND f.trade_date = t.trade_date
        WHERE t.trade_date = :today
          AND (t.symbol LIKE '60%%' OR t.symbol LIKE '00%%' OR t.symbol LIKE '00%%') -- 只要沪深主板
          AND s.name NOT LIKE '%%ST%%'                       -- 剔除 ST
          AND s.name NOT LIKE '%%退%%'                       -- 剔除退市股
          AND t.f_macd_dif > t.f_macd_dea                    -- 条件1: 今日处于金叉状态
          AND y.f_macd_dif <= y.f_macd_dea                  -- 条件2: 昨天尚未金叉 (精准锁定首日)
          AND t.f_macd_dif > 0                              -- 条件3: DIF在0轴上方 (空中加油)
          AND t.f_macd_dif < 2.0                            -- 条件4: DIF不宜过高 (防止高位二次金叉风险)
        ORDER BY t.f_macd_hist DESC;                         -- 动能最强的排前面
    """)

    try:
        with engine.connect() as conn:
            df_results = pd.read_sql(query_sql, conn, params={"today": today, "yest": yesterday})

        # 3. 输出可视化报告
        if not df_results.empty:
            print("\n" + "🔥" * 12 + " MACD 0轴上‘首日金叉’精准名单 " + "🔥" * 12)
            print(f"统计日期: {today} | 选出个股: {len(df_results)} 只")
            print("-" * 95)
            # 格式化输出
            print(df_results.to_string(index=False))
            print("-" * 95)
            print("💡 操盘研判：")
            print("1. 这一信号代表股价在多头区域完成回调，今日重新发起进攻。")
            print("2. 重点关注【量能倍数】> 1.8 且【今日红柱】明显伸长的个股。")
            print("3. 若选出标的属于当前主线题材，则是极高胜率的买点。")
            print("🔥" * 35)
        else:
            print(f"\n今日 ({today}) 主板市场未发现符合‘首日金叉’形态的个股。")

    except Exception as e:
        print(f"❌ 运行失败: {e}")

if __name__ == "__main__":
    find_pure_mainboard_macd_cross()