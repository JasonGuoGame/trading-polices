import pandas as pd
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def get_limit_up_streak_by_count():
    print(f"[{datetime.datetime.now()}] 正在按板块连板密度扫描全市场...")

    try:
        with engine.connect() as conn:
            # 1. 获取最近 15 个交易日
            date_query = text("SELECT DISTINCT trade_date FROM stk_daily_kline ORDER BY trade_date DESC LIMIT 15")
            dates = [row[0] for row in conn.execute(date_query).fetchall()]
            
            if len(dates) < 2:
                print("数据不足。")
                return

            latest_date = dates[0]
            
            # 2. 读取主板非ST行情
            query = text("""
                SELECT k.symbol, s.name, k.trade_date, k.close
                FROM stk_daily_kline k
                JOIN stocks s ON k.symbol = s.symbol
                WHERE k.trade_date >= :start_date
                  AND (k.symbol LIKE '60%' OR k.symbol LIKE '00%')
                  AND s.name NOT LIKE '%ST%'
                  AND s.name NOT LIKE '%退%'
                ORDER BY k.symbol, k.trade_date ASC
            """)
            df_all = pd.read_sql(query, conn, params={"start_date": dates[-1]})

        # 3. 计算每只股票的连板天数
        results = []
        for symbol, df in df_all.groupby('symbol'):
            df = df.sort_values('trade_date')
            df['prev_close'] = df['close'].shift(1)
            # 判定涨停条件
            df['is_limit_up'] = df['close'] >= (df['prev_close'] * 1.098).round(2)
            
            # 必须今天仍是涨停
            if df.empty or not df['is_limit_up'].iloc[-1]:
                continue
            
            # 计算连续涨停天数
            streak = 0
            for i in range(len(df)-1, -1, -1):
                if df['is_limit_up'].iloc[i]:
                    streak += 1
                else:
                    break
            
            if streak >= 2:
                results.append({
                    'symbol': symbol,
                    'name': df['name'].iloc[-1],
                    'streak': streak
                })

        if not results:
            print(f"今日 ({latest_date}) 未发现 2 连板以上个股。")
            return

        df_streaks = pd.DataFrame(results)

        # 4. 关联细分板块
        with engine.connect() as conn:
            query_rel = text("""
                SELECT symbol, sector_name as '板块'
                FROM stock_sector_relation
                WHERE sector_name LIKE '概念-%' OR sector_name LIKE 'THY%' OR sector_name LIKE 'SW%'
            """)
            df_relation = pd.read_sql(query_rel, conn)

        df_final = pd.merge(df_streaks, df_relation, on='symbol', how='inner')

        # --- 核心修改：按照板块内个股数量进行统计和排序 ---
        # 统计每个板块下有多少只不同的 symbol
        sector_counts = df_final.groupby('板块')['symbol'].nunique().sort_values(ascending=False)

        print("\n" + "🔥" * 10 + f" 今日连板阵地排行榜（按股票数排序） " + "🔥" * 10)
        print(f"统计日期：{latest_date} | 总计 {len(df_streaks)} 只连板股")
        print("=" * 85)

        # 5. 循环输出排名前 20 的板块
        for sector, count in sector_counts.head(20).items():
            # 获取该板块下所有不重复的股票信息
            sub_df = df_final[df_final['板块'] == sector].drop_duplicates('symbol').sort_values('streak', ascending=False)
            
            print(f"\n📍 板块：【{sector}】 | 连板个股数: {count}")
            print("-" * 85)
            # 整理显示格式
            sub_df['高度'] = sub_df['streak'].apply(lambda x: f"{x}连板")
            print(sub_df[['symbol', 'name', '高度']].to_string(index=False))

        print("\n" + "=" * 85)
        print(f"✅ 扫描完成。今日最强群发板块：{sector_counts.index[0]} ({sector_counts.iloc[0]}只股连板)")

    except Exception as e:
        print(f"❌ 运行失败: {e}")

if __name__ == "__main__":
    get_limit_up_streak_by_count()