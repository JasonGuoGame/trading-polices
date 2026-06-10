import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime
import json
import warnings
import sys

warnings.filterwarnings('ignore')

# --- 1. 数据库配置 ---
engine_quant = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')
engine_review = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')

# --- config ---
sys.path.append(r"C:\ws\trading-polices\config")
import config

# --- 2. 辅助函数 ---
def clean_sectors(sector_str):
    if not sector_str: return "其他"
    raw_list = sector_str.split(',')
    filtered = [s.replace('行业-', '').replace('概念-', '') for s in raw_list if not any(n in s for n in config.SECTOR_BLACKLIST)]
    return " / ".join(filtered[:2]) if filtered else "其他"

def save_to_db(df, pool_type, trade_date):
    if df.empty: return
    now = datetime.datetime.now()
    records = []
    
    for _, row in df.iterrows():
        tag_data = {"trade_date": str(trade_date)}
        # 构建动态 Notes
        if pool_type == 'short':
            status = '短线爆发黑马'
            tag_data.update({"surge": int(row['surge_count']), "vol": float(row['vol_ratio'])})
            logic_note = f"【短线逻辑】: 板块流入{row['sector_money']:.1f}亿，日内脉冲{int(row['surge_count'])}次，放量{row['vol_ratio']:.1f}倍。收盘位置{row['close_pos']:.2f}属于强承接。"
        else:
            status = '长线牛'
            tag_data.update({"drop": float(row['drop_rate']), "roe": float(row['roe'])})
            logic_note = f"【长线逻辑】: 筹码三连降({abs(row['drop_rate']):.1f}%)，人均持金{row['avg_hold_price']/10000:.1f}万。ROE({row['roe']:.1f})支撑基本面。"

        records.append({
            'symbol': row['symbol'],
            'trade_date': trade_date,
            'stock_name': row['name'],
            'pool_type': pool_type,
            'sector_name': row['核心板块'],
            'score': int(row['total_score']),
            'status': status,
            'tags': json.dumps(tag_data, ensure_ascii=False),
            'notes': logic_note,
            'created_at': now,
            'updated_at': now
        })
    
    df_save = pd.DataFrame(records)
    with engine_review.begin() as conn:
        df_save.to_sql('temp_pool_sync', con=conn, if_exists='replace', index=False)
        conn.execute(text("""
            INSERT INTO stock_pools (symbol, trade_date, stock_name, pool_type, sector_name, score, status, tags, notes, created_at, updated_at)
            SELECT symbol, trade_date, stock_name, pool_type, sector_name, score, status, tags, notes, created_at, updated_at FROM temp_pool_sync
            ON DUPLICATE KEY UPDATE 
                score = VALUES(score), status = VALUES(status), tags = VALUES(tags), notes = VALUES(notes), updated_at = VALUES(updated_at);
        """))
        conn.execute(text("DROP TABLE IF EXISTS temp_pool_sync"))

# --- 3. 核心流水线 ---
def run_scoring_pipeline():
    with engine_quant.connect() as conn:
        today = conn.execute(text("SELECT MAX(trade_date) FROM stk_factors")).fetchone()[0]

    # --- 短线：逻辑优化 ---
    # 增加：close_pos（收盘位置），防止选到高开低走的阴线
    query_st = text("""
        SELECT f.symbol, s.name, flow.net_inflow_amount as sector_money,
               COALESCE(ab.surge_count, 0) as surge_count, f.f_vol_ratio as vol_ratio,
               f.f_macd_dif, (k.close - k.open)/k.open * 100 as pct_chg,
               (k.close - k.low) / (k.high - k.low + 0.01) as close_pos,
               (SELECT GROUP_CONCAT(sector_name) FROM stock_sector_relation WHERE symbol = f.symbol) as all_sectors
        FROM stk_factors f
        JOIN stocks s ON f.symbol = s.symbol
        JOIN stk_daily_kline k ON f.symbol = k.symbol AND k.trade_date = f.trade_date
        JOIN stock_sector_relation r ON f.symbol = r.symbol
        JOIN stk_sector_fund_flow flow ON (r.sector_name = CONCAT('行业-', flow.sector_name) OR r.sector_name = CONCAT('概念-', flow.sector_name))
        LEFT JOIN stk_capital_abnormal ab ON f.symbol = ab.symbol AND f.trade_date = ab.trade_date
        WHERE f.trade_date = :d AND flow.trade_date = :d
          AND k.close > k.open AND f.f_vol_ratio BETWEEN 1.8 AND 4.5
    """)
    df_st = pd.read_sql(query_st, engine_quant, params={"d": today}).sort_values('sector_money', ascending=False).drop_duplicates('symbol')
    df_st['核心板块'] = df_st['all_sectors'].apply(clean_sectors)
    
    # 评分优化：加入收盘位置权重，量能过大扣分
    df_st['total_score'] = (
        np.clip(df_st['sector_money']/10*30, 0, 30) + 
        np.clip(df_st['surge_count']*4, 0, 20) + 
        np.clip(df_st['close_pos']*20, 0, 20) + # 收盘位置越高分越高
        np.where((df_st['f_macd_dif']>0), 20, 0) + 
        np.clip(df_st['pct_chg']*2, 0, 10)
    ).round(1)
    
    st_final = df_st[df_st['total_score'] >= 80].sort_values('total_score', ascending=False)
    save_to_db(st_final, 'short', today)

    # --- 长线：逻辑优化 ---
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
        WHERE f.trade_date = :d AND h.drop_rate < -5 AND h.avg_hold_price > 200000
    """)
    df_lt = pd.read_sql(query_lt, engine_quant, params={"d": today})
    df_lt['核心板块'] = df_lt['all_sectors'].apply(clean_sectors)
    df_lt['total_score'] = (
        np.clip(df_lt['drop_rate']/-15*40, 0, 40) + # 筹码权重提升
        np.clip(df_lt['avg_hold_price']/500000*30, 0, 30) + 
        np.where(df_lt['ma20_slope']>0, 20, 0) + 
        np.clip(df_lt['roe']/15*10, 0, 10)
    ).round(1)
    
    lt_final = df_lt[df_lt['total_score'] >= 80].sort_values('total_score', ascending=False)
    save_to_db(lt_final, 'long', today)
    print("✅ 全维度选股与逻辑存证完成。")

    # =========================
    # 4. 终端美化打印
    # =========================
    pd.set_option('display.max_colwidth', 45) # 确保板块名不被截断
    pd.set_option('display.width', 1000)

    print("\n" + "⚡" * 8 + " [短线爆发黑马池] (信号确认: 强资金+强脉冲) " + "⚡" * 8)
    print("-" * 135)
    if not st_final.empty:
        # 整理打印字段
        st_print = st_final[['symbol', 'name', '核心板块', 'total_score', 'surge_count', 'vol_ratio', 'pct_chg']].copy()
        st_print.columns = ['代码', '名称', '所属板块', '评分', '脉冲', '量比', '涨幅%']
        print(st_print.to_string(index=False))
    else:
        print("今日暂无符合要求的短线标的。")

    print("\n" + "🌊" * 8 + " [趋势稳健牛股池] (信号确认: 筹码集中+绩优) " + "🌊" * 8)
    print("-" * 135)
    if not lt_final.empty:
        # 处理筹码集中度显示
        lt_final['筹码集中度'] = lt_final['drop_rate'].apply(lambda x: f"减{abs(round(x,1))}%")
        lt_print = lt_final[['symbol', 'name',  '核心板块', 'total_score', '筹码集中度', 'roe']].copy()
        lt_print.columns = ['代码', '名称',  '所属板块', '评分', '筹码集中度', 'ROE']
        print(lt_print.to_string(index=False))
    else:
        print("今日暂无符合要求的长线标的。")

    print("\n" + "=" * 135)
    print(f"✅ 全维度评分完成。数据已同步至 trading_review.stock_pools 表。")

if __name__ == "__main__":
    run_scoring_pipeline()