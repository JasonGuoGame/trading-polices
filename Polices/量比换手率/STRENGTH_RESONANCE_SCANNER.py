import pandas as pd
from sqlalchemy import create_engine, text
import datetime
import warnings

# 屏蔽无关警告
warnings.filterwarnings('ignore')

# --- 1. 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def run_strength_resonance_scanner():
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 启动‘0轴上放量换手’共振选股系统...")

    try:
        with engine.connect() as conn:
            # 第一步：获取最新交易日和上一个交易日
            # 这样可以避免全表扫描，直接命中索引
            date_res = conn.execute(text("SELECT DISTINCT trade_date FROM stk_factors ORDER BY trade_date DESC LIMIT 2")).fetchall()
            
            if len(date_res) < 2:
                print("❌ 错误：数据库中交易日数据不足，无法进行对比。")
                return
            
            today = date_res[0][0]
            yesterday = date_res[1][0]
            print(f"📅 分析日期：今日({today}) vs 昨日({yesterday})")

            # 第二步：核心 SQL 执行
            # 逻辑：0轴上 + 换手10-25% + 量比2-5 + 双重红盘 + 主板非ST
            query_sql = text("""
                SELECT 
                    f.symbol AS '代码', 
                    s.name AS '名称', 
                    k.turnover_rate AS '换手%', 
                    f.f_quantity_ratio AS '量比',
                    f.f_macd_dif AS 'DIF',
                    k.close AS '现价',
                    ROUND((k.close - ky.close) / ky.close * 100, 2) AS '今日涨幅%'
                FROM stk_factors f
                INNER JOIN stk_daily_kline k ON f.symbol = k.symbol AND k.trade_date = :t_date
                INNER JOIN stk_daily_kline ky ON f.symbol = ky.symbol AND ky.trade_date = :y_date
                INNER JOIN stocks s ON f.symbol = s.symbol
                WHERE f.trade_date = :t_date
                  AND f.f_macd_dif > 0                         -- 1. MACD在0轴上方
                  AND k.turnover_rate BETWEEN 0.05 AND 0.1        -- 2. 换手率 10%-25%
                  AND f.f_quantity_ratio BETWEEN 3 AND 8        -- 3. 量比 2-5
                  AND k.close > ky.close                       -- 4. 收盘为红 (比昨收高)
                  AND k.close > k.open                         -- 5. 收盘为阳 (比开盘高)
                  AND s.name NOT LIKE '%%ST%%'                 -- 6. 剔除 ST
                  AND s.name NOT LIKE '%%退%%'                 -- 7. 剔除退市
                ORDER BY f.f_vol_ratio DESC                     -- 按照量能爆发力排序
            """)

            # 使用 pandas 读取 SQL 结果，传入参数绑定
            df_results = pd.read_sql(query_sql, conn, params={"t_date": today, "y_date": yesterday})

        # 第三步：展示结果
        if not df_results.empty:
            print("\n" + "🔥" * 12 + " 今日‘强共振’精选名单 " + "🔥" * 12)
            print("-" * 100)
            # 设置显示宽度
            pd.set_option('display.max_columns', None)
            pd.set_option('display.width', 1000)
            print(df_results.to_string(index=False))
            print("-" * 100)
            print(f"💡 研判：共发现 {len(df_results)} 只处于强势拉升带的个股。")
            print("💡 建议：重点关注【换手%】在 15% 附近且【涨幅%】尚未封板（如 5%-7%）的标的。")
            print("🔥" * 36 + "\n")
        else:
            print(f"\n今日 ({today}) 暂无完全符合“0轴上+量比2-5+高换手”的硬核个股。")

    except Exception as e:
        print(f"❌ 策略运行失败: {e}")

if __name__ == "__main__":
    run_strength_resonance_scanner()