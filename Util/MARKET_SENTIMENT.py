import pandas as pd
from sqlalchemy import create_engine, text
import datetime
import sys
import numpy as np

# --- 1. 引入全局配置 ---
sys.path.append(r"C:\ws\trading-polices\config")
try:
    import config
except ImportError:
    print("❌ 警告：未找到 config 配置文件，黑名单过滤将不生效。")
    class DummyConfig: SECTOR_BLACKLIST = []
    config = DummyConfig()

# --- 2. 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine_review = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')
engine = create_engine(DB_URL)

# --- 3. 指数黑名单 ---
INDEX_LIST = ['000001.SH', '399001.SZ', '399006.SZ', '000300.SH', '000852.SH']

def analyze_market_sentiment():
    print(f"[{datetime.datetime.now()}] 正在计算大盘全景数据...")

    # 1. 获取日期
    with engine.connect() as conn:
        date_query = text("SELECT DISTINCT trade_date FROM stk_daily_kline ORDER BY trade_date DESC LIMIT 2")
        dates = [row[0] for row in conn.execute(date_query).fetchall()]
    
    if len(dates) < 2:
        print("数据库数据不足。")
        return

    today, yesterday = dates[0], dates[1]
    print(f"对比日期: {yesterday} (昨日) vs {today} (今日)")

    # 2. 读取数据
    query = f"SELECT symbol, trade_date, close, amount FROM stk_daily_kline WHERE trade_date IN ('{today}', '{yesterday}')"
    df_all = pd.read_sql(query, engine)
    df_all = df_all[~df_all['symbol'].isin(INDEX_LIST)]
    
    # 3. 计算今日涨跌
    df_pivot = df_all.pivot(index='symbol', columns='trade_date', values=['close', 'amount']).dropna()
    close_today = df_pivot['close'][today]
    close_yesterday = df_pivot['close'][yesterday]
    pct_change = (close_today - close_yesterday) / close_yesterday * 100
    
    # 4. 获取板块映射并过滤黑名单
    query_sectors = "SELECT symbol, sector_name FROM stock_sector_relation WHERE sector_name LIKE '行业-%%'"
    df_sectors = pd.read_sql(query_sectors, engine)
    
    if hasattr(config, 'SECTOR_BLACKLIST') and config.SECTOR_BLACKLIST:
        # 修复：先去掉前缀再匹配黑名单
        mask = df_sectors['sector_name'].str.replace('行业-', '', regex=False).isin(config.SECTOR_BLACKLIST)
        df_sectors = df_sectors[~mask]
    
    # 5. 统计今日板块红盘率
    df_sectors = df_sectors[df_sectors['symbol'].isin(pct_change.index)]
    df_sectors['is_up'] = df_sectors['symbol'].map(lambda x: 1 if pct_change.get(x, 0) > 0 else 0)
    sector_stats = df_sectors.groupby('sector_name')['is_up'].agg(['sum', 'count'])
    sector_stats['red_rate'] = (sector_stats['sum'] / sector_stats['count']) * 100
    sector_stats = sector_stats[sector_stats['count'] >= 3] # 过滤成分股过少的
    
    top_today = sector_stats.sort_values(by='red_rate', ascending=False).head(5)
    bottom_today = sector_stats.sort_values(by='red_rate', ascending=True).head(5)

    # 🌟 🆕 新增：从数据库读取昨日 Top 5 板块
    yesterday_top_5 = []
    try:
        with engine_review.connect() as conn:
            y_query = text("""
                SELECT sector_name, red_rate FROM stk_sector_breadths 
                WHERE trade_date = :y_date AND sector_type = 'industry' 
                ORDER BY rank_pos ASC LIMIT 5
            """)
            yesterday_top_5 = conn.execute(y_query, {"y_date": yesterday}).fetchall()
    except Exception as e:
        pass # 如果表不存在或没数据则跳过

    # --- 结果展示 ---
    print("\n" + "="*60)
    print(f"📊 大盘盘面评估汇总 ({today})")
    print("-" * 60)
    
    # 宽基红盘率
    def get_rr(prefix):
        s = pct_change[pct_change.index.str.startswith(prefix)]
        return (s > 0).sum() / len(s) * 100 if len(s) > 0 else 0

    print(f"🏛️ 沪指主板: {get_rr('60'):>5.1f}% | 🏛️ 深指主板: {get_rr('00'):>5.1f}%")
    print(f"🚀 创业板:   {get_rr('30'):>5.1f}% | 🧪 科创板:   {get_rr('68'):>5.1f}%")
    print("-" * 60)

    # 昨日强势回顾
    if yesterday_top_5:
        print(f"⏪ 昨日强势回顾 ({yesterday} Top 5):")
        y_names = [f"{r[0]}({r[1]}%)" for r in yesterday_top_5]
        print(f"  {' ➔ '.join(y_names)}")
        print("-" * 60)

    # 今日排行
    print("🔥 今日行业情绪最强 Top 5:")
    for name, row in top_today.iterrows():
        clean_name = name.replace('行业-', '')
        print(f"  {clean_name:<12}: {row['red_rate']:>6.1f}% ({int(row['sum'])}/{int(row['count'])})")
    
    print("\n❄️ 今日行业情绪最弱 Bottom 5:")
    for name, row in bottom_today.iterrows():
        clean_name = name.replace('行业-', '')
        print(f"  {clean_name:<12}: {row['red_rate']:>6.1f}% ({int(row['sum'])}/{int(row['count'])})")
    print("-" * 60)
    
    # 成交额
    amt_today = df_pivot['amount'][today].sum() / 1e8
    amt_yesterday = df_pivot['amount'][yesterday].sum() / 1e8
    diff = amt_today - amt_yesterday
    print(f"💰 总成交: {amt_today:.2f}亿 | 变动: {diff:+.2f}亿 ({ (diff/amt_yesterday)*100:+.2f}%)")
    print("="*60 + "\n")

    # ==========================================
    # 保存逻辑 (存入两个表)
    # ==========================================
    with engine_review.begin() as conn:
        # 1. 保存大盘宽度
        conn.execute(text("""
            INSERT INTO market_breadths (trade_date, total_stocks, advancers, decliners, flat, limit_up, limit_down, created_at)
            VALUES (:d, :t, :a, :de, :f, :lu, :ld, :c)
            ON DUPLICATE KEY UPDATE advancers=VALUES(advancers), decliners=VALUES(decliners)
        """), {
            "d": today, "t": int(len(pct_change)), "a": int((pct_change>0).sum()), 
            "de": int((pct_change<0).sum()), "f": int((pct_change==0).sum()),
            "lu": int((pct_change>=9.8).sum()), "ld": int((pct_change<=-9.8).sum()),
            "c": datetime.datetime.now()
        })

        # 2. 保存板块维度 (stk_sector_breadths)
        records = []
        df_sorted = sector_stats.sort_values(by='red_rate', ascending=False).reset_index()
        for idx, row in df_sorted.iterrows():
            records.append({
                'trade_date': today, 'sector_name': row['sector_name'].replace('行业-', ''),
                'sector_type': 'industry', 'red_rate': round(row['red_rate'], 2),
                'advancers': int(row['sum']), 'total_stocks': int(row['count']),
                'rank_pos': idx + 1, 'created_at': datetime.datetime.now()
            })
        
        if records:
            conn.execute(text("""
                INSERT INTO stk_sector_breadths (trade_date, sector_name, sector_type, red_rate, advancers, total_stocks, rank_pos, created_at)
                VALUES (:trade_date, :sector_name, :sector_type, :red_rate, :advancers, :total_stocks, :rank_pos, :created_at)
                ON DUPLICATE KEY UPDATE red_rate=VALUES(red_rate), rank_pos=VALUES(rank_pos)
            """), records)

    print(f"✅ 数据同步完成。")

if __name__ == "__main__":
    analyze_market_sentiment()