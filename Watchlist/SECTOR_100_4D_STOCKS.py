import json
import pandas as pd
from sqlalchemy import create_engine, text
import datetime
import sys
import numpy as np

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)
engine_review = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')

def get_latest_trade_date():
    with engine.connect() as conn:
        res = conn.execute(text("SELECT MAX(trade_date) FROM stk_daily_kline")).scalar()
    return res

# -------------------------
# scoring (100 分制打分模型)
# -------------------------
def calc_4d_score(row):
    score = 0
    # 1. 恒强板块权重分 (20分)
    # sector_max_inflow_rate 现在代表双表共振后的百分制得分
    score += min(row.get("sector_max_inflow_rate", 0) * 0.2, 20)
    
    # 2. 资金规模分 (25分)
    score += (row.get("capital_score", 0) * 0.15)
    score += min(row.get("main_net_ratio", 0), 10)
    
    # 3. 资金攻击分 (30分)
    score += min(row.get("buy_power_ratio", 0) / 100 * 15, 15)
    score += min(row.get("attack_score", 0) / 100 * 15, 15)
    
    # 4. 趋势分 (15分)
    score += min(row.get("f_mom_20", 0) * 100, 10)
    score += max(0, 5 - row.get("f_dist_high", 10))
    
    # 5. 筹码/量价分 (10分)
    score += (row.get("chip_score", 0) / 100 * 5)
    qr = row.get("f_quantity_ratio", 1)
    if 1.5 <= qr <= 5.5: score += 5
    
    return round(score, 2)

def save_to_stock_pool(df_selected, trade_date):
    if df_selected.empty: return
    now = datetime.datetime.now()
    records = []
    for _, row in df_selected.iterrows():
        tags = {
            "strategy": "Double_Sector_Persistence",
            "breadth_days": int(row.get("b_count", 0)),
            "strength_days": int(row.get("s_count", 0)),
            "capital_score": int(row["capital_score"]),
            "profit_ratio": round(float(row["profit_ratio"]), 2),
            "attack_score": int(row.get("attack_score", 0))
        }
        score = row["sort_score"]
        records.append({
            "symbol": row["symbol"],
            "trade_date": trade_date,
            "stock_name": row["stock_name"],
            "pool_type": "short",
            "sector_name": str(row["sector_names"])[:95],
            "score": score,
            "status": "四维共振",
            "tags": json.dumps(tags, ensure_ascii=False),
            "notes": f"宽度入围{row['b_count']}次, 强度入围{row['s_count']}次。资金分{row['capital_score']}, 获利盘{row['profit_ratio']:.1f}%",
            "created_at": now, "updated_at": now,
            "is_watch_focus": 1 if score >= 82 else 0,
            "watch_level": 3 if score >= 86 else (2 if score >= 78 else 1)
        })
        
    df_save = pd.DataFrame(records)
    try:
        with engine_review.begin() as conn:
            df_save.to_sql("tmp_stock_pool", conn, if_exists="replace", index=False)
            upsert_sql = text("""
                INSERT INTO stock_pools (symbol, trade_date, stock_name, pool_type, sector_name, score, status, tags, notes, created_at, updated_at, is_watch_focus, watch_level)
                SELECT symbol, trade_date, stock_name, pool_type, sector_name, score, status, tags, notes, created_at, updated_at, is_watch_focus, watch_level FROM tmp_stock_pool
                ON DUPLICATE KEY UPDATE score = VALUES(score), notes = VALUES(notes), tags = VALUES(tags), updated_at = VALUES(updated_at), is_watch_focus = VALUES(is_watch_focus), watch_level = VALUES(watch_level)
            """)
            conn.execute(upsert_sql)
        print(f"✅ 成功同步 {len(df_save)} 只双重验证主线股票")
    except Exception as e:
        print(f"❌ 入池失败: {e}")

def select_stocks_smart_match():
    today = get_latest_trade_date()
    print(f"🚀 启动四维共振选股 (双表回溯Top15版)，基准日期: {today}\n")

    # 1. 获取日期
    with engine.connect() as conn:
        date_query = text("SELECT DISTINCT trade_date FROM stk_daily_kline ORDER BY trade_date DESC LIMIT 7")
        last_7_dates = [row[0].strftime('%Y-%m-%d') for row in conn.execute(date_query).fetchall()]
    dates_str = ",".join([f"'{d}'" for d in last_7_dates])

    # 2. 统计【宽度表】Top 15 次数
    breadth_sql = f"""
        SELECT sector_name, COUNT(*) as b_count 
        FROM stk_sector_breadths 
        WHERE trade_date IN ({dates_str}) AND rank_pos <= 15 AND sector_type = 'industry'
        GROUP BY sector_name
    """
    df_b = pd.read_sql(text(breadth_sql), engine_review)

    # 3. 统计【强度表】Top 15 次数
    scores_sql = f"""
        SELECT sector_name, COUNT(*) as s_count 
        FROM stk_sector_scores 
        WHERE trade_date IN ({dates_str}) AND rank_pos <= 15
        GROUP BY sector_name
    """
    df_s = pd.read_sql(text(scores_sql), engine_review)

    # 4. 合并双表回溯结果
    df_strong_sectors = pd.merge(df_b, df_s, on='sector_name', how='outer').fillna(0)
    
    # 恒强逻辑：在任意一张表里出现 >= 3 次
    df_strong_sectors = df_strong_sectors[(df_strong_sectors['b_count'] >= 3) | (df_strong_sectors['s_count'] >= 3)]
    
    if df_strong_sectors.empty:
        print("❌ 当前市场混沌，无恒强板块。")
        return

    # 计算板块最终权重分 (100分制)
    # 逻辑：两表出现次数之和 / 14 * 100
    df_strong_sectors['weight_score'] = ((df_strong_sectors['b_count'] + df_strong_sectors['s_count']) / 14 * 100).clip(0, 100)
    
    sector_score_map = dict(zip(df_strong_sectors['sector_name'], df_strong_sectors['weight_score']))
    sector_b_map = dict(zip(df_strong_sectors['sector_name'], df_strong_sectors['b_count']))
    sector_s_map = dict(zip(df_strong_sectors['sector_name'], df_strong_sectors['s_count']))

    clean_sectors = df_strong_sectors['sector_name'].tolist()
    print(f"🔥 识别到恒强共振主线: {', '.join(clean_sectors)}")

    # 5. 匹配个股映射
    db_sectors = pd.read_sql("SELECT DISTINCT sector_name FROM stock_sector_relation", engine)['sector_name'].tolist()
    matched_db_sectors = [db for db in db_sectors if any(clean in db for clean in clean_sectors)]
    
    if not matched_db_sectors: return

    sectors_str = ",".join([f"'{s}'" for s in matched_db_sectors])
    basic_sql = f"""
        SELECT r.symbol, s.name AS stock_name, GROUP_CONCAT(r.sector_name SEPARATOR ' | ') AS sector_names
        FROM stock_sector_relation r JOIN stocks s ON r.symbol = s.symbol
        WHERE r.sector_name IN ({sectors_str}) AND s.name NOT LIKE '%%ST%%' AND s.name NOT LIKE '%%退%%'
        GROUP BY r.symbol, s.name
    """
    df_basic = pd.read_sql(text(basic_sql), engine)
    
    # 注入板块信息
    def get_info(names, info_map):
        vals = [info_map.get(n.strip().replace('行业-',''), 0) for n in str(names).split('|')]
        return max(vals) if vals else 0

    df_basic['sector_max_inflow_rate'] = df_basic['sector_names'].apply(lambda x: get_info(x, sector_score_map))
    df_basic['b_count'] = df_basic['sector_names'].apply(lambda x: get_info(x, sector_b_map))
    df_basic['s_count'] = df_basic['sector_names'].apply(lambda x: get_info(x, sector_s_map))

    # 6. 获取因子、K线、资金、筹码
    syms = ",".join([f"'{s}'" for s in df_basic['symbol'].tolist()])
    df_fac = pd.read_sql(f"SELECT symbol, f_mom_20, f_macd_dif, f_macd_dea, f_macd_hist, f_bb_m, f_quantity_ratio, f_dist_high FROM stk_factors WHERE trade_date='{today}' AND symbol IN ({syms})", engine)
    df_k = pd.read_sql(f"SELECT symbol, open, close, turnover_rate FROM stk_daily_kline WHERE trade_date='{today}' AND symbol IN ({syms})", engine)
    df_fund = pd.read_sql(f"SELECT symbol, main_net_inflow, main_net_ratio, inflow_3d, buy_power_ratio, attack_score, capital_score, volume_power_ratio FROM stk_stock_fund_flow WHERE trade_date='{today}' AND symbol IN ({syms})", engine)
    df_chip = pd.read_sql(f"SELECT symbol, profit_ratio, chip_score FROM stk_chip_factor WHERE trade_date='{today}' AND symbol IN ({syms})", engine)

    # 7. 合并与过滤
    df_all = df_basic.merge(df_fac, on='symbol').merge(df_k, on='symbol').merge(df_fund, on='symbol').merge(df_chip, on='symbol').dropna()
    
    cond_trend = (df_all['f_mom_20'] > 0) & (df_all['f_macd_dif'] > df_all['f_macd_dea']) & (df_all['f_macd_hist'] > 0) & (df_all['close'] > df_all['f_bb_m'])
    cond_fund = (df_all['main_net_inflow'] > 0) & (df_all['inflow_3d'] > 0) & (df_all['capital_score'] >= 70) & (df_all['buy_power_ratio'] >= 55) & (df_all['volume_power_ratio'] >= 1.1)
    cond_chip = (df_all['profit_ratio'] > 60) & (df_all['chip_score'] > 60)
    cond_vol = (df_all['close'] > df_all['open']) & (df_all['close']/df_all['open'] > 1.015) & (df_all['f_quantity_ratio'].between(1.5, 6.0)) & (df_all['turnover_rate'].between(0.015, 0.20))
    cond_atk = (df_all["attack_score"] >= 70) & (df_all["buy_power_ratio"] >= 60)

    df_sel = df_all[cond_fund & cond_trend & cond_chip & cond_vol & cond_atk].copy()

    # 8. 评分与入库
    if not df_sel.empty:
        df_sel['sort_score'] = df_sel.apply(calc_4d_score, axis=1)
        df_sel = df_sel.sort_values('sort_score', ascending=False)
        save_to_stock_pool(df_sel.head(50), today)
        print(df_sel[['symbol', 'stock_name', 'sort_score', 'b_count', 's_count']].head(15).to_string(index=False))
    else:
        print("❌ 今日未发现符合双验证共振条件的个股。")

if __name__ == "__main__":
    select_stocks_smart_match()