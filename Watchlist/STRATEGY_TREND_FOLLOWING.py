import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime
import json
import warnings

warnings.filterwarnings('ignore')

# --- 1. 数据库配置 (双库连接) ---
# 行情及因子库
engine_quant = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')
# 策略复盘及股票池
engine_review = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')

def calculate_resonance_score(row):
    """
    科学打分逻辑：
    1. 板块流入权重 (40%): 1-10亿对应 0-40分
    2. 趋势动能权重 (30%): DIF值 0-2 对应 0-30分
    3. 成交爆发权重 (30%): 量能倍数 1-3倍对应 0-30分
    """
    s1 = np.clip(row['板块流入(亿)'] / 10 * 40, 0, 40)
    s2 = np.clip(row['DIF'] / 2 * 30, 0, 30)
    s3 = np.clip((row['量能倍数'] - 1) * 15, 0, 30)
    return int(s1 + s2 + s3)

def save_to_review_pool(df, trade_date):
    """
    将结果 UPSERT 到 trading_review.stock_pools
    """
    if df.empty:
        return

    now = datetime.datetime.now()
    records = []

    for _, row in df.iterrows():
        # 1. 构造核心因子快照 (JSON)
        tags_dict = {
            "strategy": "MACD_BOLL_Resonance",
            "dif": round(float(row['DIF']), 4),
            "vol_ratio": round(float(row['量能倍数']), 2),
            "sector_inflow": round(float(row['板块流入(亿)']), 2)
        }

        records.append({
            'symbol': row['代码'],
            'trade_date': trade_date,
            'stock_name': row['名称'],
            'pool_type': 'long',  # 趋势策略归类为长线/波段池
            'sector_name': row['所属热门板块'],
            'score': calculate_resonance_score(row),
            'status': '趋势确立',
            'tags': json.dumps(tags_dict, ensure_ascii=False),
            'notes': f"所属[{row['所属热门板块']}]板块今日强力吸金{row['板块流入(亿)']}亿，个股收复布林中轨。",
            'created_at': now,
            'updated_at': now
        })

    df_save = pd.DataFrame(records)

    try:
        with engine_review.begin() as conn:
            # 写入临时表
            df_save.to_sql('temp_trend_sync', con=conn, if_exists='replace', index=False)
            
            # 使用复合主键进行 UPSERT
            upsert_sql = text("""
                INSERT INTO stock_pools (symbol, trade_date, stock_name, pool_type, sector_name, score, status, tags, notes, created_at, updated_at)
                SELECT symbol, trade_date, stock_name, pool_type, sector_name, score, status, tags, notes, created_at, updated_at FROM temp_trend_sync
                ON DUPLICATE KEY UPDATE 
                    score = VALUES(score),
                    status = VALUES(status),
                    tags = VALUES(tags),
                    notes = VALUES(notes),
                    updated_at = VALUES(updated_at);
            """)
            conn.execute(upsert_sql)
            conn.execute(text("DROP TABLE IF EXISTS temp_trend_sync;"))
        print(f"✅ 成功同步 {len(df_save)} 只趋势个股到 [中线股票池]")
    except Exception as e:
        print(f"❌ 数据库同步失败: {e}")

def run_trend_resonance_pipeline():
    print(f"[{datetime.datetime.now()}] 启动 V5.0 趋势共振系统...")

    # 1. 获取日期
    with engine_quant.connect() as conn:
        date_res = conn.execute(text("SELECT DISTINCT trade_date FROM stk_factors ORDER BY trade_date DESC LIMIT 2")).fetchall()
        if len(date_res) < 2: return
        today, yesterday = date_res[0][0], date_res[1][0]

    # 2. 执行核心 SQL 筛选
    query_sql = text("""
        SELECT 
            t.symbol as '代码', s.name as '名称', 
            flow.sector_name as '所属热门板块',
            flow.net_inflow_amount as '板块流入(亿)',
            k_t.close as '现价',
            t.f_macd_dif as 'DIF',
            t.f_bb_m as '中轨(20日线)',
            t.f_vol_ratio as '量能倍数'
        FROM stk_factors t
        JOIN stk_factors y ON t.symbol = y.symbol AND y.trade_date = :yesterday
        JOIN stocks s ON t.symbol = s.symbol
        JOIN stk_daily_kline k_t ON t.symbol = k_t.symbol AND k_t.trade_date = :today
        JOIN stk_daily_kline k_y ON t.symbol = k_y.symbol AND k_y.trade_date = :yesterday
        JOIN stock_sector_relation r ON t.symbol = r.symbol
        JOIN stk_sector_fund_flow flow ON (r.sector_name = CONCAT('行业-', flow.sector_name) OR r.sector_name = CONCAT('概念-', flow.sector_name))
        WHERE t.trade_date = :today AND flow.trade_date = :today
          AND flow.net_inflow_amount >= 1.0
          AND (t.symbol LIKE '60%' OR t.symbol LIKE '00%' OR t.symbol LIKE '30%')
          AND s.name NOT LIKE '%%ST%%'
          AND t.f_macd_dif > 0
          AND k_t.close > t.f_bb_m AND k_y.close <= y.f_bb_m
          AND t.f_vol_ratio > 1.1
        GROUP BY t.symbol, s.name, flow.sector_name
        ORDER BY flow.net_inflow_amount DESC
    """)

    try:
        with engine_quant.connect() as conn:
            df_results = pd.read_sql(query_sql, conn, params={"today": today, "yesterday": yesterday})

        if not df_results.empty:
            # 控制台预览
            print("\n" + "🔥" * 8 + " 趋势共振筛选结果 " + "🔥" * 8)
            print(df_results[['代码', '名称', '所属热门板块', '板块流入(亿)', '现价']].to_string(index=False))
            
            # 执行入库
            save_to_review_pool(df_results, today)
        else:
            print(f"今日 ({today}) 暂无符合趋势共振的标的。")

    except Exception as e:
        print(f"❌ 运行失败: {e}")

if __name__ == "__main__":
    run_trend_resonance_pipeline()