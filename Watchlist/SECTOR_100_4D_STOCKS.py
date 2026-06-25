import json
import pandas as pd
from sqlalchemy import create_engine, text
import datetime
import sys
import numpy as np

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)
engine_review = create_engine(
    'mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review'
)

def get_latest_trade_date():
    with engine.connect() as conn:
        res = conn.execute(text("SELECT MAX(trade_date) FROM stk_daily_kline")).scalar()
    return res

# -------------------------
# scoring (100 分制打分模型)
# -------------------------
def calc_4d_score(row):
    """
    基于趋势、资金、板块强度、量价四维合力的打分模型
    """
    score = 0
    # 1. 板块强度分 (20分) - 这里的 sector_max_inflow_rate 已改为连强权重
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
    
    # 5. 筹码分 (10分)
    score += (row.get("chip_score", 0) / 100 * 10)
    
    # 6. 量价加分项
    qr = row.get("f_quantity_ratio", 1)
    if 1.5 <= qr <= 3: 
        score += 10
    elif qr > 3: 
        score += max(0, 10 - (qr - 3) * 3)
        
    tr = row.get("turnover_rate", 0)
    if 3 <= tr <= 15: 
        score += 5
        
    return round(score, 2)

def save_to_stock_pool(df_selected, trade_date):
    if df_selected.empty: return
    now = datetime.datetime.now()
    records = []
    for _, row in df_selected.iterrows():
        tags = {
            "strategy": "Continuous_Strong_Sectors",
            "capital_score": int(row["capital_score"]),
            "profit_ratio": round(float(row["profit_ratio"]), 2),
            "quantity_ratio": round(float(row["f_quantity_ratio"]), 2),
            "main_net_ratio": round(float(row.get("main_net_ratio", 0)), 2),
            "attack_score": int(row.get("attack_score", 0)),
            "sector_days": int(row.get("strong_days_count", 0))
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
            "notes": f"连强主线({row['strong_days_count']}次Top10)，资金分{row['capital_score']}，获利盘{row['profit_ratio']:.1f}%",
            "created_at": now,
            "updated_at": now,
            "is_watch_focus": 1 if score >= 80 else 0,
            "watch_level": 3 if score >= 85 else (2 if score >= 75 else 1)
        })
        
    df_save = pd.DataFrame(records)
    try:
        with engine_review.begin() as conn:
            df_save.to_sql("tmp_stock_pool", conn, if_exists="replace", index=False)
            upsert_sql = text("""
                INSERT INTO stock_pools (symbol, trade_date, stock_name, pool_type, sector_name, score, status, tags, notes, created_at, updated_at, is_watch_focus, watch_level)
                SELECT symbol, trade_date, stock_name, pool_type, sector_name, score, status, tags, notes, created_at, updated_at, is_watch_focus, watch_level FROM tmp_stock_pool
                ON DUPLICATE KEY UPDATE score = VALUES(score), tags = VALUES(tags), notes = VALUES(notes), updated_at = VALUES(updated_at), is_watch_focus = VALUES(is_watch_focus), watch_level = VALUES(watch_level)
            """)
            conn.execute(upsert_sql)
            conn.execute(text("DROP TABLE IF EXISTS tmp_stock_pool"))
        print(f"✅ 成功同步 {len(df_save)} 只股票到股票池")
    except Exception as e:
        print(f"❌ 入池失败: {e}")

def select_stocks_smart_match():
    today = get_latest_trade_date()
    print(f"🚀 启动四维共振选股 (连强主线升级版)，基准日期: {today}\n")

    # 1. 寻找“连强主线”板块 (过去 7 天有 3 天进入 Top 10)
    print("1️⃣ 正在扫描过去 7 个交易日的板块表现...")
    with engine.connect() as conn:
        date_list_query = text("SELECT DISTINCT trade_date FROM stk_daily_kline ORDER BY trade_date DESC LIMIT 7")
        last_7_dates = [row[0].strftime('%Y-%m-%d') for row in conn.execute(date_list_query).fetchall()]
    
    dates_str = ",".join([f"'{d}'" for d in last_7_dates])

    breadth_sql = f"""
        SELECT sector_name, COUNT(*) as strong_days
        FROM stk_sector_breadths 
        WHERE trade_date IN ({dates_str}) 
          AND rank_pos <= 10 
          AND sector_type = 'industry'
        GROUP BY sector_name
        HAVING strong_days >= 3
        ORDER BY strong_days DESC
    """
    df_strong_sectors = pd.read_sql(text(breadth_sql), engine_review)
    
    if df_strong_sectors.empty:
        print("❌ 当前市场无‘连强’主线板块，建议减仓观望。")
        return

    # 板块强度权重映射
    sector_score_map = {}
    sector_days_map = {}
    for _, row in df_strong_sectors.iterrows():
        days = row['strong_days']
        # 3次=80分, 4次=90分, 5次+=100分
        weight_score = 80 if days == 3 else (90 if days == 4 else 100)
        sector_score_map[row['sector_name']] = weight_score
        sector_days_map[row['sector_name']] = days

    clean_sectors = df_strong_sectors['sector_name'].tolist()
    print(f"🔥 识别到连强主线: {', '.join(clean_sectors)}")

    # 2. 匹配数据库真实板块名
    db_sectors = pd.read_sql("SELECT DISTINCT sector_name FROM stock_sector_relation", engine)['sector_name'].tolist()
    matched_db_sectors = [db for db in db_sectors if any(clean in db for clean in clean_sectors)]
    
    if not matched_db_sectors:
        print("❌ 无法从映射表中定位板块。")
        return

    # 3. 提取板块内个股并计算板块权重
    sectors_str = ",".join([f"'{s}'" for s in matched_db_sectors])
    basic_sql = f"""
        SELECT r.symbol, s.name AS stock_name, GROUP_CONCAT(r.sector_name SEPARATOR ' | ') AS sector_names
        FROM stock_sector_relation r JOIN stocks s ON r.symbol = s.symbol
        WHERE r.sector_name IN ({sectors_str}) AND s.name NOT LIKE '%%ST%%' AND s.name NOT LIKE '%%退%%'
        GROUP BY r.symbol, s.name
    """
    df_basic = pd.read_sql(text(basic_sql), engine)
    
    # 注入板块强度分和出现次数
    def get_sector_info(names_str, info_map):
        if not names_str: return 0
        vals = [info_map.get(n.strip().replace('行业-',''), 0) for n in str(names_str).split('|')]
        return max(vals) if vals else 0

    df_basic['sector_max_inflow_rate'] = df_basic['sector_names'].apply(lambda x: get_sector_info(x, sector_score_map))
    df_basic['strong_days_count'] = df_basic['sector_names'].apply(lambda x: get_sector_info(x, sector_days_map))

    # 4. 获取多维因子
    symbols_str = ",".join([f"'{s}'" for s in df_basic['symbol'].tolist()])
    
    df_factors = pd.read_sql(f"SELECT symbol, f_mom_20, f_macd_dif, f_macd_dea, f_macd_hist, f_bb_m, f_quantity_ratio, f_dist_high FROM stk_factors WHERE trade_date = '{today}' AND symbol IN ({symbols_str})", engine)
    df_kline = pd.read_sql(f"SELECT symbol, open, close, turnover_rate FROM stk_daily_kline WHERE trade_date = '{today}' AND symbol IN ({symbols_str})", engine)
    df_fund = pd.read_sql(f"SELECT symbol, main_net_inflow, main_net_ratio, inflow_3d, buy_power_ratio, attack_score, capital_score, volume_power_ratio FROM stk_stock_fund_flow WHERE trade_date = '{today}' AND symbol IN ({symbols_str})", engine)
    df_chip = pd.read_sql(f"SELECT symbol, profit_ratio, chip_score FROM stk_chip_factor WHERE trade_date = '{today}' AND symbol IN ({symbols_str})", engine)

    # 5. 数据大合并
    df_all = df_basic.merge(df_factors, on='symbol', how='inner') \
                     .merge(df_kline, on='symbol', how='inner') \
                     .merge(df_fund, on='symbol', how='inner') \
                     .merge(df_chip, on='symbol', how='inner')
    
    df_all = df_all.dropna()
    print(f"📊 候选池样本量: {len(df_all)}")

    # 6. 四维共振严格过滤 (Trend + Fund + Chip + Price/Vol)
    cond_trend = (df_all['f_mom_20'] > 0) & (df_all['f_macd_dif'] > df_all['f_macd_dea']) & (df_all['f_macd_hist'] > 0) & (df_all['close'] > df_all['f_bb_m'])
    cond_fund = (df_all['main_net_inflow'] > 0) & (df_all['inflow_3d'] > 0) & (df_all['capital_score'] >= 70) & (df_all['buy_power_ratio'] >= 55) & (df_all['volume_power_ratio'] >= 1.1)
    cond_chip = (df_all['profit_ratio'] > 60) & (df_all['chip_score'] > 60)
    cond_vol_price = (df_all['close'] > df_all['open']) & (df_all['close'] / df_all['open'] > 1.02) & (1.5 <= df_all['f_quantity_ratio'] <= 5.5) & (0.02 <= df_all['turnover_rate'] <= 0.18)
    cond_attack = (df_all["attack_score"] >= 70) & (df_all["buy_power_ratio"] >= 60)

    df_selected = df_all[cond_fund & cond_trend & cond_chip & cond_vol_price & cond_attack].copy()

    # 7. 评分、排序、入库
    if not df_selected.empty:
        df_selected['sort_score'] = df_selected.apply(calc_4d_score, axis=1)
        df_selected = df_selected.sort_values(by='sort_score', ascending=False)
        
        # 最终入库前 50 只
        save_to_stock_pool(df_selected.head(50), today)
        
        # 打印 Top 15 结果
        print("\n" + "="*80)
        print(f"🎯 选股完成：今日共有 {len(df_selected)} 只个股符合连强主线共振条件")
        print("="*80)
        cols = ['symbol', 'stock_name', 'sort_score', 'capital_score', 'strong_days_count', 'profit_ratio']
        print(df_selected[cols].head(15).to_string(index=False))
        print("="*80)
    else:
        print("❌ 今日无符合“连强主线共振”条件的个股。")

if __name__ == "__main__":
    select_stocks_smart_match()