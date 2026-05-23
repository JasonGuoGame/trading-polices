import pandas as pd
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def screen_by_amount():
    print(f"[{datetime.datetime.now()}] 正在扫描大成交额个股（15亿+）...")

    try:
        with engine.connect() as conn:
            # 1. 获取最新日期
            date_res = conn.execute(text("SELECT MAX(trade_date) FROM stk_daily_kline")).fetchone()
            latest_date = date_res[0]
            
            if latest_date is None:
                print("数据库中没有行情数据。")
                return

            print(f"当前分析日期：{latest_date}")

            # 2. 使用 text() 和参数绑定（解决 % 报错和日期格式问题的终极方案）
            # 注意：在 text() 模式下，SQL 里的 % 不需要写成 %%，因为这里不经过 Python 的字符串格式化
            query = text("""
                SELECT k.symbol as '代码', s.name as '名称', k.close as '收盘价', 
                       k.amount as 'amount_raw', 
                       (k.close - k.open)/k.open * 100 as 'pct_chg'
                FROM stk_daily_kline k
                JOIN stocks s ON k.symbol = s.symbol
                WHERE k.trade_date = :t_date
                  AND k.amount >= 1500000000
                  AND (k.symbol LIKE '60%' OR k.symbol LIKE '00%')
                  AND s.name NOT LIKE '%ST%'
                  AND s.name NOT LIKE '%退%'
                ORDER BY k.amount DESC
            """)
            
            # 使用参数绑定传递日期
            df = pd.read_sql(query, conn, params={"t_date": latest_date})

        if df.empty:
            print(f"今日未发现符合条件的个股。")
            return

        # 3. 数据整理
        df['成交额(亿)'] = (df['amount_raw'] / 100000000).round(2)
        df['涨幅%'] = df['pct_chg'].round(2)

        # 4. 逻辑分类
        above_20 = df[df['amount_raw'] >= 2000000000].copy()
        between_15_20 = df[(df['amount_raw'] >= 1500000000) & (df['amount_raw'] < 2000000000)].copy()

        # 5. 输出展示
        print("\n" + "🔥" * 10 + " 第一梯队：成交额 20 亿以上 " + "🔥" * 10)
        if not above_20.empty:
            print(above_20[['代码', '名称', '收盘价', '涨幅%', '成交额(亿)']].to_string(index=False))
        else:
            print("无")

        print("\n" + "💎" * 10 + " 第二梯队：成交额 15 - 20 亿 " + "💎" * 10)
        if not between_15_20.empty:
            print(between_15_20[['代码', '名称', '收盘价', '涨幅%', '成交额(亿)']].to_string(index=False))
        else:
            print("无")

    except Exception as e:
        print(f"❌ 运行发生错误: {e}")

if __name__ == "__main__":
    screen_by_amount()