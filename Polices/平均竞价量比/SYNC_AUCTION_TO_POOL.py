import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime
import json
import warnings
import sys

warnings.filterwarnings('ignore')

# --- 1. 路径与配置加载 ---
sys.path.append(r"C:\ws\trading-polices\config")
import config  

engine_quant = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')
engine_review = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')

# --- 2. 核心辅助函数 ---

def get_sector_sentiment():
    """从 forum_post 获取最近 3 天各板块的平均情绪分"""
    print("🧠 正在分析社交媒体板块情绪...")
    sql = text("""
        SELECT topic, AVG(sentiment) as avg_sent
        FROM forum_post
        WHERE created_time >= DATE_SUB(NOW(), INTERVAL 3 DAY)
          AND topic IS NOT NULL AND topic != '全市场'
        GROUP BY topic
    """)
    try:
        with engine_review.connect() as conn:
            df_sent = pd.read_sql(sql, conn)
        return dict(zip(df_sent['topic'], df_sent['avg_sent']))
    except Exception as e:
        print(f"⚠️ 舆情获取失败(可能表为空): {e}")
        return {}

def get_metadata(conn):
    """获取全市场板块映射"""
    query_rel = text("""
        SELECT symbol, GROUP_CONCAT(DISTINCT sector_name) as all_sectors
        FROM stock_sector_relation
        WHERE sector_name LIKE '行业-%%' OR sector_name LIKE '概念-%%'
        GROUP BY symbol
    """)
    df_rel = pd.read_sql(query_rel, conn)
    
    sector_map = {}
    for _, row in df_rel.iterrows():
        raw_list = row['all_sectors'].split(',')
        filtered = [s.replace('行业-', '').replace('概念-', '') for s in raw_list 
                    if not any(noise in s for noise in config.SECTOR_BLACKLIST)]
        if filtered:
            sector_map[row['symbol']] = filtered
    return sector_map

def calculate_trend_score(row):
    """
    打分模型：竞价(60) + 趋势动能(20) + 价格位置(20)
    """
    # 1. 竞价量能分 (最高 60)
    qr = float(row['avg_ratio'] or 0)
    s_vol = np.clip((qr - 1) / 9 * 60, 0, 60)

    # 2. 动量分 (最高 20)
    mom = float(row['f_mom_20'] or 0)
    s_mom = np.clip(mom * 100, 0, 20) 

    # 3. 价格分 (最高 20)
    pct = float(row['open_pct'] or 0)
    s_price = 20 if 2.0 <= pct <= 5.0 else (10 if 0 < pct < 2.0 else 0)

    return int(s_vol + s_mom + s_price)

# --- 3. 主程序逻辑 ---

def sync_trending_auction_signals():
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🚀 启动‘趋势向上’竞价选股流...")

    # A. 获取舆情数据
    sentiment_map = get_sector_sentiment()

    with engine_quant.connect() as conn:
        # B. 确定最新交易日
        latest_date = conn.execute(text("SELECT MAX(trade_date) FROM stk_auction_signal")).scalar()
        if not latest_date:
            print("❌ 错误：stk_auction_signal 表为空。")
            return
            
        print(f"📅 目标日期: {latest_date}")

        # C. 预加载板块
        sector_map_dict = get_metadata(conn)

        # D. SQL 联结：竞价 + 技术因子表
        query = text("""
            SELECT 
                a.symbol, a.name, a.avg_ratio, a.open_pct, a.auction_amount,
                f.f_mom_20, f.f_macd_dif, f.f_macd_hist, f.f_dist_high
            FROM stk_auction_signal a
            JOIN stk_factors f ON a.symbol = f.symbol AND a.trade_date = f.trade_date
            WHERE a.trade_date = :d 
              AND a.avg_ratio >= 1.5
              AND a.name NOT LIKE '%%ST%%'
              AND (a.symbol LIKE '60%%' OR a.symbol LIKE '00%%' OR a.symbol LIKE '30%%')
              -- 🌟 物理过滤：趋势向上 (动量为正)
              AND f.f_mom_20 > 0
              AND f.f_macd_dif > -0.05
        """)
        df_merged = pd.read_sql(query, conn, params={"d": latest_date})

    if df_merged.empty:
        print("💡 今日未发现符合‘趋势向上’硬条件的竞价信号。")
        return

    records = []
    now = datetime.datetime.now()

    for _, row in df_merged.iterrows():
        symbol = row['symbol']
        sectors = sector_map_dict.get(symbol, ["综合题材"])
        
        # 🌟 舆情软过滤
        max_sent = 50 
        for s in sectors:
            if s in sentiment_map:
                max_sent = max(max_sent, sentiment_map[s])
        
        # 如果所属板块舆情过低，视为风险股剔除
        if max_sent < 35:
            continue

        # 🌟 修正此处函数名调用
        score = calculate_trend_score(row)
        if score < 55: continue

        tags = {
            "strategy": "Trend_Up_Auction",
            "mom20": round(float(row['f_mom_20']), 4),
            "macd_dif": round(float(row['f_macd_dif']), 4),
            "sentiment": round(max_sent, 1)
        }

        records.append({
            'symbol': symbol,
            'trade_date': latest_date,
            'stock_name': row['name'],
            'pool_type': 'short',
            'sector_name': " / ".join(sectors[:2]),
            'score': score,
            'status': "趋势竞价",
            'tags': json.dumps(tags, ensure_ascii=False),
            'notes': f"趋势向上(MOM:{tags['mom20']})，舆情分{tags['sentiment']}。竞价量比{row['avg_ratio']}。",
            'is_watch_focus': 1 if score >= 80 else 0,
            'watch_level': 3 if score >= 80 else 1,
            'created_at': now,
            'updated_at': now
        })

    # E. 写入数据库
    if records:
        df_save = pd.DataFrame(records).sort_values('score', ascending=False)
        with engine_review.begin() as conn_r:
            conn_r.execute(text("DELETE FROM stock_pools WHERE trade_date = :d AND status = '趋势竞价'"), {"d": latest_date})
            df_save.to_sql('stock_pools', con=conn_r, if_exists='append', index=False)
        
        print(f"\n" + "✅" * 5 + f" 成功同步 {len(df_save)} 只标的至作战池 " + "✅" * 5)
        print(df_save[['symbol', 'stock_name', 'score', 'sector_name']].head(10).to_string(index=False))
    else:
        print("💡 经过趋势和舆情双重过滤，无合适标的。")

if __name__ == "__main__":
    sync_trending_auction_signals()