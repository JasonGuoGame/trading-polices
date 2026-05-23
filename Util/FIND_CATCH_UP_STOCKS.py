import pandas as pd
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

# 1. 定义当前最强龙头的集合（作为搜索源）
LEADERS = ['603399.SH', '002192.SZ', '002842.SZ', '603937.SH']

def find_catch_up_opportunities():
    print(f"[{datetime.datetime.now()}] 正在基于龙头逻辑寻找【补涨潜力股】...")

    try:
        with engine.connect() as conn:
            # 第一步：锁定龙头股共同的“高含金量”板块
            # 找出这几只票共同属于哪些细分概念或行业
            sector_query = text("""
                SELECT sector_name, COUNT(*) as cnt 
                FROM stock_sector_relation 
                WHERE symbol IN :leaders 
                  AND (sector_name LIKE '概念-%%' OR sector_name LIKE 'THY3%%')
                GROUP BY sector_name 
                HAVING cnt >= 2
            """)
            shared_sectors = pd.read_sql(sector_query, conn, params={"leaders": LEADERS})
            
            if shared_sectors.empty:
                print("未能识别出龙头的共性板块。")
                return
            
            target_sectors = shared_sectors['sector_name'].tolist()
            print(f"识别到核心共振板块: {target_sectors}")

            # 第二步：在这些板块中寻找满足“补涨形态”的个股
            # 条件：1. 主板非ST; 2. 均线粘合; 3. 今日温和放量; 4. 涨幅尚未透支
            search_query = text("""
                SELECT 
                    f.symbol, 
                    s.name, 
                    r.sector_name as '所属板块',
                    f.f_ma_cohesion as '均线粘合度',
                    f.f_vol_ratio as '量能倍数',
                    (k.close - k.open)/k.open * 100 as '今日涨幅%'
                FROM stk_factors f
                JOIN stocks s ON f.symbol = s.symbol
                JOIN stk_daily_kline k ON f.symbol = k.symbol AND f.trade_date = k.trade_date
                JOIN stock_sector_relation r ON f.symbol = r.symbol
                WHERE f.trade_date = (SELECT MAX(trade_date) FROM stk_factors)
                  AND r.sector_name IN :sectors
                  AND f.symbol NOT IN :leaders          -- 排除已经飞起的龙头
                  AND (f.symbol LIKE '60%' OR f.symbol LIKE '00%') -- 只要主板
                  AND s.name NOT LIKE '%ST%'            -- 剔除ST
                  AND f.f_ma_cohesion < 0.04            -- 均线高度粘合 (蓄势)
                  AND f.f_vol_ratio BETWEEN 1.2 AND 2.5 -- 刚开始放量 (主力试盘)
                  AND (k.close - k.open)/k.open * 100 BETWEEN 1.5 AND 6.0 -- 涨幅温和 (还没加速)
                ORDER BY f.f_ma_cohesion ASC
            """)
            
            df_candidates = pd.read_sql(search_query, conn, params={
                "sectors": target_sectors, 
                "leaders": LEADERS
            })

        # 第三步：输出结果
        if not df_candidates.empty:
            # 因为一只股可能属于多个目标板块，去重显示
            df_res = df_candidates.drop_duplicates(subset=['symbol'])
            
            print("\n" + "💎" * 15)
            print(f"🚀 发现 {len(df_res)} 只【锂电/有色】补涨潜力股：")
            print("-" * 85)
            print(df_res.to_string(index=False))
            print("-" * 85)
            print("💡 补涨操作逻辑：")
            print("1. 龙头【永杉锂业】明天若继续强势连板，资金大概率会攻击这些‘低位粘合’的个股。")
            print("2. 选股：优先选‘均线粘合度’最小（最平）的那只。")
            print("3. 止损：今日阳线实体底部。")
        else:
            print("\n该板块内暂未发现符合‘补涨起爆’形态的个股。")

    except Exception as e:
        print(f"❌ 运行失败: {e}")

if __name__ == "__main__":
    find_catch_up_opportunities()