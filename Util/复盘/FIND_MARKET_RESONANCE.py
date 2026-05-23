import pandas as pd
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def find_main_force_resonance():
    print(f"[{datetime.datetime.now()}] 正在启动‘资金流向+个股异动’双维度共振分析...")

    # 1. 定义噪音屏蔽词 (防止属性类标签干扰)
    NOISE_KEYWORDS = [
        '%融资融券%', '%沪股通%', '%深股通%', '%MSCI%', '%标准普尔%', '%富时罗素%', 
        '%中证%', '%上证%', '%昨日%', '%小盘%', '%大盘%', '%权重%', '%两融%', 
        '%证金%', '%汇金%', '%基金重仓%', '%预盈预增%', '%转债%', '%破净%'
    ]

    try:
        with engine.connect() as conn:
            # A. 获取日期
            date_res = conn.execute(text("SELECT MAX(trade_date) FROM stk_daily_kline")).fetchone()
            today = date_res[0]
            yest_res = conn.execute(text("SELECT DISTINCT trade_date FROM stk_daily_kline WHERE trade_date < :t ORDER BY trade_date DESC LIMIT 1"), {"t": today}).fetchone()
            yesterday = yest_res[0]

            print(f"分析周期：{yesterday} -> {today}")

            # B. 核心 SQL 逻辑
            # 1. 寻找今天【量能倍数 > 1.8】或【绝对金额 > 15亿】的活跃股
            # 2. 关联板块，并且板块今日必须是【主力净流入 > 1.0亿】
            noise_filter = " AND " + " AND ".join([f"r.sector_name NOT LIKE '{k}'" for k in NOISE_KEYWORDS])
            
            query = text(f"""
                SELECT 
                    flow.sector_name as '板块',
                    flow.net_inflow_amount as '板块流入(亿)',
                    flow.net_inflow_rate as '流入率%',
                    k_t.symbol as '代码', 
                    s.name as '名称', 
                    k_t.amount as 'today_amt',
                    k_y.amount as 'yest_amt',
                    (k_t.close - k_y.close)/k_y.close * 100 as 'pct_chg'
                FROM stk_daily_kline k_t
                JOIN stocks s ON k_t.symbol = s.symbol
                JOIN stock_sector_relation r ON k_t.symbol = r.symbol
                JOIN stk_sector_fund_flow flow ON (
                    r.sector_name = CONCAT('行业-', flow.sector_name) OR 
                    r.sector_name = CONCAT('概念-', flow.sector_name)
                )
                LEFT JOIN stk_daily_kline k_y ON k_t.symbol = k_y.symbol AND k_y.trade_date = :yest
                WHERE k_t.trade_date = :today
                  AND flow.trade_date = :today
                  AND flow.net_inflow_amount > 1.0  -- 板块流入门槛：1亿
                  AND (
                      k_t.amount > k_y.amount * 1.8 OR   -- 增量逻辑：量能比昨天放大1.8倍
                      k_t.amount >= 1500000000           -- 存量逻辑：绝对金额保持在15亿以上
                  )
                  AND (k_t.symbol LIKE '60%' OR k_t.symbol LIKE '00%' OR k_t.symbol LIKE '30%')
                  AND s.name NOT LIKE '%ST%'
                  {noise_filter}
            """)
            
            df = pd.read_sql(query, conn, params={"today": today, "yest": yesterday})

        if df.empty:
            print("今日未发现板块与个股的强力共振。")
            return

        # 3. 板块汇总计算热力分
        # 热力分 = 板块流入额(50%) + 板块内异动股数量(50%)
        # 我们先算每个板块的异动股数
        sector_agg = df.groupby(['板块', '板块流入(亿)', '流入率%']).agg({
            '代码': 'nunique'
        }).rename(columns={'代码': '异动股数'}).reset_index()

        # 计算评分 (简单加权)
        sector_agg['热力评分'] = (sector_agg['板块流入(亿)'] * 0.5) + (sector_agg['异动股数'] * 2.0)
        
        # 排序取前 15 名
        sector_rank = sector_agg.sort_values('热力评分', ascending=False).head(15)

        # 4. 输出报告
        print("\n" + "⚔️" * 10 + f" 今日 A 股【主力共振】进攻主线 " + "⚔️" * 10)
        print("=" * 115)

        for _, row in sector_rank.iterrows():
            sec_name = row['板块']
            # 获取该板块下最活跃的个股名单
            sub_df = df[df['板块'] == sec_name].sort_values('today_amt', ascending=False).head(8)
            sub_df = sub_df.drop_duplicates(subset=['代码'])

            print(f"\n🚩 【主线】{sec_name} | 💰 主力流入: {row['板块流入(亿)']}亿 | 🚀 异动家数: {row['异动股数']}")
            print("-" * 115)
            # 格式化输出
            report_df = pd.DataFrame()
            report_df['代码'] = sub_df['代码']
            report_df['名称'] = sub_df['名称']
            report_df['涨幅%'] = sub_df['pct_chg'].round(2)
            report_df['今日成交(亿)'] = (sub_df['today_amt'] / 1e8).round(2)
            report_df['量能倍数'] = (sub_df['today_amt'] / (sub_df['yest_amt'] + 0.1)).round(2)
            print(report_df.to_string(index=False))

        print("\n" + "=" * 115)
        print(f"✅ 探测完成。当前最具持续性的进攻方向是：{sector_rank.iloc[0]['板块']}")

    except Exception as e:
        print(f"❌ 运行失败: {e}")

if __name__ == "__main__":
    find_main_force_resonance()