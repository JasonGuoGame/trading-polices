import json
import pandas as pd
import pandas_ta as ta
from sqlalchemy import create_engine, text
import datetime
import warnings

warnings.filterwarnings('ignore')

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)
engine_review = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')

def get_latest_trade_date():
    with engine.connect() as conn:
        res = conn.execute(text("SELECT MAX(trade_date) FROM stk_daily_kline")).scalar()
    return res

# ---------------------------------------------------------
# 1. 核心计算函数 (引入中期趋势过滤)
# ---------------------------------------------------------
def apply_vegas_logic(df_group):
    df_group = df_group.sort_values('trade_date')
    if len(df_group) < 200: return None 
    
    # 计算均线系统
    df_group['ema20'] = ta.ema(df_group['close'], length=20)   # 短中期线
    df_group['ema60'] = ta.ema(df_group['close'], length=60)   # 中期分水岭
    df_group['ema144'] = ta.ema(df_group['close'], length=144) # 维加斯通道
    df_group['ema169'] = ta.ema(df_group['close'], length=169)
    
    # 计算 EMA144 的 20 日趋势 (长周期趋势)
    df_group['ema_slope_20'] = df_group['ema144'].diff(20)
    
    last_row = df_group.iloc[-1].copy()
    if pd.isna(last_row['ema144']): return None
    
    c = float(last_row['close'])
    o = float(last_row['open'])
    e20 = float(last_row['ema20'])
    e60 = float(last_row['ema60'])
    e144 = float(last_row['ema144'])
    e169 = float(last_row['ema169'])
    
    # 核心判断：顺向多头排列 (股价 > 20线 > 60线 > 144线)
    is_bull_alignment = (e20 > e60) and (e60 > e144) and (e144 > e169)
    last_row['is_strong_trend'] = 1 if is_bull_alignment else 0
    
    last_row['dist_to_vegas'] = (c - e144) / e144
    last_row['is_red_candle'] = 1 if c > o else 0
    
    return last_row

# ---------------------------------------------------------
# 2. 精准打分模型
# ---------------------------------------------------------
def calc_enhanced_score(row):
    score = 0
    # A. 趋势排列分 (30分)
    if row.get('is_strong_trend') == 1:
        score += 30
    
    # B. 位置贴合分 (40分)
    dist = row['dist_to_vegas']
    if 0 <= dist <= 0.03: score += 40
    elif 0.03 < dist <= 0.08: score += 20
    
    # C. 资金与板块分 (30分)
    s_total = row.get('sector_total_score', 0)
    attack = row.get('attack_score', 0)
    score += (float(s_total) / 100) * 15
    score += (float(attack) / 100) * 15
        
    return round(score, 2)

# ---------------------------------------------------------
# 3. 策略执行主函数
# ---------------------------------------------------------
def select_vegas_tunnel_strategy():
    today = get_latest_trade_date()
    if not today: return
    print(f"🚀 开始执行【真·上升通道】维加斯精选策略，日期: {today}")

    # 1. 获取强势板块
    sector_sql = f"SELECT sector_name, total_score as sector_total_score, is_leader, rank_pos FROM stk_sector_scores WHERE trade_date = '{today}' AND (rank_pos <= 15 OR is_leader = 1)"
    df_sector_ranking = pd.read_sql(text(sector_sql), engine_review)
    if df_sector_ranking.empty: return

    # 2. 获取数据并计算
    start_date = (today - datetime.timedelta(days=450)).strftime('%Y-%m-%d')
    kline_sql = f"SELECT symbol, trade_date, open, close FROM stk_daily_kline WHERE trade_date >= '{start_date}'"
    df_all_k = pd.read_sql(text(kline_sql), engine)
    
    df_vegas = df_all_k.groupby('symbol').apply(apply_vegas_logic).reset_index()

    # 3. 关联板块 (模糊匹配)
    symbols_str = ",".join([f"'{s}'" for s in df_vegas['symbol'].unique()])
    relation_sql = f"SELECT r.symbol, s.name as stock_name, r.sector_name FROM stock_sector_relation r JOIN stocks s ON r.symbol = s.symbol WHERE r.symbol IN ({symbols_str})"
    df_rel = pd.read_sql(text(relation_sql), engine)

    matched_list = []
    for sector_info in df_sector_ranking.to_dict('records'):
        mask = df_rel['sector_name'].str.contains(sector_info['sector_name'], case=False, na=False)
        temp_df = df_rel[mask].copy()
        if not temp_df.empty:
            temp_df['sector_total_score'] = sector_info['sector_total_score']
            temp_df['is_leader'] = sector_info['is_leader']
            matched_list.append(temp_df)
    
    if not matched_list: return
    df_merge = pd.concat(matched_list, ignore_index=True)
    df_final = df_vegas.merge(df_merge, on='symbol', how='inner')
    
    # 4. 关联资金流
    fund_sql = f"SELECT symbol, attack_score FROM stk_stock_fund_flow WHERE trade_date = '{today}'"
    df_fund = pd.read_sql(text(fund_sql), engine)
    df_final = df_final.merge(df_fund, on='symbol', how='left')

    # --- 5. 剔除下降通道的硬性过滤条件 ---
    cond_bull = df_final['is_strong_trend'] == 1       # 1. 顺向多头排列 (股价 > 20 > 60 > 144)
    cond_slope = df_final['ema_slope_20'] > 0          # 2. 144 线上升趋势
    cond_above = df_final['close'] >= df_final['ema144']# 3. 价格在 144 线上方
    cond_dist = df_final['dist_to_vegas'] <= 0.10      # 4. 距离 144 线小于 10%
    cond_red = df_final['is_red_candle'] == 1          # 5. 当天是阳线止跌
    cond_no_st = ~df_final['stock_name'].str.contains("ST|退")

    df_selected = df_final[cond_bull & cond_slope & cond_above & cond_dist & cond_red & cond_no_st].copy()

    if not df_selected.empty:
        df_selected['sort_score'] = df_selected.apply(calc_enhanced_score, axis=1)
        df_selected = df_selected.sort_values(by='sort_score', ascending=False)
        
        print(f"🎯 彻底剔除下降通道后，选中真正多头核心标的: {len(df_selected)} 只")
        save_vegas_to_pool(df_selected.head(20), today) # 入库前20名
        
        print("\n" + "="*90)
        cols = ['symbol', 'stock_name', 'sector_name', 'dist_to_vegas', 'sort_score']
        print(df_selected[cols].head(15).to_string(index=False))
        print("="*90)
    else:
        print("❌ 今日无符合“顺向多头+通道回踩”的个股。")

# ---------------------------------------------------------
# 4. 入库逻辑 (已补全完整 SQL)
# ---------------------------------------------------------
def save_vegas_to_pool(df_selected, trade_date):
    now = datetime.datetime.now()
    records = []
    for _, row in df_selected.iterrows():
        tags = {
            "strategy": "VegasStrictUpTrend",
            "dist": f"{row['dist_to_vegas']:.2%}",
            "sector": row['sector_name']
        }
        records.append({
            "symbol": row["symbol"],
            "trade_date": trade_date,
            "stock_name": row["stock_name"],
            "pool_type": "short",
            "sector_name": str(row["sector_name"])[:60],
            "score": row["sort_score"],
            "status": "四维共振",
            "tags": json.dumps(tags, ensure_ascii=False),
            "notes": f"【多头回踩】板块:{row['sector_name']}, 距离支撑:{row['dist_to_vegas']:.2%}",
            "created_at": now,
            "updated_at": now,
            "is_watch_focus": 1 if row["sort_score"] >= 80 else 0,
            "watch_level": 3 if row["sort_score"] >= 85 else (2 if row["sort_score"] >= 75 else 1)
        })

    df_save = pd.DataFrame(records)
    try:
        with engine_review.begin() as conn:
            # 1. 写入临时表
            df_save.to_sql("tmp_vegas_up", conn, if_exists="replace", index=False)
            
            # 2. 补全完整的 INSERT ... ON DUPLICATE KEY UPDATE 语句
            upsert_sql = text("""
            INSERT INTO stock_pools (
                symbol, trade_date, stock_name, pool_type, sector_name, score, status, tags, notes,
                created_at, updated_at, is_watch_focus, watch_level
            )
            SELECT 
                symbol, trade_date, stock_name, pool_type, sector_name, score, status, tags, notes,
                created_at, updated_at, is_watch_focus, watch_level 
            FROM tmp_vegas_up
            ON DUPLICATE KEY UPDATE
                score = VALUES(score),
                tags = VALUES(tags),
                notes = VALUES(notes),
                updated_at = VALUES(updated_at),
                is_watch_focus = VALUES(is_watch_focus),
                watch_level = VALUES(watch_level)
            """)
            conn.execute(upsert_sql)
            
            # 3. 清理临时表
            conn.execute(text("DROP TABLE IF EXISTS tmp_vegas_up"))
        print(f"✅ 成功同步 {len(df_save)} 只严格上升通道股票到数据库")
    except Exception as e:
        print(f"❌ 入库失败: {e}")

if __name__ == "__main__":
    select_vegas_tunnel_strategy()