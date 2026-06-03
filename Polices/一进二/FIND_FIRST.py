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
import config  # 导入你的全局配置文件

# 数据库连接
engine_quant = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')
engine_review = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')

# --- 2. 辅助函数：板块脱水与提取 ---
def clean_and_pick_two_sectors(sector_str):
    if not sector_str: return "其他"
    raw_list = sector_str.split(',')
    filtered = []
    for s in raw_list:
        if not any(noise in s for noise in config.SECTOR_BLACKLIST):
            clean_s = s.replace('行业-', '').replace('概念-', '')
            filtered.append(clean_s)
    if len(filtered) >= 2:
        return f"{filtered[0]} / {filtered[1]}"
    elif len(filtered) == 1:
        return filtered[0]
    return "核心题材"

def calculate_sniper_score(row):
    """
    首板狙击评分 (0-100):
    1. 板块流入 (50分): 2亿起步, 10亿以上给50分
    2. 放量倍数 (50分): 1.8倍起步, 4倍以上给50分
    """
    s1 = np.clip((row['板块流入'] - 2) * 5 + 10, 10, 50)
    s2 = np.clip((row['放量倍数'] - 1.8) * 20 + 10, 10, 50)
    return int(s1 + s2)

def save_to_stock_pool(df, trade_date):
    """
    将首板名单全量替换存入 trading_review.stock_pools
    """
    if df.empty: return
    now = datetime.datetime.now()
    records = []
    db_status = "首板狙击"

    for _, row in df.iterrows():
        # 1. 板块脱水
        cleaned_sector = clean_and_pick_two_sectors(row['all_sectors'])
        
        # 2. 构造 JSON 标签
        tags_dict = {
            "strategy": "Mainline_First_Board",
            "vol_ratio": float(row['放量倍数']),
            "inflow": float(row['板块流入']),
            "pct_chg": float(row['涨幅'])
        }

        records.append({
            'symbol': row['symbol'],
            'trade_date': trade_date,
            'stock_name': row['名称'],
            'pool_type': 'short',
            'sector_name': cleaned_sector,
            'score': calculate_sniper_score(row),
            'status': db_status,
            'tags': json.dumps(tags_dict, ensure_ascii=False),
            'notes': f"今日[{cleaned_sector}]主线爆发，个股放量{row['放量倍数']}倍封首板，主力抢筹迹象明显。",
            'is_watch_focus': 1,  # 设为重点关注
            'watch_level': 3,     # 最高观察等级
            'created_at': now,
            'updated_at': now
        })

    df_save = pd.DataFrame(records).drop_duplicates(subset=['symbol'])

    try:
        with engine_review.begin() as conn:
            # 物理删除今日旧的“首板狙击”记录，确保去伪存真
            conn.execute(text("DELETE FROM stock_pools WHERE trade_date = :d AND status = :s"), {"d": trade_date, "s": db_status})
            # 批量写入
            df_save.to_sql('stock_pools', con=conn, if_exists='append', index=False)
        print(f"✅ 成功同步 {len(df_save)} 只首板标的到 [短线作战池]")
    except Exception as e:
        print(f"❌ 数据库写入失败: {e}")

# --- 3. 核心筛选逻辑 ---
def run_first_board_pipeline():
    print(f"[{datetime.datetime.now()}] 🚀 启动【主线首板放量】深度筛选...")

    with engine_quant.connect() as conn:
        # 获取最近三个交易日 (用于判定首板)
        date_res = conn.execute(text("SELECT DISTINCT trade_date FROM stk_daily_kline ORDER BY trade_date DESC LIMIT 3")).fetchall()
        if len(date_res) < 3: return
        today, yesterday, day_before = date_res[0][0], date_res[1][0], date_res[2][0]

        # 动态生成 SQL 黑名单
        blacklist_sql = " AND ".join([f"r.sector_name NOT LIKE '%%{k}%%'" for k in config.SECTOR_BLACKLIST])

        query = text(f"""
            SELECT 
                k_t.symbol, 
                MAX(s.name) as '名称', 
                MAX(flow.sector_name) as 'main_sector',
                MAX(flow.net_inflow_amount) as '板块流入',
                MAX(k_t.close) as '收盘价',
                ROUND(MAX(k_t.volume) / MAX(k_y.volume), 2) as '放量倍数',
                ROUND((MAX(k_t.close) - MAX(k_y.close)) / MAX(k_y.close) * 100, 2) as '涨幅',
                (SELECT GROUP_CONCAT(DISTINCT sector_name) FROM stock_sector_relation WHERE symbol = k_t.symbol) as all_sectors
            FROM stk_daily_kline k_t
            JOIN stk_daily_kline k_y ON k_t.symbol = k_y.symbol AND k_y.trade_date = :yest
            JOIN stocks s ON k_t.symbol = s.symbol
            JOIN stock_sector_relation r ON k_t.symbol = r.symbol
            JOIN stk_sector_fund_flow flow ON (r.sector_name = CONCAT('行业-', flow.sector_name) OR r.sector_name = CONCAT('概念-', flow.sector_name))
            WHERE k_t.trade_date = :today
              AND flow.trade_date = :today
              AND flow.net_inflow_amount > 2.0                     -- 板块必须是吸金主线
              AND k_t.close >= ROUND(k_y.close * 1.098, 2)        -- 今天涨停
              AND k_y.close < ROUND((SELECT close FROM stk_daily_kline WHERE symbol=k_t.symbol AND trade_date = :db ORDER BY trade_date DESC LIMIT 1) * 1.098, 2) -- 昨天没涨停
              AND k_t.volume > k_y.volume * 1.8                    -- 充分换手
              AND (r.sector_name LIKE '行业-%%' OR r.sector_name LIKE '概念-%%')
              AND ({blacklist_sql})
            GROUP BY k_t.symbol
            ORDER BY MAX(flow.net_inflow_amount) DESC
        """)
        
        df_candidates = pd.read_sql(query, conn, params={"today": today, "yest": yesterday, "db": day_before})

    if not df_candidates.empty:
        # 终端报告
        print("\n" + "⚔️" * 10 + " 明日【一进二】狙击名单 " + "⚔️" * 10)
        print("-" * 115)
        # 在打印前预计算分数
        df_candidates['score'] = df_candidates.apply(calculate_sniper_score, axis=1)
        df_candidates = df_candidates.sort_values('score', ascending=False)
        
        # 格式化打印
        report_df = df_candidates.copy()
        report_df['板块'] = report_df['all_sectors'].apply(clean_and_pick_two_sectors)
        print(report_df[['symbol', '名称', '板块', 'score', '板块流入', '放量倍数', '涨幅']].to_string(index=False))
        print("-" * 115)
        
        # 执行入库
        save_to_stock_pool(df_candidates, today)
    else:
        print("💡 今日未发现符合‘主线+换手首板’的极品标的。")

if __name__ == "__main__":
    run_first_board_pipeline()