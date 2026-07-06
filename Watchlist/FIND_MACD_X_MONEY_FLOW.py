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

# --- 数据库配置 ---
engine_quant = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')
engine_review = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')

def calculate_macd_res_score(row):
    """评分逻辑：板块分数(40) + 量能(30) + MACD红柱(30)"""
    # 🌟 修改：直接使用该个股所属最强板块的评分
    s1 = np.clip((row['最强板块分数'] - 60) / 40 * 30 + 10, 10, 40)
    s2 = np.clip((row['量能倍数'] - 1) * 15, 0, 30)
    s3 = np.clip(row['HIST'] * 100, 5, 30)
    return int(s1 + s2 + s3)

def save_to_review_pool(df, trade_date):
    """将结果存入 trading_review.stock_pools"""
    if df.empty: return
    now = datetime.datetime.now()
    records = []
    for _, row in df.iterrows():
        tags_dict = {
            "strategy": "MACD_Best_Sector_Match",
            "hist": round(float(row['HIST']), 4),
            "vol_ratio": round(float(row['量能倍数']), 2),
            "sector_score": round(float(row['最强板块分数']), 2),
            "sector_rank": int(row['板块排名'])
        }
        records.append({
            'symbol': row['代码'],
            'trade_date': trade_date,
            'stock_name': row['名称'],
            'pool_type': 'short',
            'sector_name': row['最强板块名称'], # 🌟 存入最高分板块名
            'score': int(row['综合评分']),
            'status': '资金共振金叉',
            'tags': json.dumps(tags_dict, ensure_ascii=False),
            'notes': f"所属最强板块[{row['最强板块名称']}]评分{row['最强板块分数']}，排名第{row['板块排名']}。",
            'created_at': now,
            'updated_at': now
        })
    df_save = pd.DataFrame(records)
    with engine_review.begin() as conn:
        df_save.to_sql('temp_macd_pool', con=conn, if_exists='replace', index=False)
        upsert_sql = text("""
            INSERT INTO stock_pools (symbol, trade_date, stock_name, pool_type, sector_name, score, status, tags, notes, created_at, updated_at)
            SELECT symbol, trade_date, stock_name, pool_type, sector_name, score, status, tags, notes, created_at, updated_at FROM temp_macd_pool
            ON DUPLICATE KEY UPDATE 
                score = VALUES(score), 
                sector_name = VALUES(sector_name),
                tags = VALUES(tags), 
                notes = VALUES(notes),
                updated_at = VALUES(updated_at);
        """)
        conn.execute(upsert_sql)
        conn.execute(text("DROP TABLE IF EXISTS temp_macd_pool;"))

def run_macd_resonance_pipeline():
    print(f"[{datetime.datetime.now()}] 🚀 启动【最强归属板块】MACD金叉探测系统...")

    # 1. 获取日期
    with engine_quant.connect() as conn:
        date_res = conn.execute(text("SELECT MAX(trade_date) FROM stk_factors")).fetchone()
        today = date_res[0]
        yest_res = conn.execute(text("SELECT DISTINCT trade_date FROM stk_factors WHERE trade_date < :t ORDER BY trade_date DESC LIMIT 1"), {"t": today}).fetchone()
        yesterday = yest_res[0]

    # 2. 核心 SQL 升级：
    # 使用窗口函数 ROW_NUMBER() 对个股所属的所有板块按分数进行排名
    query_sql = text("""
        WITH StockBestSector AS (
            SELECT 
                r.symbol,
                ss.sector_name,
                ss.total_score,
                ss.rank_pos,
                ROW_NUMBER() OVER(PARTITION BY r.symbol ORDER BY ss.total_score DESC) as score_rank
            FROM stock_sector_relation r
            JOIN trading_review.stk_sector_scores ss ON (
                r.sector_name = ss.sector_name 
                OR r.sector_name = CONCAT('行业-', ss.sector_name) 
                OR r.sector_name = CONCAT('概念-', ss.sector_name)
            )
            WHERE ss.trade_date = :today
        )
        SELECT 
            t.symbol as '代码', 
            s.name as '名称', 
            bs.sector_name as '最强板块名称',
            bs.total_score as '最强板块分数',
            bs.rank_pos as '板块排名',
            t.f_macd_dif as DIF, 
            t.f_macd_hist as HIST,
            f.f_vol_ratio as '量能倍数'
        FROM stk_factors t
        JOIN stk_factors y ON t.symbol = y.symbol AND y.trade_date = :yesterday
        JOIN stocks s ON t.symbol = s.symbol
        -- 🌟 核心关联：只取每个股票评分最高的那个板块数据
        JOIN StockBestSector bs ON t.symbol = bs.symbol AND bs.score_rank = 1
        LEFT JOIN stk_factors f ON t.symbol = f.symbol AND f.trade_date = t.trade_date
        WHERE t.trade_date = :today
          AND (t.symbol LIKE '60%' OR t.symbol LIKE '00%' OR t.symbol LIKE '30%')
          AND s.name NOT LIKE '%%ST%%'
          AND t.f_macd_dif > t.f_macd_dea                       
          AND y.f_macd_dif <= y.f_macd_dea                     
          AND t.f_macd_dif > 0                                 
        ORDER BY bs.total_score DESC;
    """)

    try:
        with engine_quant.connect() as conn:
            df_results = pd.read_sql(query_sql, conn, params={"today": today, "yesterday": yesterday})

        if not df_results.empty:
            # 3. 计算综合评分
            df_results['综合评分'] = df_results.apply(calculate_macd_res_score, axis=1)
            df_results = df_results.sort_values('综合评分', ascending=False)

            # --- 输出报告部分 ---
            print("\n" + "🏆" * 12 + " 最强板块归属 + MACD金叉报告 " + "🏆" * 12)
            display_cols = ['代码', '名称', '最强板块名称', '最强板块分数', '板块排名', '综合评分', '量能倍数']
            print(df_results[display_cols].head(20).to_string(index=False))
            print("-" * 120)
            
            # 4. 存入数据库
            save_to_review_pool(df_results, today)
            print(f"✅ 成功提取 {len(df_results)} 只个股并关联其最强归属板块。")
        else:
            print(f"\n今日 ({today}) 暂未发现符合条件的金叉个股。")

    except Exception as e:
        print(f"❌ 运行失败: {e}")

if __name__ == "__main__":
    run_macd_resonance_pipeline()