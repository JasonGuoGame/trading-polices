import pandas as pd
import numpy as np
from xtquant import xtdata
from sqlalchemy import create_engine, text
import datetime
import json
import warnings
import sys
import os

warnings.filterwarnings('ignore')

# --- 1. 路径与配置加载 ---
sys.path.append(r"C:\ws\trading-polices\config")
import config  # 导入你的黑名单配置

# 数据库配置
engine_quant = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')
engine_review = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')

# --- 2. 辅助函数 ---
def get_clean_sectors(symbol, conn):
    """为个股提取脱水后的双板块"""
    query = text("""
        SELECT GROUP_CONCAT(DISTINCT sector_name) 
        FROM stock_sector_relation WHERE symbol = :s
    """)
    res = conn.execute(query, {"s": symbol}).fetchone()[0]
    if not res: return "未分类"
    
    raw_list = res.split(',')
    filtered = []
    for s in raw_list:
        if not any(noise in s for noise in config.SECTOR_BLACKLIST):
            clean_s = s.replace('行业-', '').replace('概念-', '')
            filtered.append(clean_s)
    
    if len(filtered) >= 2: return f"{filtered[0]} / {filtered[1]}"
    elif len(filtered) == 1: return filtered[0]
    return "综合题材"

def get_all_metadata(conn):
    """
    一次性获取全市场名称和脱水后的板块映射
    """
    print("🔎 正在预加载板块映射...")
    # 1. 获取名称
    df_names = pd.read_sql("SELECT symbol, name FROM stocks", conn)
    name_map = dict(zip(df_names['symbol'], df_names['name']))

    # 2. 获取所有板块关系并手动脱水
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
        # 应用黑名单过滤
        filtered = [s.replace('行业-','').replace('概念-','') for s in raw_list 
                    if not any(noise in s for noise in config.SECTOR_BLACKLIST)]
        # 取前两个
        sector_map[row['symbol']] = " / ".join(filtered[:2]) if filtered else "其他"
            
    return name_map, sector_map

def save_to_stock_pool(results_list, trade_date):
    """将竞价结果同步至 trading_review.stock_pools"""
    if not results_list: return
    
    now = datetime.datetime.now()
    records = []
    db_status = "竞价异动"

    for item in results_list:
        # 评分逻辑：量比 * 10，最高 100 分
        score = int(np.clip(item['竞价量比'] * 10, 0, 100))
        
        # 构造 JSON 标签
        tags_dict = {
            "strategy": "Auction_Surge",
            "ratio": item['竞价量比'],
            "open_pct": item['竞价涨幅%'],
            "amount_wan": item['成交额(万)']
        }

        records.append({
            'symbol': item['代码'],
            'trade_date': trade_date,
            'stock_name': item['名称'],
            'pool_type': 'short',
            'sector_name': item['所属板块'],
            'score': score,
            'status': db_status,
            'tags': json.dumps(tags_dict, ensure_ascii=False),
            'notes': f"竞价量比达 {item['竞价量比']} 倍，属于早盘资金强力抢筹。",
            'is_watch_focus': 1 if item['竞价量比'] > 10 else 0,
            'watch_level': 2 if item['竞价量比'] > 8 else 1,
            'created_at': now,
            'updated_at': now
        })

    df_save = pd.DataFrame(records)
    try:
        with engine_review.begin() as conn:
            # 物理删除今日旧的“竞价异动”记录
            conn.execute(text("DELETE FROM stock_pools WHERE trade_date = :d AND status = :s"), 
                         {"d": trade_date, "s": db_status})
            # 批量写入
            df_save.to_sql('stock_pools', con=conn, if_exists='append', index=False, chunksize=1000)
        print(f"✅ 成功同步 {len(df_save)} 条竞价异动信号至股票池。")
    except Exception as e:
        print(f"❌ 数据库写入失败: {e}")

def run_auction_pipeline():
    print(f"[{datetime.datetime.now()}] 启动全自动竞价同步系统...")
    
    today = datetime.date.today()
    
    # 1. 提取历史基准 (V5)
    history_sql = """
    SELECT symbol, AVG(volume) as v5_avg
    FROM (
        SELECT symbol, volume,
               ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY trade_time DESC) as rn
        FROM stk_min_kline
        WHERE TIME(trade_time) = '09:30:00' AND DATE(trade_time) < CURDATE()
    ) t
    WHERE rn <= 5 GROUP BY symbol
    """
    with engine_quant.connect() as conn:
        df_v5 = pd.read_sql(text(history_sql), conn)
        df_names = pd.read_sql("SELECT symbol, name FROM stocks", conn)
        name_map = dict(zip(df_names['symbol'], df_names['name']))
        
    if df_v5.empty:
        print("❌ 错误：行情数据库中无分时历史。")
        return

    name_map, sector_map = get_all_metadata(conn)

    v5_map = dict(zip(df_v5['symbol'], df_v5['v5_avg']))
    target_stocks = list(v5_map.keys())

    # 2. 获取实时竞价快照
    xtdata.enable_hello = False
    ticks = xtdata.get_full_tick(target_stocks)
    if not ticks:
        print("❌ 未能获取到 QMT 实时竞价数据。")
        return

    # 3. 计算并关联板块
    all_results = []
    with engine_quant.connect() as conn:
        for symbol, tick in ticks.items():
            base_v = v5_map.get(symbol, 0)
            today_v = tick.get('volume', 0)
            
            if base_v > 0 and today_v > 0:
                ratio = round(today_v / base_v, 2)
                if ratio > 80: ratio /= 100 # 单位修正
                
                # 筛选：量比 > 5 且 价格不低于昨收
                if ratio >= 5.0 and tick.get('lastPrice', 0) >= tick.get('lastClose', 0):
                    # 获取脱水后的双板块
                    clean_sector = sector_map.get(symbol, "其他")
                    
                    all_results.append({
                        '代码': symbol,
                        '名称': name_map.get(symbol, '未知'),
                        '所属板块': clean_sector,
                        '竞价量比': ratio,
                        '竞价涨幅%': round((tick['lastPrice']/tick['lastClose']-1)*100, 2) if tick.get('lastClose') else 0,
                        '成交额(万)': round(tick['amount']/10000, 2)
                    })

    # 4. 执行报告与入库
    if all_results:
        # 控制台报告
        df_report = pd.DataFrame(all_results).sort_values('竞价量比', ascending=False)
        print("\n" + "🏮" * 10 + " 今日竞价抢筹名单 (量比 > 5) " + "🏮" * 10)
        print("-" * 110)
        print(df_report.head(20).to_string(index=False))
        print("-" * 110)
        
        # 存入数据库
        save_to_stock_pool(all_results, today)
    else:
        print("今日未发现符合条件的竞价异动标的。")

if __name__ == "__main__":
    run_auction_pipeline()