import pandas as pd
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def screen_top_20_amount_sectors():
    print(f"[{datetime.datetime.now()}] 正在扫描全市场资金最集中的 Top 20 板块...")

    try:
        with engine.connect() as conn:
            # 1. 获取最新交易日
            date_res = conn.execute(text("SELECT MAX(trade_date) FROM stk_daily_kline")).fetchone()
            latest_date = date_res[0]
            
            if latest_date is None:
                print("错误：数据库中没有行情数据。")
                return

            print(f"分析日期：{latest_date}")

            # 2. 核心 SQL：筛选成交额 > 10亿 的股票及其细分板块
            query = text("""
                SELECT 
                    r.sector_name as '板块',
                    k.symbol as '代码', 
                    s.name as '名称', 
                    k.amount as 'amount_raw',
                    (k.close - k.open)/k.open * 100 as '涨幅%'
                FROM stk_daily_kline k
                JOIN stocks s ON k.symbol = s.symbol
                JOIN stock_sector_relation r ON k.symbol = r.symbol
                WHERE k.trade_date = :t_date
                  AND k.amount >= 1000000000  -- 10 亿门槛
                  AND (r.sector_name LIKE '概念-%' OR r.sector_name LIKE 'THY%' OR r.sector_name LIKE 'SW%')
                  AND s.name NOT LIKE '%ST%'
                  AND s.name NOT LIKE '%退%'
                ORDER BY k.amount DESC
            """)
            
            df = pd.read_sql(query, conn, params={"t_date": latest_date})

        if df.empty:
            print(f"今日未发现大额成交个股。")
            return

        # 3. 数据处理：单位转换
        df['成交额(亿)'] = (df['amount_raw'] / 100000000).round(2)
        df['涨幅%'] = df['涨幅%'].round(2)

        # 4. 计算板块总热度 (按板块内大票的总成交额排序)
        sector_heat = df.groupby('板块')['成交额(亿)'].sum().sort_values(ascending=False)
        
        # --- 核心修改：只取前 20 个板块 ---
        top_20_sectors = sector_heat.head(20)

        print("\n" + "🏆" * 10 + f" 今日资金流向最强 Top 20 细分题材 " + "🏆" * 10)
        print("=" * 85)

        # 5. 循环输出
        for rank, (sector, total_amt) in enumerate(top_20_sectors.items(), 1):
            # 获取该板块下的个股
            sub_df = df[df['板块'] == sector].sort_values('成交额(亿)', ascending=False)
            
            # 去掉重复个股显示（因为一只股可能属于多个板块，在这里正常显示）
            print(f"\n【Top {rank}】板块：{sector} | 板块大票总成交: {total_amt:.2f} 亿")
            print("-" * 85)
            # 格式化打印列
            print(sub_df[['代码', '名称', '涨幅%', '成交额(亿)']].to_string(index=False))

        print("\n" + "=" * 85)
        print(f"✅ 全市场 Top 20 板块扫描完成。")
        print(f"💡 建议：重点关注排名前 5 的板块，那是当下的绝对主战场。")

    except Exception as e:
        print(f"❌ 运行发生错误: {e}")

if __name__ == "__main__":
    screen_top_20_amount_sectors()