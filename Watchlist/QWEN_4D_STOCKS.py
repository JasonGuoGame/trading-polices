import json
import pandas as pd
from sqlalchemy import create_engine, text
import datetime

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
# scoring (全新四维合力版)
# -------------------------
def calc_4d_score(row):
    """
    基于趋势、资金、板块、量价四维合力的 100 分制科学打分模型
    """
    score = 0.0
    
    # 1. 板块维度 (最高 20 分)
    # 🌟 修复：使用映射过来的板块最大净流入占比 (sector_max_inflow_rate)
    sector_rate = row.get('sector_max_inflow_rate', 0)
    score += min(sector_rate * 4, 20)
    
    # 2. 资金维度 (最高 30 分)
    # 🌟 修复：使用查询出来的个股主力净流入占比 (main_net_ratio)
    score += row.get('capital_score', 0) * 0.2  
    main_ratio = row.get('main_net_ratio', 0)
    score += min(main_ratio, 10) 
    
    # 3. 趋势维度 (最高 25 分)
    score += min(row.get('f_mom_20', 0) * 100, 15)
    dist_score = max(0, 10 - row.get('f_dist_high', 20))
    score += dist_score
    
    # 4. 量价维度 (最高 25 分)
    qr = row.get('f_quantity_ratio', 1.0)
    if 1.5 <= qr <= 3.0:
        score += 15
    elif qr > 3.0:
        score += max(0, 15 - (qr - 3.0) * 5) 
    else:
        score += (qr / 1.5) * 15             
        
    tr = row.get('turnover_rate', 0)
    if 5.0 <= tr <= 15.0:
        score += 10
    elif tr > 15.0:
        score += max(0, 10 - (tr - 15.0) * 0.5) 
    else:
        score += (tr / 5.0) * 10                
        
    return round(score, 2)

def save_to_stock_pool(df_selected, trade_date):
    if df_selected.empty:
        print("⚠️ 无股票需要入池")
        return

    now = datetime.datetime.now()
    records = []

    for _, row in df_selected.iterrows():
        tags = {
            "strategy": "FourDimResonance",
            "capital_score": int(row["capital_score"]),
            "profit_ratio": round(float(row["profit_ratio"]), 2),
            "quantity_ratio": round(float(row["f_quantity_ratio"]), 2),
            "main_net_ratio": round(float(row.get("main_net_ratio", 0)), 2),
            "sector_rate": round(float(row.get("sector_max_inflow_rate", 0)), 2)
        }
        
        # 直接使用已经计算好的 sort_score
        score = row["sort_score"] 

        records.append({
            "symbol": row["symbol"],
            "trade_date": trade_date,
            "stock_name": row["stock_name"],
            "pool_type": "short",
            "sector_name": str(row["sector_names"])[:95], # 截断防报错
            "score": score,
            "status": "四维共振",
            "tags": json.dumps(tags, ensure_ascii=False),
            "notes": f"资金评分{row['capital_score']}，板块流入占比{row.get('sector_max_inflow_rate', 0):.1f}%，获利盘{row['profit_ratio']:.1f}%",
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
            INSERT INTO stock_pools (
                symbol, trade_date, stock_name, pool_type, sector_name, score, status, tags, notes,
                created_at, updated_at, is_watch_focus, watch_level
            )
            SELECT 
                symbol, trade_date, stock_name, pool_type, sector_name, score, status, tags, notes,
                created_at, updated_at, is_watch_focus, watch_level
            FROM tmp_stock_pool
            ON DUPLICATE KEY UPDATE
                score = VALUES(score), sector_name = VALUES(sector_name), tags = VALUES(tags), 
                notes = VALUES(notes), updated_at = VALUES(updated_at), 
                is_watch_focus = VALUES(is_watch_focus), watch_level = VALUES(watch_level)
            """)
            conn.execute(upsert_sql)
            conn.execute(text("DROP TABLE IF EXISTS tmp_stock_pool"))

        print(f"✅ 成功同步 {len(df_save)} 只股票到股票池")
    except Exception as e:
        print(f"❌ 入池失败: {e}")

def select_stocks_smart_match():
    today = get_latest_trade_date()
    print(f"🚀 开始执行智能匹配四维选股，基准日期: {today}\n")

    # ==========================================
    # 步骤 1：获取资金流 TOP 20 干净板块名 (🌟 补充查询 net_inflow_rate)
    # ==========================================
    print("1️⃣ 正在计算当日最强板块 TOP 10...")
    flow_sql = f"""
    SELECT sector_name, net_inflow_amount, net_inflow_rate 
    FROM stk_sector_fund_flow 
    WHERE trade_date = '{today}' AND net_inflow_amount > 0
    ORDER BY net_inflow_amount DESC LIMIT 10
    """
    df_flow = pd.read_sql(text(flow_sql), engine)
    
    if df_flow.empty:
        print("❌ 今日无主力净流入板块，市场极度弱势，建议空仓。")
        return

    clean_sectors = df_flow['sector_name'].tolist()
    print(f"💰 资金流强势板块: {', '.join(clean_sectors)}")

    # 🌟 核心修复：构建板块名称到净流入占比的映射字典
    # 用于后续给个股打上“所属板块资金强度”的标签
    sector_rate_map = dict(zip(df_flow['sector_name'], df_flow['net_inflow_rate'].fillna(0)))

    # ==========================================
    # 步骤 2：Python 端智能模糊匹配
    # ==========================================
    print("2️⃣ 正在智能匹配数据库中的真实板块名称...")
    all_db_sectors_sql = "SELECT DISTINCT sector_name FROM stock_sector_relation"
    db_sectors = pd.read_sql(text(all_db_sectors_sql), engine)['sector_name'].tolist()
    
    matched_db_sectors = set()
    for clean_name in clean_sectors:
        for db_name in db_sectors:
            if clean_name in db_name:
                matched_db_sectors.add(db_name)
                
    if not matched_db_sectors:
        print("❌ 致命错误：无法映射任何板块，请检查数据源。")
        return

    # ==========================================
    # 步骤 3：使用匹配到的真实板块名称进行选股
    # ==========================================
    print("3️⃣ 正在提取强势板块内个股的多维数据...")
    sectors_str = ",".join([f"'{str(s).replace(chr(39), chr(39)+chr(39))}'" for s in matched_db_sectors])
    
    basic_sql = f"""
    SELECT 
        r.symbol, 
        s.name AS stock_name,
        GROUP_CONCAT(r.sector_name SEPARATOR ' | ') AS sector_names
    FROM stock_sector_relation r
    JOIN stocks s ON r.symbol = s.symbol
    WHERE r.sector_name IN ({sectors_str})
      AND s.name NOT LIKE '%ST%' AND s.name NOT LIKE '%退%'
    GROUP BY r.symbol, s.name
    """
    df_basic = pd.read_sql(text(basic_sql), engine)
    
    if df_basic.empty:
        print("❌ 强势板块内无有效个股。")
        return

    # 🌟 核心修复：解析 sector_names，提取该股票所属板块中最大的净流入占比
    def get_max_sector_rate(names_str):
        if not names_str: return 0
        # 分割字符串，查找映射字典，取最大值
        rates = [sector_rate_map.get(name.strip(), 0) for name in str(names_str).split('|')]
        return max(rates) if rates else 0

    df_basic['sector_max_inflow_rate'] = df_basic['sector_names'].apply(get_max_sector_rate)

    symbols_str = ",".join([f"'{s}'" for s in df_basic['symbol'].tolist()])

    # 获取多维因子数据
    factor_sql = f"""SELECT symbol, f_mom_20, f_macd_dif, f_macd_dea, f_macd_hist, f_bb_m, f_quantity_ratio, f_dist_high FROM stk_factors WHERE trade_date = '{today}' AND symbol IN ({symbols_str})"""
    df_factors = pd.read_sql(text(factor_sql), engine)

    kline_sql = f"""SELECT symbol, open, close, turnover_rate FROM stk_daily_kline WHERE trade_date = '{today}' AND symbol IN ({symbols_str})"""
    df_kline = pd.read_sql(text(kline_sql), engine)

    # 🌟 核心修复：补充查询 main_net_ratio (个股主力净流入占比)
    fund_sql = f"""SELECT symbol, main_net_inflow, main_net_ratio, inflow_3d, capital_score FROM stk_stock_fund_flow WHERE trade_date = '{today}' AND symbol IN ({symbols_str})"""
    df_fund = pd.read_sql(text(fund_sql), engine)

    chip_sql = f"""SELECT symbol, profit_ratio, chip_score FROM stk_chip_factor WHERE trade_date = '{today}' AND symbol IN ({symbols_str})"""
    df_chip = pd.read_sql(text(chip_sql), engine)

    # 合并数据
    df_all = df_basic.merge(df_kline, on='symbol', how='inner') \
                     .merge(df_factors, on='symbol', how='inner') \
                     .merge(df_fund, on='symbol', how='inner') \
                     .merge(df_chip, on='symbol', how='inner')
    
    df_all = df_all.dropna()
    print(f"📊 多维数据完整的股票数量: {len(df_all)}\n")

    # ==========================================
    # 步骤 4：四维共振严格过滤
    # ==========================================
    print("4️⃣ 正在执行四维共振严格过滤...")
    
    cond_trend = (df_all['f_mom_20'] > 0) & (df_all['f_macd_dif'] > df_all['f_macd_dea']) & (df_all['f_macd_hist'] > 0) & (df_all['close'] > df_all['f_bb_m'])
    # cond_fund = (df_all['main_net_inflow'] > 0) & (df_all['capital_score'] >= 70)
    cond_fund = (df_all['main_net_inflow'] > 0) & (df_all['inflow_3d'] > 0) & (df_all['capital_score'] >= 70)
    cond_chip = (df_all['profit_ratio'] > 60) & (df_all['chip_score'] > 60)
    
    # 🌟 核心修复：换手率阈值改回 3.0 和 15.0 (数据库存的是百分比数值，如 5.0 代表 5%)
    cond_vol_price = (df_all['close'] > df_all['open']) & \
                     (df_all['close'] / df_all['open'] > 1.02) & \
                     (df_all['f_quantity_ratio'] > 1.5) & \
                     (df_all['f_quantity_ratio'] < 5.0) & \
                     (df_all['turnover_rate'] > 0.03) & \
                     (df_all['turnover_rate'] < 0.15)

    df_selected = df_all[cond_fund & cond_trend & cond_chip & cond_vol_price].copy()

    # ==========================================
    # 步骤 5：结果输出与入库
    # ==========================================
    print("="*90)
    print(f"🎯 四维共振选股结果 (共 {len(df_selected)} 只)")
    print("="*90)
    
    if not df_selected.empty:
        # 🌟 核心修复：使用全新的 calc_4d_score 进行排序
        df_selected['sort_score'] = df_selected.apply(calc_4d_score, axis=1)
        df_selected = df_selected.sort_values(by='sort_score', ascending=False)
        
        # 入库前 50 名
        save_to_stock_pool(df_selected.head(50), today)

        cols = ['symbol', 'stock_name', 'sector_names', 'sort_score', 'capital_score', 'main_net_ratio', 'sector_max_inflow_rate', 'profit_ratio', 'f_quantity_ratio', 'turnover_rate']
        rename_dict = {
            'symbol': '代码', 'stock_name': '名称', 'sector_names': '所属强势板块', 
            'sort_score': '四维总分', 'capital_score': '资金评分', 
            'main_net_ratio': '个股流入占比%', 'sector_max_inflow_rate': '板块流入占比%',
            'profit_ratio': '获利盘%', 'f_quantity_ratio': '量比', 'turnover_rate': '换手率%'
        }
        
        df_display = df_selected[cols].head(20).rename(columns=rename_dict)
        
        # 格式化输出
        for col in ['个股流入占比%', '板块流入占比%', '获利盘%', '换手率%']:
            df_display[col] = df_display[col].apply(lambda x: f"{x:.2f}%")
            
        print(df_display.to_string(index=False))
    else:
        print("❌ 今日无符合四维共振条件的股票，建议空仓观望。")
    print("="*90)

if __name__ == "__main__":
    select_stocks_smart_match()