import pandas as pd
from sqlalchemy import create_engine, text
import datetime
import os
import warnings
import sys
import numpy as np

warnings.filterwarnings('ignore')

# --- 引入全局配置 ---
sys.path.append(r"C:\ws\trading-polices\config")
import config  

# --- 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def find_real_money_movements_to_db():
    print(f"[{datetime.datetime.now()}] 🚀 启动‘倍量级’进攻与撤退双向探测器...")

    try:
        with engine.connect() as conn:
            # 1. 日期获取
            date_res = conn.execute(text("SELECT MAX(trade_date) FROM stk_daily_kline")).fetchone()
            today = date_res[0]
            yest_res = conn.execute(text("SELECT DISTINCT trade_date FROM stk_daily_kline WHERE trade_date < :t ORDER BY trade_date DESC LIMIT 1"), {"t": today}).fetchone()
            yesterday = yest_res[0]

            # 2. 获取标准板块名单
            sector_list_sql = text("SELECT DISTINCT sector_name FROM stk_sector_fund_flow WHERE trade_date = :d")
            official_sectors = [r[0] for r in conn.execute(sector_list_sql, {"d": today}).fetchall()]
            
            if not official_sectors:
                print(f"❌ 今日 ({today}) 尚未生成资金流数据。")
                return
            
            # 3. SQL：提取 12 亿 + 倍量个股 (放开限制，包含所有动作)
            blacklist_filter = " AND ".join([f"r.sector_name NOT LIKE '%%{k}%%'" for k in config.SECTOR_BLACKLIST])

            query = text(f"""
                SELECT 
                    r.sector_name as 'sector_raw',
                    k_t.symbol, s.name, 
                    k_t.amount as 'today_amt_raw',
                    k_y.amount as 'yest_amt_raw',
                    k_t.close as 'close_t',
                    k_t.high as 'high_t',
                    k_t.low as 'low_t',
                    k_y.close as 'close_y'
                FROM stk_daily_kline k_t
                JOIN stocks s ON k_t.symbol = s.symbol
                JOIN stock_sector_relation r ON k_t.symbol = r.symbol
                JOIN stk_daily_kline k_y ON k_t.symbol = k_y.symbol AND k_y.trade_date = :yest
                WHERE k_t.trade_date = :today
                  AND k_t.amount >= 1200000000           -- 12 亿门槛
                  AND k_t.amount >= (k_y.amount * 2)     -- 倍量放量
                  AND s.name NOT LIKE '%%ST%%'
                  AND ({blacklist_filter})
            """)
            df = pd.read_sql(query, conn, params={"today": today, "yest": yesterday})

        if df.empty:
            print(f"今日未发现符合‘倍量’条件的活跃个股。")
            return

        # 4. 名称对齐与去重
        def align_to_official(raw_name):
            clean = raw_name.replace('行业-', '').replace('概念-', '').replace('Ⅱ', '').replace('Ⅲ', '').strip()
            if clean in official_sectors: return clean
            for off_n in official_sectors:
                if off_n in clean: return off_n
            return None

        df['sector_name'] = df['sector_raw'].apply(align_to_official)
        df = df.dropna(subset=['sector_name']).copy()
        # 🌟 解决主键冲突：针对 (股票+标准板块) 去重
        df = df.drop_duplicates(subset=['symbol', 'sector_name']).copy()

        # 5. 核心量化判定 (动作分型)
        df['pct_chg'] = ((df['close_t'] - df['close_y']) / df['close_y'] * 100).round(2)
        df['close_pos'] = ((df['close_t'] - df['low_t']) / (df['high_t'] - df['low_t'] + 0.001)).round(2)
        
        df['action_type'] = '中性'
        # 进攻：大涨且收在高位
        df.loc[(df['pct_chg'] > 2.0) & (df['close_pos'] > 0.7), 'action_type'] = '进攻'
        # 撤退：大跌，或者出现了极长的上影线（冲高回落，收盘在低位）
        df.loc[(df['pct_chg'] < -2.0) | (df['close_pos'] < 0.3), 'action_type'] = '撤退'
        
        # 只保留有明确动作的（过滤掉中性，减少数据噪音）
        df = df[df['action_type'] != '中性'].copy()

        if df.empty:
            print(f"今日虽有倍量，但动作均表现平平（中性），无须记录。")
            return

        # 6. 数据整理
        df['amount_today'] = (df['today_amt_raw'] / 1e8).round(2)
        df['amount_yesterday'] = (df['yest_amt_raw'] / 1e8).round(2)
        df['trade_date'] = today
        df['last_update'] = datetime.datetime.now()

        # 7. 板块聚合
        # 统计板块内总异动数
        sector_stats = df.groupby('sector_name').agg({'symbol': 'nunique'}).rename(columns={'symbol': 'sector_new_count'}).reset_index()
        # 统计板块内总异动金额
        sector_amt = df.groupby('sector_name')['amount_today'].sum().reset_index().rename(columns={'amount_today': 'sector_new_amount'})
        
        df_final = pd.merge(df, sector_stats, on='sector_name')
        df_final = pd.merge(df_final, sector_amt, on='sector_name')

        # 8. 写入数据库
        db_cols = [
            'trade_date', 'symbol', 'name', 'sector_name', 
            'amount_today', 'amount_yesterday', 'pct_chg', 
            'sector_new_count', 'sector_new_amount', 'last_update',
            'close_pos', 'action_type'
        ]
        
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM stk_market_attack_log WHERE trade_date = :d"), {"d": today})
            df_final[db_cols].to_sql('stk_market_attack_log', con=conn, if_exists='append', index=False)

        # 9. 分类复盘报告
        print("\n" + "⚖️" * 10 + f" 倍量个股：阵营分布 ({today}) " + "⚖️" * 10)
        
        summary = df_final.groupby(['action_type', 'sector_name']).size().unstack(level=0, fill_value=0)
        
        if '进攻' in summary.columns:
            print("\n🔥 [进攻阵营] 强力买入的题材:")
            print(summary['进攻'].sort_values(ascending=False).head(5))
            
        if '撤退' in summary.columns:
            print("\n💀 [撤退阵营] 巨量派发的题材:")
            print(summary['撤退'].sort_values(ascending=False).head(5))

    except Exception as e:
        print(f"❌ 运行失败: {e}")

if __name__ == "__main__":
    find_real_money_movements_to_db()