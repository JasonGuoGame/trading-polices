import pandas as pd
from sqlalchemy import create_engine, text
import datetime
import os
import warnings
import sys

warnings.filterwarnings('ignore')

# --- 引入全局配置 ---
# 这里使用简化的路径添加方式
sys.path.append(r"C:\ws\trading-polices\config")
import config  # 导入你的全局配置文件

# --- 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def find_real_money_attack_to_db():
    print(f"[{datetime.datetime.now()}] 启动‘进攻 vs 撤退’双向题材探测...")

    try:
        with engine.connect() as conn:
            # A. 日期获取
            date_res = conn.execute(text("SELECT MAX(trade_date) FROM stk_daily_kline")).fetchone()
            today = date_res[0]
            yest_res = conn.execute(text("SELECT DISTINCT trade_date FROM stk_daily_kline WHERE trade_date < :t ORDER BY trade_date DESC LIMIT 1"), {"t": today}).fetchone()
            yesterday = yest_res[0]

            # B. 核心 SQL：包含 high_t, low_t, close_y 用于计算动作性质
            # noise_filter_sql = " AND " + " AND ".join([f"r.sector_name NOT LIKE '{k}'" for k in NOISE_KEYWORDS])
            # 核心改动：在 {k} 的前后手动加上 %%
            noise_filter_sql = " AND " + " AND ".join([f"r.sector_name NOT LIKE '%%{k}%%'" for k in config.SECTOR_BLACKLIST])

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
                LEFT JOIN stk_daily_kline k_y ON k_t.symbol = k_y.symbol AND k_y.trade_date = :yest
                WHERE k_t.trade_date = :today
                  AND k_t.amount >= 2000000000
                  AND (k_y.amount < 2000000000 OR k_y.amount IS NULL)
                  AND (k_t.symbol LIKE '60%' OR k_t.symbol LIKE '00%' OR k_t.symbol LIKE '30%')
                  AND s.name NOT LIKE '%%ST%%'
                  AND (r.sector_name LIKE '行业-%%' OR r.sector_name LIKE '概念-%%')
                  {noise_filter_sql}
            """)
            df = pd.read_sql(query, conn, params={"today": today, "yest": yesterday})

        if df.empty:
            print(f"今日 ({today}) 未发现新晋 20 亿异动个股。")
            return

        # 2. 核心量化计算
        # 对齐软件涨幅 (对比昨收)
        df['pct_chg'] = ((df['close_t'] - df['close_y']) / df['close_y'] * 100).round(2)
        # 计算收盘位置：(收盘-最低)/(最高-最低)
        df['close_pos'] = ((df['close_t'] - df['low_t']) / (df['high_t'] - df['low_t'] + 0.01)).round(2)
        
        # 判定动作类型
        # 进攻：涨幅 > 2% 且 收在全天 60% 以上位置
        # 撤退：涨幅 < -2% 或 收在全天 30% 以下位置（长上影或大阴线）
        df['action_type'] = '中性'
        df.loc[(df['pct_chg'] > 2.0) & (df['close_pos'] > 0.6), 'action_type'] = '进攻'
        df.loc[(df['pct_chg'] < -2.0) | (df['close_pos'] < 0.3), 'action_type'] = '撤退'

        df['amount_today'] = (df['today_amt_raw'] / 1e8).round(2)
        df['amount_yesterday'] = (df['yest_amt_raw'].fillna(0) / 1e8).round(2)
        df['trade_date'] = today
        df['last_update'] = datetime.datetime.now()
        df['sector_name'] = df['sector_raw'].str.replace('行业-', '').str.replace('概念-', '')

        # 3. 按板块聚合汇总 (统计该板块内有多少进攻，多少撤退)
        sector_stats = df.groupby('sector_name').agg({
            'symbol': 'nunique'
        }).rename(columns={'symbol': 'sector_new_count'}).reset_index()

        # 计算板块总金额 (仅针对新晋20亿的票)
        sector_amt = df.groupby('sector_name')['amount_today'].sum().reset_index().rename(columns={'amount_today': 'sector_new_amount'})
        
        # 合并回主表
        df_final = pd.merge(df, sector_stats, on='sector_name')
        df_final = pd.merge(df_final, sector_amt, on='sector_name')

        # 4. 写入数据库
        # 字段列表需严格对应 DDL
        db_cols = [
            'trade_date', 'symbol', 'name', 'sector_name', 
            'amount_today', 'amount_yesterday', 'pct_chg', 
            'sector_new_count', 'sector_new_amount', 'last_update',
            'close_pos', 'action_type' # 匹配新加的列
        ]
        
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM stk_market_attack_log WHERE trade_date = :d"), {"d": today})
            df_final[db_cols].to_sql('stk_market_attack_log', con=conn, if_exists='append', index=False)

        # 5. 输出分类复盘
        print("\n" + "⚔️" * 10 + f" 题材进攻 vs 撤退看板 ({today}) " + "⚔️" * 10)
        
        # 统计各题材动作
        summary = df_final.groupby(['sector_name', 'action_type']).size().unstack(fill_value=0)
        if '进攻' in summary.columns:
            print("\n🔥 正在【强力进攻】的题材:")
            print(summary.sort_values('进攻', ascending=False).head(5))
        
        if '撤退' in summary.columns:
            print("\n💀 资金【正在撤退】的题材:")
            print(summary.sort_values('撤退', ascending=False).head(5))

    except Exception as e:
        print(f"❌ 运行失败: {e}")

if __name__ == "__main__":
    find_real_money_attack_to_db()