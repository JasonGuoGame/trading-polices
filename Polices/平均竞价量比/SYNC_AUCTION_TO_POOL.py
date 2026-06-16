import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime
import json
import warnings
import sys
import os

warnings.filterwarnings('ignore')

# --- 1. 路径与配置加载 ---
sys.path.append(r"C:\ws\trading-polices\config")
import config  # 导入你的全局黑名单配置

# 数据库配置
engine_quant = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')
engine_review = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')

# --- 2. 核心辅助函数 ---

def get_metadata(conn):
    """
    一次性获取全市场脱水后的板块映射，避免循环查询
    """
    print("🔎 正在预加载板块映射与黑名单过滤...")
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
        # 应用黑名单过滤逻辑
        filtered = []
        for s in raw_list:
            if not any(noise in s for noise in config.SECTOR_BLACKLIST):
                clean_s = s.replace('行业-', '').replace('概念-', '')
                filtered.append(clean_s)
        
        # 提取前两个板块
        if len(filtered) >= 2:
            sector_map[row['symbol']] = f"{filtered[0]} / {filtered[1]}"
        elif len(filtered) == 1:
            sector_map[row['symbol']] = filtered[0]
        else:
            sector_map[row['symbol']] = "综合题材"
            
    return sector_map

def calculate_scientific_score(row):
    """
    科学评分逻辑：量比(60) + 涨幅(30) + 金额(10)
    使用数据库中的 avg_ratio, open_pct, auction_amount
    """
    # 1. 量能分 (Max 60)
    qr = float(row['avg_ratio'] or 0)
    s_vol = np.clip((qr - 1) / 9 * 60, 0, 60) if qr >= 1 else 0

    # 2. 价格分 (Max 30) - 黄金区间 2%-5%
    pct = float(row['open_pct'] or 0)
    if 2.0 <= pct <= 5.0: s_price = 30
    elif 0 < pct < 2.0: s_price = (pct / 2.0) * 20
    elif 5.0 < pct <= 7.0: s_price = 15
    else: s_price = 0

    # 3. 金额分 (Max 10) - 500万以上满分
    amt = float(row['auction_amount'] or 0)
    s_amt = np.clip(amt / 500 * 10, 0, 10)

    return int(s_vol + s_price + s_amt)

# --- 3. 主程序逻辑 ---

def sync_auction_signals_to_pool():
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🚀 启动竞价信号入库流程...")

    with engine_quant.connect() as conn:
        # A. 确定最新交易日
        date_res = conn.execute(text("SELECT MAX(trade_date) FROM stk_auction_signal")).fetchone()
        latest_date = date_res[0]
        if not latest_date:
            print("❌ 错误：stk_auction_signal 表为空，请先运行信号采集脚本。")
            return
        
        print(f"📅 目标日期: {latest_date}")

        # B. 预加载板块信息
        sector_map = get_metadata(conn)

        # C. 提取今日高量比信号 (量比 > 1.5)
        query = text("""
            SELECT symbol, name, avg_ratio, open_pct, auction_amount 
            FROM stk_auction_signal 
            WHERE trade_date = :d AND avg_ratio >= 1.5
              AND name NOT LIKE '%%ST%%'
              AND (symbol LIKE '60%%' OR symbol LIKE '00%%' OR symbol LIKE '30%%')
        """)
        df_signals = pd.read_sql(query, conn, params={"d": latest_date})

    if df_signals.empty:
        print("💡 今日无达标的竞价异动信号。")
        return

    # D. 处理数据并打分
    records = []
    now = datetime.datetime.now()
    db_status = "竞价异动"

    for _, row in df_signals.iterrows():
        symbol = row['symbol']
        score = calculate_scientific_score(row)
        
        # 只要 60 分以上的
        if score < 60: continue

        tags = {
            "strategy": "DB_Auction",
            "ratio": float(row['avg_ratio']),
            "open_pct": float(row['open_pct']),
            "amt_wan": float(row['auction_amount'])
        }

        records.append({
            'symbol': symbol,
            'trade_date': latest_date,
            'stock_name': row['name'],
            'pool_type': 'short',
            'sector_name': sector_map.get(symbol, "未分类"),
            'score': score,
            'status': db_status,
            'tags': json.dumps(tags, ensure_ascii=False),
            'notes': f"竞价量比{row['avg_ratio']}倍，成交{row['auction_amount']}万。量价共振强。",
            'is_watch_focus': 1 if score >= 85 else 0,
            'watch_level': 3 if score >= 85 else 1,
            'created_at': now,
            'updated_at': now
        })

    # E. 执行数据库写入 (UPSERT)
    if records:
        df_save = pd.DataFrame(records).sort_values('score', ascending=False)
        
        try:
            with engine_review.begin() as conn_r:
                # 物理删除今日旧的“竞价强异动”
                conn_r.execute(text("DELETE FROM stock_pools WHERE trade_date = :d AND status = :s"), 
                             {"d": latest_date, "s": db_status})
                # 写入
                df_save.to_sql('stock_pools', con=conn_r, if_exists='append', index=False, chunksize=1000)
            
            # F. 输出报告
            print("\n" + "🏁" * 10 + " 竞价选股入库清单 (Top 15) " + "🏁" * 10)
            print("-" * 110)
            print(df_save[['symbol', 'stock_name', 'sector_name', 'score', 'status']].head(15).to_string(index=False))
            print("-" * 110)
            print(f"✅ 成功将 {len(df_save)} 只标的同步至作战池。")
        except Exception as e:
            print(f"❌ 写入数据库失败: {e}")
    else:
        print("💡 筛选完成，无高分标的入选。")

if __name__ == "__main__":
    sync_auction_signals_to_pool()