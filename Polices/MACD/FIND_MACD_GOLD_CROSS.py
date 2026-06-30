import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime
import json
import warnings
import sys

warnings.filterwarnings('ignore')

# --- 引入全局配置 ---
sys.path.append(r"C:\ws\trading-polices\config")
import config  

# --- 1. 数据库配置 ---
engine_quant = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')
engine_review = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')

def clean_and_pick_two_sectors(sector_str):
    if not sector_str: return "其他"
    raw_list = sector_str.split(',')
    filtered = [s.replace('行业-', '').replace('概念-', '') for s in raw_list 
                if not any(noise in s for noise in config.SECTOR_BLACKLIST)]
    return " / ".join(filtered[:2]) if filtered else "其他题材"

def calculate_macd_res_score(row):
    """评分逻辑保持不变"""
    s1 = np.clip((row['板块流入(亿)'] - 2) * 5 + 10, 10, 40)
    s2 = np.clip((row['量能倍数'] - 1) * 15, 0, 30)
    s3 = np.clip(row['HIST'] * 100, 5, 30)
    return int(s1 + s2 + s3)

def save_to_review_pool(df, trade_date):
    if df.empty: return
    now = datetime.datetime.now()
    records = []
    for _, row in df.iterrows():
        tags_dict = {
            "strategy": "MACD_Volume_Control",
            "hist": round(float(row['HIST']), 4),
            "vol_ratio": round(float(row['量能倍数']), 2),
            "amount_growth": round(float(row['今日成交额']/row['昨日成交额']), 2),
            "trigger_sector": row['main_sector'],       # 触发选股的原始板块
            "sector_inflow_eb": round(float(row['板块流入(亿)']), 2) # 板块流入金额(亿)
        }
        records.append({
            'symbol': row['代码'], 
            'trade_date': trade_date, 
            'stock_name': row['名称'],
            'pool_type': 'short', 
            'sector_name': row['所属板块'], # 这里是脱水后的双板块，如 "证券 / 互联网金融"
            'score': int(row['综合评分']),
            'status': '资金共振金叉', 
            'tags': json.dumps(tags_dict, ensure_ascii=False),
            # 🌟 同时也更新了 notes 的描述，更直观
            'notes': f"属于[{row['main_sector']}]板块(流入{row['板块流入(亿)']}亿)，MACD金叉且量控良好。",
            'created_at': now, 
            'updated_at': now
        })
    df_save = pd.DataFrame(records)
    with engine_review.begin() as conn:
        df_save.to_sql('temp_macd_pool', con=conn, if_exists='replace', index=False)
        upsert_sql = text("""
            INSERT INTO stock_pools (symbol, trade_date, stock_name, pool_type, sector_name, score, status, tags, notes, created_at, updated_at)
            SELECT symbol, trade_date, stock_name, pool_type, sector_name, score, status, tags, notes, created_at, updated_at FROM temp_macd_pool
            ON DUPLICATE KEY UPDATE score = VALUES(score), sector_name = VALUES(sector_name), tags = VALUES(tags), updated_at = VALUES(updated_at);
        """)
        conn.execute(upsert_sql)
        conn.execute(text("DROP TABLE IF EXISTS temp_macd_pool;"))

def run_macd_resonance_pipeline():
    print(f"[{datetime.datetime.now()}] 🚀 启动【量控+MACD金叉】探测系统...")

    # 1. 获取日期
    with engine_quant.connect() as conn:
        date_res = conn.execute(text("SELECT MAX(trade_date) FROM stk_factors")).fetchone()
        today = date_res[0]
        yest_res = conn.execute(text("SELECT DISTINCT trade_date FROM stk_factors WHERE trade_date < :t ORDER BY trade_date DESC LIMIT 1"), {"t": today}).fetchone()
        yesterday = yest_res[0]

    # 2. 核心 SQL 增加 stk_daily_kline 关联
    query_sql = text("""
        SELECT 
            t.symbol as '代码', 
            s.name as '名称', 
            flow.sector_name as 'main_sector',
            flow.net_inflow_amount as '板块流入(亿)',
            t.f_macd_dif as DIF, 
            t.f_macd_hist as HIST,
            f.f_vol_ratio as '量能倍数',
            k_t.amount as '今日成交额',
            k_y.amount as '昨日成交额',
            (SELECT GROUP_CONCAT(sector_name) FROM stock_sector_relation WHERE symbol = t.symbol) as all_sectors
        FROM stk_factors t
        JOIN stk_factors y ON t.symbol = y.symbol AND y.trade_date = :yesterday
        JOIN stocks s ON t.symbol = s.symbol
        JOIN stock_sector_relation r ON t.symbol = r.symbol
        JOIN stk_sector_fund_flow flow ON (r.sector_name = CONCAT('行业-', flow.sector_name) OR r.sector_name = CONCAT('概念-', flow.sector_name))
        LEFT JOIN stk_factors f ON t.symbol = f.symbol AND f.trade_date = t.trade_date
        -- 🌟 核心修改 A：关联日线表获取今日和昨日的成交额
        JOIN stk_daily_kline k_t ON t.symbol = k_t.symbol AND k_t.trade_date = :today
        JOIN stk_daily_kline k_y ON t.symbol = k_y.symbol AND k_y.trade_date = :yesterday
        WHERE t.trade_date = :today AND flow.trade_date = :today
          AND flow.net_inflow_amount > 2.0                    
          AND (t.symbol LIKE '60%' OR t.symbol LIKE '00%' OR t.symbol LIKE '30%')
          AND s.name NOT LIKE '%%ST%%'
          AND t.f_macd_dif > t.f_macd_dea                       
          AND y.f_macd_dif <= y.f_macd_dea                     
          AND t.f_macd_dif > 0         
          -- 🌟 核心修改 B：过滤成交额倍数（今日 < 昨天 * 1.1）
          AND k_t.amount < (k_y.amount * 1.2)
        GROUP BY t.symbol, s.name, flow.sector_name            
        ORDER BY flow.net_inflow_amount DESC;
    """)

    try:
        with engine_quant.connect() as conn:
            df_results = pd.read_sql(query_sql, conn, params={"today": today, "yesterday": yesterday})

        if not df_results.empty:
            df_results['综合评分'] = df_results.apply(calculate_macd_res_score, axis=1)
            df_results['所属板块'] = df_results['all_sectors'].apply(clean_and_pick_two_sectors)
            df_results = df_results.sort_values('综合评分', ascending=False)

            print("\n" + "📊" * 12 + " 量控 + MACD金叉共振报告 " + "📊" * 12)
            display_cols = ['代码', '名称', '所属板块', '综合评分', '板块流入(亿)', '量能倍数', '今日成交额']
            print(df_results[display_cols].to_string(index=False))
            print("-" * 120)
            print(f"💡 策略逻辑：MACD水上金叉 + 板块资金强流入 + 排除爆量股(今日金额 < 昨天的1.1倍)。")
            
            save_to_review_pool(df_results, today)
        else:
            print(f"\n今日 ({today}) 暂未发现符合条件的个股。")

    except Exception as e:
        print(f"❌ 运行失败: {e}")

if __name__ == "__main__":
    run_macd_resonance_pipeline()