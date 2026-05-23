import pandas as pd
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def get_limit_down_analysis():
    print(f"[{datetime.datetime.now()}] 正在扫描全市场跌停个股及其分布...")

    try:
        with engine.connect() as conn:
            # 1. 获取最近两个交易日
            date_query = text("SELECT DISTINCT trade_date FROM stk_daily_kline ORDER BY trade_date DESC LIMIT 2")
            dates = [row[0] for row in conn.execute(date_query).fetchall()]
            
            if len(dates) < 2:
                print("数据库数据不足，无法计算跌停。")
                return

            today = dates[0]
            yesterday = dates[1]
            print(f"分析日期：昨日 {yesterday} -> 今日 {today}")

            # 2. 读取全市场主板行情 (过滤ST，或者保留ST看风险)
            # 这里默认包含主板 10% 跌停逻辑
            query = text("""
                SELECT k.symbol, s.name, k.close, k.open, 
                       (SELECT close FROM stk_daily_kline WHERE symbol = k.symbol AND trade_date = :yest) as prev_close
                FROM stk_daily_kline k
                JOIN stocks s ON k.symbol = s.symbol
                WHERE k.trade_date = :today
                  AND (k.symbol LIKE '60%' OR k.symbol LIKE '00%') -- 只要主板
                ORDER BY k.symbol ASC
            """)
            df_all = pd.read_sql(query, conn, params={"today": today, "yest": yesterday})

        # 3. 判定跌停逻辑
        # 跌停标准：今日收盘价 <= round(昨日收盘 * 0.90, 2)
        # 我们使用 0.902 宽限值来兼容一些四舍五入的特殊情况
        df_all['is_limit_down'] = df_all['close'] <= (df_all['prev_close'] * 0.902).round(2)
        
        limit_down_stocks = df_all[df_all['is_limit_down'] == True].copy()

        if limit_down_stocks.empty:
            print(f"今日 ({today}) 全市场未发现主板跌停个股。")
            return

        # 4. 关联细分板块
        with engine.connect() as conn:
            query_rel = text("""
                SELECT symbol, sector_name as '板块'
                FROM stock_sector_relation
                WHERE sector_name LIKE '概念-%' OR sector_name LIKE 'THY%' OR sector_name LIKE 'SW%'
            """)
            df_relation = pd.read_sql(query_rel, conn)

        # 合并跌停股与板块
        df_final = pd.merge(limit_down_stocks, df_relation, on='symbol', how='inner')

        # 5. 按照板块内跌停股票数量排序
        sector_counts = df_final.groupby('板块')['symbol'].nunique().sort_values(ascending=False)

        print("\n" + "❄️" * 10 + f" 今日跌停重灾区排行榜（按跌停数排序） " + "❄️" * 10)
        print(f"统计日期：{today} | 总计 {len(limit_down_stocks)} 只跌停股")
        print("=" * 85)

        # 6. 循环输出排名前 20 的板块
        for sector, count in sector_counts.head(20).items():
            # 获取该板块下所有的跌停个股
            sub_df = df_final[df_final['板块'] == sector].drop_duplicates('symbol')
            
            # 计算该股今日跌幅
            sub_df['跌幅%'] = ((sub_df['close'] - sub_df['prev_close']) / sub_df['prev_close'] * 100).round(2)

            print(f"\n💀 板块：【{sector}】 | 跌停个股数: {count}")
            print("-" * 85)
            print(sub_df[['symbol', 'name', '跌幅%', 'close']].to_string(index=False))

        print("\n" + "=" * 85)
        print(f"✅ 扫描完成。当前风险最集中的板块是：{sector_counts.index[0]}")
        print(f"💡 警示：若一个板块连续多日出现在此榜单前列，说明该题材已彻底进入‘退潮期’，切勿抄底。")

    except Exception as e:
        print(f"❌ 运行失败: {e}")

if __name__ == "__main__":
    get_limit_down_analysis()