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
# 1. 核心计算函数：EMA5-20-60 趋势系统
# ---------------------------------------------------------
def apply_ema_strategy_logic(df_group):
    df_group = df_group.sort_values('trade_date')
    if len(df_group) < 60: return None
    
    # 计算 EMA 系统
    df_group['ema5'] = ta.ema(df_group['close'], length=5)
    df_group['ema20'] = ta.ema(df_group['close'], length=20)
    df_group['ema60'] = ta.ema(df_group['close'], length=60)
    
    # 计算 EMA20 和 EMA60 的斜率 (当前值 - 3天前值)
    df_group['ema20_slope'] = df_group['ema20'].diff(3)
    df_group['ema60_slope'] = df_group['ema60'].diff(3)
    
    # 获取最后一行
    last_row = df_group.iloc[-1].copy()
    prev_row = df_group.iloc[-2].copy()
    
    c, o, v = float(last_row['close']), float(last_row['open']), float(last_row['volume'])
    e5, e20, e60 = float(last_row['ema5']), float(last_row['ema20']), float(last_row['ema60'])
    
    # --- 策略核心条件判断 ---
    
    # 条件1：趋势多头 (EMA20 > EMA60 且 EMA60 向上)
    cond_trend = (e20 > e60) and (last_row['ema60_slope'] > 0)
    
    # 条件2：买点二 - 回踩 EMA20 (最低价接近或触及 EMA20，但收盘价守住)
    # 允许收盘价在 EMA20 上方 2% 偏移以内
    is_pullback = (last_row['low'] <= e20 * 1.01) and (c >= e20 * 0.99)
    
    # 条件3：阳线确认 (止跌回升)
    is_red_candle = c > o
    
    # 条件4：价格位置
    is_price_ok = c > e20
    
    last_row['tech_match'] = 1 if (cond_trend and is_pullback and is_red_candle and is_price_ok) else 0
    last_row['dist_to_ema20'] = (c - e20) / e20 # 偏离度
    
    return last_row

# ---------------------------------------------------------
# 2. 策略执行主函数
# ---------------------------------------------------------
def select_mainline_ema_strategy():
    today = get_latest_trade_date()
    if not today: return
    print(f"🚀 开始执行【EMA5-20-60主线回踩】精选策略，日期: {today}")

    # --- 第一关：主线过滤 (Sector Score + is_leader) ---
    sector_sql = f"""
    SELECT sector_name, total_score as sector_total_score, is_leader 
    FROM stk_sector_scores 
    WHERE trade_date = '{today}' AND is_leader = 1
    """
    df_sectors = pd.read_sql(text(sector_sql), engine_review)
    if df_sectors.empty:
        print("⚠️ 今日无领头羊主线板块，策略审慎运行...")
        return

    # --- 第二关：提取 K 线并计算趋势 ---
    start_date = (today - datetime.timedelta(days=150)).strftime('%Y-%m-%d')
    kline_sql = f"SELECT symbol, trade_date, open, high, low, close, volume FROM stk_daily_kline WHERE trade_date >= '{start_date}'"
    df_all_k = pd.read_sql(text(kline_sql), engine)
    
    print("   计算 EMA 趋势模型...")
    df_tech = df_all_k.groupby('symbol').apply(apply_ema_strategy_logic).reset_index()
    df_tech = df_tech[df_tech['tech_match'] == 1] # 初步筛选技术达标

    # --- 第三、四关：关联主线关系 & 资金面 & 中军分 ---
    symbols_str = ",".join([f"'{s}'" for s in df_tech['symbol'].unique()])
    
    # 关联板块关系和中军分 (假设中军分在 stk_backbone_scores 表)
    # 如果你没有独立的表，可以用市值或主力资金评分代替
    relation_sql = f"""
    SELECT r.symbol, s.name as stock_name, r.sector_name
    FROM stock_sector_relation r
    JOIN stocks s ON r.symbol = s.symbol
    WHERE r.symbol IN ({symbols_str})
    """
    df_rel = pd.read_sql(text(relation_sql), engine)
    
    # 关联资金评分 (Capital Score)
    fund_sql = f"""
    SELECT symbol, capital_score, attack_score 
    FROM stk_stock_fund_flow 
    WHERE trade_date = '{today}' AND capital_score >= 70
    """
    df_fund = pd.read_sql(text(fund_sql), engine)

    # 数据合并
    df_merge = df_tech.merge(df_rel, on='symbol', how='inner')
    df_merge = df_merge.merge(df_sectors, on='sector_name', how='inner') # 强制关联主线板块
    df_merge = df_merge.merge(df_fund, on='symbol', how='inner') # 强制关联强资金

    # --- 最终打分优化 ---
    def calc_final_score(row):
        score = 0
        # 技术位置分 (40分)：越贴近 EMA20 分越高
        dist = abs(row['dist_to_ema20'])
        score += max(0, 40 - dist * 500)
        
        # 资金强度分 (30分)
        score += (row['capital_score'] / 100) * 30
        
        # 板块分 (30分)
        score += (row['sector_total_score'] / 100) * 30
        
        return round(score, 2)

    if not df_merge.empty:
        df_merge['sort_score'] = df_merge.apply(calc_final_score, axis=1)
        df_selected = df_merge.sort_values('sort_score', ascending=False)
        
        print(f"🎯 选股完成！主线主升浪回踩标的: {len(df_selected)} 只")
        save_to_pool(df_selected.head(15), today)
        
        # 打印结果
        cols = ['symbol', 'stock_name', 'sector_name', 'dist_to_ema20', 'capital_score', 'sort_score']
        print("\n" + "="*100)
        print(df_selected[cols].to_string(index=False))
        print("="*100)
    else:
        print("❌ 今日主线中未发现符合 EMA20 回踩条件的个股。")

# ---------------------------------------------------------
# 3. 入库逻辑
# ---------------------------------------------------------
def save_to_pool(df_selected, trade_date):
    now = datetime.datetime.now()
    records = []
    for _, row in df_selected.iterrows():
        tags = {
            "strategy": "EMA20_Mainline_Pullback",
            "sector": row['sector_name'],
            "cap_score": row['capital_score']
        }
        records.append({
            "symbol": row["symbol"], "trade_date": trade_date, "stock_name": row["stock_name"],
            "pool_type": "short", "sector_name": str(row["sector_name"])[:60],
            "score": row["sort_score"], "status": "EMA回踩",
            "tags": json.dumps(tags, ensure_ascii=False),
            "notes": f"【主线回踩】{row['sector_name']}领头羊，回踩EMA20，资金分:{row['capital_score']}",
            "created_at": now, "updated_at": now,
            "is_watch_focus": 1 if row["sort_score"] >= 80 else 0,
            "watch_level": 3 if row["sort_score"] >= 85 else 2
        })

    df_save = pd.DataFrame(records)
    with engine_review.begin() as conn:
        df_save.to_sql("tmp_ema_pool", conn, if_exists="replace", index=False)
        conn.execute(text("""
            INSERT INTO stock_pools (symbol, trade_date, stock_name, pool_type, sector_name, score, status, tags, notes, created_at, updated_at, is_watch_focus, watch_level)
            SELECT symbol, trade_date, stock_name, pool_type, sector_name, score, status, tags, notes, created_at, updated_at, is_watch_focus, watch_level FROM tmp_ema_pool
            ON DUPLICATE KEY UPDATE score=VALUES(score), tags=VALUES(tags), notes=VALUES(notes), updated_at=VALUES(updated_at), is_watch_focus=VALUES(is_watch_focus), watch_level=VALUES(watch_level)
        """))
        conn.execute(text("DROP TABLE IF EXISTS tmp_ema_pool"))
    print(f"✅ 成功同步 {len(df_save)} 只标的到 stock_pools")

if __name__ == "__main__":
    select_mainline_ema_strategy()