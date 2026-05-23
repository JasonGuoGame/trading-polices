import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime
import json
import warnings
import sys

warnings.filterwarnings('ignore')

# --- 引入全局配置 ---
# 这里使用简化的路径添加方式
sys.path.append(r"C:\ws\trading-polices\config")
import config  # 导入你的全局配置文件

# --- 1. 数据库配置 ---
engine_quant = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')
engine_review = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')

def clean_sectors(sector_str):
    if not sector_str: return "其他"
    raw_list = sector_str.split(',')
    filtered = [s.replace('行业-', '').replace('概念-', '') for s in raw_list if not any(n in s for n in config.SECTOR_BLACKLIST)]
    return " / ".join(filtered[:2]) if filtered else "其他"

def save_to_db(df, pool_type, trade_date):
    """
    将筛选结果存入 trading_review.stock_pools 表 (修正 trade_date 写入)
    """
    if df.empty: return
    
    now = datetime.datetime.now()
    records = []
    
    for _, row in df.iterrows():
        # 构造 JSON 标签
        tag_data = {}
        status = ''
        if pool_type == 'short':
            status = '短线爆发黑马'
            tag_data.update({"surge": int(row['surge_count']), "vol": float(row['vol_ratio']), "pct": float(row['pct_chg'])})
        else:
            status = '长线牛'
            tag_data.update({"drop": float(row['drop_rate']), "roe": float(row['roe'])})
            
        records.append({
            'symbol': row['symbol'],
            'trade_date': trade_date,     # <--- 关键修正：显式添加 trade_date 字段
            'stock_name': row['name'],
            'pool_type': pool_type,
            'sector_name': row['核心板块'],
            'score': int(row['total_score']),
            'status': status,
            'tags': json.dumps(tag_data, ensure_ascii=False),
            'notes': f"系统自动计算评分入库",
            'created_at': now,
            'updated_at': now
        })
    
    df_save = pd.DataFrame(records)
    
    try:
        with engine_review.begin() as conn:
            # 1. 写入临时表
            df_save.to_sql('temp_pool_sync', con=conn, if_exists='replace', index=False)
            
            # 2. 执行合并插入 (明确列出 trade_date)
            upsert_sql = text("""
                INSERT INTO stock_pools (
                    symbol, trade_date, stock_name, pool_type, 
                    sector_name, score, status, tags, notes, created_at, updated_at
                )
                SELECT 
                    symbol, trade_date, stock_name, pool_type, 
                    sector_name, score, status, tags, notes, created_at, updated_at 
                FROM temp_pool_sync
                ON DUPLICATE KEY UPDATE 
                    trade_date = VALUES(trade_date),
                    score = VALUES(score), 
                    sector_name = VALUES(sector_name), 
                    tags = VALUES(tags), 
                    updated_at = VALUES(updated_at);
            """)
            conn.execute(upsert_sql)
            conn.execute(text("DROP TABLE IF EXISTS temp_pool_sync"))
            
        print(f"✅ 成功同步 {len(df_save)} 只【{pool_type}】股票到作战中心数据库。")
    except Exception as e:
        print(f"❌ 存入股票池失败: {e}")

def run_scoring_pipeline():
    # 1. 获取最新日期
    with engine_quant.connect() as conn:
        today = conn.execute(text("SELECT MAX(trade_date) FROM stk_factors")).fetchone()[0]

    print(f"\n[{datetime.datetime.now()}] 🚀 正在执行 {today} 全维度评分系统...")

    # --- 2. 短线黑马池计算 ---
    query_st = text("""
        SELECT f.symbol, s.name, flow.net_inflow_amount as sector_money,
               COALESCE(ab.surge_count, 0) as surge_count, f.f_vol_ratio as vol_ratio,
               f.f_macd_dif, (k.close - k.open)/k.open * 100 as pct_chg,
               (SELECT GROUP_CONCAT(sector_name) FROM stock_sector_relation WHERE symbol = f.symbol) as all_sectors
        FROM stk_factors f
        JOIN stocks s ON f.symbol = s.symbol
        JOIN stk_daily_kline k ON f.symbol = k.symbol AND k.trade_date = f.trade_date
        JOIN stock_sector_relation r ON f.symbol = r.symbol
        JOIN stk_sector_fund_flow flow ON (r.sector_name = CONCAT('行业-', flow.sector_name) OR r.sector_name = CONCAT('概念-', flow.sector_name))
        LEFT JOIN stk_capital_abnormal ab ON f.symbol = ab.symbol AND f.trade_date = ab.trade_date
        WHERE f.trade_date = :d AND flow.trade_date = :d
          AND (f.symbol LIKE '60%' OR f.symbol LIKE '00%' OR f.symbol LIKE '30%')
    """)
    df_st = pd.read_sql(query_st, engine_quant, params={"d": today}).sort_values('sector_money', ascending=False).drop_duplicates('symbol')
    df_st['核心板块'] = df_st['all_sectors'].apply(clean_sectors)
    df_st['total_score'] = (np.clip(df_st['sector_money']/10*30,0,30) + np.clip(df_st['surge_count']*4,0,20) + 
                            np.clip((df_st['vol_ratio']-1)*10,0,20) + np.where(df_st['f_macd_dif']>0,20,0) + 
                            np.clip(df_st['pct_chg']*2,0,10)).round(1)
    df_st['trade_date'] = today
    st_final = df_st[df_st['total_score'] >= 80].sort_values('total_score', ascending=False)

    # --- 3. 长线牛股池计算 ---
    query_lt = text("""
        WITH Holder AS (
            SELECT h1.symbol, ((h1.holder_count - h3.holder_count)/h3.holder_count*100) as drop_rate, h1.avg_hold_price
            FROM (SELECT *, ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY end_date DESC) as rn FROM stk_holders_history) h1
            JOIN (SELECT *, ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY end_date DESC) as rn FROM stk_holders_history) h3 ON h1.symbol = h3.symbol AND h3.rn = 3
            WHERE h1.rn = 1
        )
        SELECT f.symbol, s.name, h.drop_rate, h.avg_hold_price, COALESCE(fund.roe, 0) as roe,
               (f.f_bb_m - f_prev.f_bb_m)/f_prev.f_bb_m * 100 as ma20_slope,
               (SELECT GROUP_CONCAT(sector_name) FROM stock_sector_relation WHERE symbol = f.symbol) as all_sectors
        FROM stk_factors f
        JOIN stocks s ON f.symbol = s.symbol
        JOIN Holder h ON f.symbol = h.symbol
        JOIN stk_factors f_prev ON f.symbol = f_prev.symbol AND f_prev.trade_date = DATE_SUB(f.trade_date, INTERVAL 7 DAY)
        LEFT JOIN stk_fundamental fund ON f.symbol = fund.symbol
        WHERE f.trade_date = :d AND s.name NOT LIKE '%ST%'
    """)
    df_lt = pd.read_sql(query_lt, engine_quant, params={"d": today})
    df_lt['核心板块'] = df_lt['all_sectors'].apply(clean_sectors)
    df_lt['total_score'] = (np.clip(df_lt['drop_rate']/-15*30,0,30) + np.clip(df_lt['avg_hold_price']/500000*30,0,30) + 
                            np.where(df_lt['ma20_slope']>0,20,0) + np.clip(df_lt['roe']/15*20,0,20)).round(1)
    df_lt['trade_date'] = today
    lt_final = df_lt[df_lt['total_score'] >= 80].sort_values('total_score', ascending=False)

    # --- 4. 控制台打印 ---
    pd.set_option('display.max_colwidth', 45)
    
    print("\n" + "⚡" * 8 + " [短线爆发黑马池] " + "⚡" * 8)
    print("-" * 125)
    if not st_final.empty:
        print(st_final[['symbol', 'name', 'trade_date', '核心板块', 'total_score', 'surge_count', 'vol_ratio', 'pct_chg']].to_string(index=False))
    else:
        print("今日暂无符合要求的短线标的。")

    print("\n" + "🌊" * 8 + " [趋势稳健牛股池] " + "🌊" * 8)
    print("-" * 125)
    if not lt_final.empty:
        lt_final['筹码集中度'] = lt_final['drop_rate'].apply(lambda x: f"减{abs(round(x,1))}%")
        print(lt_final[['symbol', 'name', 'trade_date', '核心板块', 'total_score', '筹码集中度', 'roe']].to_string(index=False))
    else:
        print("今日暂无符合要求的长线标的。")

    # --- 5. 入库 ---
    save_to_db(st_final, 'short', today)
    save_to_db(lt_final, 'long', today)

if __name__ == "__main__":
    run_scoring_pipeline()