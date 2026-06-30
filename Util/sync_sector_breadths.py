import pandas as pd
from sqlalchemy import create_engine, text
import datetime
import sys

# --- 1. 引入全局配置 ---
sys.path.append(r"C:\ws\trading-polices\config")
try:
    import config
except ImportError:
    class DummyConfig: SECTOR_BLACKLIST = []
    config = DummyConfig()

# --- 2. 数据库配置 ---
engine_quant = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')
engine_review = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')

# 指数黑名单
INDEX_LIST = ['000001.SH', '399001.SZ', '399006.SZ', '000300.SH', '000852.SH']

def get_latest_dates():
    with engine_quant.connect() as conn:
        query = text("SELECT DISTINCT trade_date FROM stk_daily_kline ORDER BY trade_date DESC LIMIT 2")
        res = conn.execute(query).fetchall()
        return [row[0] for row in res]

def sync_sector_data():
    dates = get_latest_dates()
    if len(dates) < 2:
        print("数据不足。")
        return
    
    today, yesterday = dates[0], dates[1]
    print(f"正在分析日期: {today} (对比基准: {yesterday})")

    # 1. 获取个股价格数据
    # 注意：这里多查了一个 trade_date 字段，用于 pivot
    idx_str = "','".join(INDEX_LIST)
    sql_kline = f"""
        SELECT symbol, trade_date, close FROM stk_daily_kline 
        WHERE trade_date IN ('{today}', '{yesterday}') 
        AND symbol NOT IN ('{idx_str}')
    """
    df_all = pd.read_sql(sql_kline, engine_quant)

    if df_all.empty:
        print("未提取到 K 线数据。")
        return

    # 🌟 核心修复 A：强制去重，防止同一天出现重复 symbol
    # 这一步解决了 ValueError 报错
    df_all = df_all.drop_duplicates(subset=['symbol', 'trade_date'], keep='last')

    # 🌟 核心修复 B：正确配置 Pivot 参数
    # index 是行（股票），columns 是列（日期），values 是格心（收盘价）
    try:
        df_pivot = df_all.pivot(index='symbol', columns='trade_date', values='close')
        df_pivot = df_pivot.dropna()
    except Exception as e:
        print(f"数据透视失败: {e}")
        return
    
    # 计算涨幅和上涨状态映射
    pct_change = (df_pivot[today] - df_pivot[yesterday]) / df_pivot[yesterday] * 100
    is_up_map = (pct_change > 0).astype(int)

    # 2. 获取板块映射
    query_sectors = "SELECT symbol, sector_name FROM stock_sector_relation WHERE sector_name LIKE '行业-%%'"
    df_rel = pd.read_sql(query_sectors, engine_quant)
    
    if hasattr(config, 'SECTOR_BLACKLIST') and config.SECTOR_BLACKLIST:
        mask = df_rel['sector_name'].str.replace('行业-', '', regex=False).isin(config.SECTOR_BLACKLIST)
        df_rel = df_rel[~mask]

    final_records = []
    now_time = datetime.datetime.now()

    # --- A. 计算宽基/宽板红盘 rate ---
    broad_groups = {'沪指主板': '60', '深指主板': '00', '创业板': '30', '科创板': '68'}
    for b_name, prefix in broad_groups.items():
        subset = pct_change[pct_change.index.str.startswith(prefix)]
        if not subset.empty:
            adv = (subset > 0).sum()
            total = len(subset)
            final_records.append({
                'trade_date': today, 'sector_name': b_name, 'sector_type': 'broad',
                'red_rate': round(adv / total * 100, 2), 'advancers': int(adv),
                'total_stocks': int(total), 'rank_pos': 0, 'created_at': now_time
            })

    # --- B. 计算行业细分红盘 rate ---
    df_rel['is_up'] = df_rel['symbol'].map(is_up_map)
    df_rel = df_rel.dropna(subset=['is_up'])
    
    ind_stats = df_rel.groupby('sector_name')['is_up'].agg(['sum', 'count']).reset_index()
    ind_stats.columns = ['sector_name', 'advancers', 'total_stocks']
    ind_stats = ind_stats[ind_stats['total_stocks'] >= 3].copy()
    ind_stats['red_rate'] = (ind_stats['advancers'] / ind_stats['total_stocks'] * 100).round(2)
    
    ind_stats = ind_stats.sort_values('red_rate', ascending=False).reset_index(drop=True)
    ind_stats['rank_pos'] = ind_stats.index + 1
    
    for _, row in ind_stats.iterrows():
        final_records.append({
            'trade_date': today, 'sector_name': row['sector_name'].replace('行业-', ''),
            'sector_type': 'industry', 'red_rate': row['red_rate'],
            'advancers': int(row['advancers']), 'total_stocks': int(row['total_stocks']),
            'rank_pos': int(row['rank_pos']), 'created_at': now_time
        })

    # 4. 执行 Upsert 写入
    if not final_records: return

    df_save = pd.DataFrame(final_records)
    upsert_sql = text("""
        INSERT INTO stk_sector_breadths (
            trade_date, sector_name, sector_type, red_rate, 
            advancers, total_stocks, rank_pos, created_at
        ) VALUES (
            :trade_date, :sector_name, :sector_type, :red_rate, 
            :advancers, :total_stocks, :rank_pos, :created_at
        ) ON DUPLICATE KEY UPDATE 
            red_rate = VALUES(red_rate), advancers = VALUES(advancers),
            total_stocks = VALUES(total_stocks), rank_pos = VALUES(rank_pos)
    """)

    with engine_review.begin() as conn:
        conn.execute(upsert_sql, df_save.to_dict(orient='records'))

    print(f"✅ 成功同步 {len(df_save)} 条数据至 stk_sector_breadths。")

if __name__ == "__main__":
    sync_sector_data()