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

def get_past_15_counts(today, days=6):
    """回溯查询过去N个交易日进入前15名的次数"""
    with engine_review.connect() as conn:
        # 获取最近的N个历史交易日（不含今日）
        date_query = text("""
            SELECT DISTINCT trade_date FROM stk_sector_breadths 
            WHERE trade_date < :t ORDER BY trade_date DESC LIMIT :d
        """)
        past_dates = [r[0] for r in conn.execute(date_query, {"t": today, "d": days}).fetchall()]
        
        if not past_dates:
            return {}
        
        # 统计这些日子里排名前15的板块出现次数
        count_query = text("""
            SELECT sector_name, COUNT(*) as cnt 
            FROM stk_sector_breadths 
            WHERE trade_date IN :dates AND rank_pos <= 15 AND sector_type = 'industry'
            GROUP BY sector_name
        """)
        res = conn.execute(count_query, {"dates": past_dates}).fetchall()
        return {r[0]: r[1] for r in res}

def sync_sector_data():
    dates = get_latest_dates()
    if len(dates) < 2:
        print("数据不足。")
        return
    
    today, yesterday = dates[0], dates[1]
    print(f"正在分析日期: {today} (对比基准: {yesterday})")

    # 1. 获取个股价格数据
    idx_str = "','".join(INDEX_LIST)
    sql_kline = f"""
        SELECT symbol, trade_date, close FROM stk_daily_kline 
        WHERE trade_date IN ('{today}', '{yesterday}') 
        AND symbol NOT IN ('{idx_str}')
    """
    df_all = pd.read_sql(sql_kline, engine_quant)
    if df_all.empty: return

    df_all = df_all.drop_duplicates(subset=['symbol', 'trade_date'], keep='last')

    try:
        df_pivot = df_all.pivot(index='symbol', columns='trade_date', values='close').dropna()
    except Exception as e:
        print(f"数据透视失败: {e}"); return
    
    pct_change = (df_pivot[today] - df_pivot[yesterday]) / df_pivot[yesterday] * 100
    is_up_map = (pct_change > 0).astype(int)

    # 2. 获取板块映射
    query_sectors = "SELECT symbol, sector_name FROM stock_sector_relation WHERE sector_name LIKE '行业-%%'"
    df_rel = pd.read_sql(query_sectors, engine_quant)
    
    if hasattr(config, 'SECTOR_BLACKLIST') and config.SECTOR_BLACKLIST:
        mask = df_rel['sector_name'].str.replace('行业-', '', regex=False).isin(config.SECTOR_BLACKLIST)
        df_rel = df_rel[~mask]

    # 🌟 核心新增：获取历史前15名的统计数据（过去6天）
    history_counts = get_past_15_counts(today, 6)

    final_records = []
    now_time = datetime.datetime.now()

    # --- A. 宽基计算 (不参与领头羊逻辑，设为0) ---
    broad_groups = {'沪指主板': '60', '深指主板': '00', '创业板': '30', '科创板': '68'}
    for b_name, prefix in broad_groups.items():
        subset = pct_change[pct_change.index.str.startswith(prefix)]
        if not subset.empty:
            adv = (subset > 0).sum()
            total = len(subset)
            final_records.append({
                'trade_date': today, 'sector_name': b_name, 'sector_type': 'broad',
                'red_rate': round(adv / total * 100, 2), 'advancers': int(adv),
                'total_stocks': int(total), 'rank_pos': 0, 'created_at': now_time,
                'persistence_7d': 0, 'is_leader': 0
            })

    # --- B. 行业细分计算 ---
    df_rel['is_up'] = df_rel['symbol'].map(is_up_map)
    df_rel = df_rel.dropna(subset=['is_up'])
    ind_stats = df_rel.groupby('sector_name')['is_up'].agg(['sum', 'count']).reset_index()
    ind_stats.columns = ['sector_name', 'advancers', 'total_stocks']
    ind_stats = ind_stats[ind_stats['total_stocks'] >= 5].copy()
    ind_stats['red_rate'] = (ind_stats['advancers'] / ind_stats['total_stocks'] * 100).round(2)
    ind_stats = ind_stats.sort_values('red_rate', ascending=False).reset_index(drop=True)
    ind_stats['rank_pos'] = ind_stats.index + 1
    
    for _, row in ind_stats.iterrows():
        clean_name = row['sector_name'].replace('行业-', '')
        
        # 🌟 核心新增：计算连强逻辑
        # 1. 基础次数 = 过去6天的次数
        p_count = history_counts.get(clean_name, 0)
        # 2. 如果今天也进前15名，次数 + 1
        if row['rank_pos'] <= 15:
            p_count += 1
        
        is_leader = 1 if p_count >= 3 else 0

        final_records.append({
            'trade_date': today, 'sector_name': clean_name,
            'sector_type': 'industry', 'red_rate': row['red_rate'],
            'advancers': int(row['advancers']), 'total_stocks': int(row['total_stocks']),
            'rank_pos': int(row['rank_pos']), 'created_at': now_time,
            'persistence_7d': int(p_count),
            'is_leader': int(is_leader)
        })

    # 4. 执行写入
    if not final_records: return
    df_save = pd.DataFrame(final_records)
    
    upsert_sql = text("""
        INSERT INTO stk_sector_breadths (
            trade_date, sector_name, sector_type, red_rate, 
            advancers, total_stocks, rank_pos, created_at,
            persistence_7d, is_leader
        ) VALUES (
            :trade_date, :sector_name, :sector_type, :red_rate, 
            :advancers, :total_stocks, :rank_pos, :created_at,
            :persistence_7d, :is_leader
        ) ON DUPLICATE KEY UPDATE 
            red_rate = VALUES(red_rate), advancers = VALUES(advancers),
            total_stocks = VALUES(total_stocks), rank_pos = VALUES(rank_pos),
            persistence_7d = VALUES(persistence_7d), is_leader = VALUES(is_leader)
    """)

    with engine_review.begin() as conn:
        conn.execute(upsert_sql, df_save.to_dict(orient='records'))

    leader_count = len(df_save[df_save['is_leader'] == 1])
    print(f"✅ 同步完成！发现 {leader_count} 个领头羊板块。")

if __name__ == "__main__":
    sync_sector_data()