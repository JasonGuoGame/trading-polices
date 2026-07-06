import pandas as pd
from sqlalchemy import create_engine, text
import datetime
import json
import warnings
import numpy as np

warnings.filterwarnings('ignore')

# --- 数据库配置 ---
engine_quant = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')
engine_review = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')

def get_new_face_sectors_with_meta():
    """
    返回符合条件的板块及其变动元数据
    """
    print(f"[{datetime.datetime.now()}] 🔎 正在扫描‘绝对新面孔’黑马板块...")
    
    def build_query(table_name, extra_cond=""):
        return f"""
        SELECT sector_name, today_rank, best_past_rank FROM (
            SELECT 
                sector_name,
                MAX(CASE WHEN date_idx = 1 THEN rank_pos END) as today_rank,
                MIN(CASE WHEN date_idx BETWEEN 2 AND 6 THEN rank_pos END) as best_past_rank
            FROM (
                SELECT sector_name, rank_pos, trade_date,
                       DENSE_RANK() OVER (ORDER BY trade_date DESC) as date_idx
                FROM {table_name}
                WHERE 1=1 {extra_cond}
            ) t WHERE date_idx <= 6
            GROUP BY sector_name
        ) final 
        WHERE today_rank <= 10 AND (best_past_rank > 30 OR best_past_rank IS NULL)
        """

    with engine_review.connect() as conn:
        df1 = pd.read_sql(text(build_query("trading_review.stk_sector_scores")), conn)
        df2 = pd.read_sql(text(build_query("trading_review.stk_sector_breadths", "AND sector_type = 'industry'")), conn)

    df_combined = pd.concat([df1, df2]).drop_duplicates(subset=['sector_name'])
    return df_combined

def find_sector_backbone(target_sector, today_str):
    query_sql = text("""
        SELECT 
            k.symbol, s.name, k.amount as '成交额', k.close,
            (k.close / k.open - 1) * 100 as '当日涨幅',
            f.capital_score as '资金分',
            f.main_net_inflow as '主力流入'
        FROM stk_daily_kline k
        JOIN stock_sector_relation r ON k.symbol = r.symbol
        JOIN stocks s ON k.symbol = s.symbol
        LEFT JOIN stk_stock_fund_flow f ON k.symbol = f.symbol AND f.trade_date = k.trade_date
        WHERE k.trade_date = :t
          AND (r.sector_name = :sec OR r.sector_name = CONCAT('行业-', :sec) OR r.sector_name = CONCAT('概念-', :sec))
          AND s.name NOT LIKE '%%ST%%' AND s.name NOT LIKE '%%退%%'
    """)

    df = pd.read_sql(query_sql, engine_quant, params={"t": today_str, "sec": target_sector})
    if df.empty: return None

    df = df.drop_duplicates(subset=['symbol'])

    df['amt_score'] = (df['成交额'] / df['成交额'].max()) * 60
    df['cap_score'] = (df['资金分'].fillna(0) / 100) * 30
    df['stable_score'] = df['当日涨幅'].apply(lambda x: 10 if 2 <= x <= 7 else (5 if x > 0 else 0))
    df['backbone_score'] = df['amt_score'] + df['cap_score'] + df['stable_score']
    
    return df.sort_values('backbone_score', ascending=False).head(3)

def save_to_long_pool(df_stocks, sector_meta, today):
    """
    科学填充数据并存入 trading_review.stock_pools
    """
    if df_stocks is None or df_stocks.empty: return

    records = []
    now = datetime.datetime.now()

    for _, row in df_stocks.iterrows():
        # 1. 构建科学的 JSON Tags
        tags_dict = {
            "backbone_score": round(float(row['backbone_score']), 2),
            "amount_eb": round(float(row['成交额'] / 1e8), 2),
            "capital_score": int(row['资金分']) if not pd.isna(row['资金分']) else 0,
            "today_pct": round(float(row['当日涨幅']), 2),
            "sector_jump": f"{sector_meta['best_past_rank']} -> {sector_meta['today_rank']}"
        }

        # 2. 构建 Notes
        note_str = (f"板块[{sector_meta['sector_name']}]突围中军。该板块过去5天均在30名外，今日冲入第{int(sector_meta['today_rank'])}名。 "
                    f"个股今日成交{tags_dict['amount_eb']}亿，资金评分{tags_dict['capital_score']}，处于健康进攻态势。")

        # 3. 准备入库记录
        records.append({
            'symbol': row['symbol'],
            'trade_date': today,
            'stock_name': row['name'],
            'pool_type': 'long',
            'sector_name': sector_meta['sector_name'],
            'score': int(row['backbone_score']),
            'status': '长线牛',
            'tags': json.dumps(tags_dict, ensure_ascii=False),
            'notes': note_str,
            'created_at': now,
            'updated_at': now,
            'is_watch_focus': 1 if row['backbone_score'] > 80 else 0,
            'watch_level': 3 if row['backbone_score'] > 85 else (2 if row['backbone_score'] > 75 else 1)
        })

    # 4. 执行 UPSERT 写入
    df_save = pd.DataFrame(records)
    with engine_review.begin() as conn:
        df_save.to_sql('tmp_backbone_sync', conn, if_exists='replace', index=False)
        upsert_sql = text("""
            INSERT INTO stock_pools (
                symbol, trade_date, stock_name, pool_type, sector_name, score, status, tags, notes, 
                created_at, updated_at, is_watch_focus, watch_level
            )
            SELECT 
                symbol, trade_date, stock_name, pool_type, sector_name, score, status, tags, notes, 
                created_at, updated_at, is_watch_focus, watch_level 
            FROM tmp_backbone_sync
            ON DUPLICATE KEY UPDATE 
                score = VALUES(score),
                tags = VALUES(tags),
                notes = VALUES(notes),
                updated_at = VALUES(updated_at),
                is_watch_focus = VALUES(is_watch_focus),
                watch_level = VALUES(watch_level);
        """)
        conn.execute(upsert_sql)
        conn.execute(text("DROP TABLE IF EXISTS tmp_backbone_sync"))

def run_strategy_pipeline():
    # 1. 抓取严格的黑马板块元数据
    df_sectors = get_new_face_sectors_with_meta()
    
    if df_sectors.empty:
        print("🕒 今日暂未发现符合条件的‘冰点突围’黑马板块。")
        return

    # 2. 获取最新日期
    with engine_quant.connect() as conn:
        today = conn.execute(text("SELECT MAX(trade_date) FROM stk_daily_kline")).scalar()

    print(f"🔥 发现 {len(df_sectors)} 个‘冰点突围’板块，准备提取中军...")

    # 3. 循环板块找中军并入库
    for _, s_row in df_sectors.iterrows():
        sec_name = s_row['sector_name']
        print(f"--- 正在处理板块: {sec_name} ---")
        
        top_stocks = find_sector_backbone(sec_name, today)
        
        if top_stocks is not None:
            # 入库
            save_to_long_pool(top_stocks, s_row, today)
            print(f"✅ 板块 [{sec_name}] 的前 {len(top_stocks)} 只中军股已同步至‘长线牛’池。")
        else:
            print(f"⚠️ 板块 [{sec_name}] 未能定位到合适中军。")

if __name__ == "__main__":
    run_strategy_pipeline()