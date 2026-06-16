import pandas as pd
from xtquant import xtdata
from sqlalchemy import create_engine, text
import datetime
import json
import sys
import numpy as np

# --- 1. 路径与配置加载 ---
sys.path.append(r"C:\ws\trading-polices\config")
import config  # 导入你的黑名单配置

# --- 1. 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')
engine_review = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')

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
            "amount_wan": item['竞价成交额(万)']
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

def get_auction_sentiment_with_mysql():
    print(f"[{datetime.datetime.now()}] 启动‘MySQL+QMT’联动竞价探测器...")

    # 1. 从 MySQL 提取 5 日历史开盘均量 (V5)
    # 逻辑：提取 trade_time 为 09:30:00 的所有记录，计算最近 5 天的平均值
    print("正在从 MySQL 计算历史开盘基准量...")
    
    # 这里的 SQL 利用窗口函数取出每只股票最近 5 个交易日的开盘首分钟量
    # 注意：TIME(trade_time) = '09:30:00' 对应竞价成交量
    history_sql = """
    SELECT symbol, AVG(volume) as v5_avg
    FROM (
        SELECT symbol, volume,
               ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY trade_time DESC) as rn
        FROM stk_min_kline
        WHERE TIME(trade_time) = '09:30:00'
    ) t
    WHERE rn <= 5
    GROUP BY symbol
    """
    
    with engine.connect() as conn:
        df_v5 = pd.read_sql(text(history_sql), conn)
        name_map, sector_map = get_all_metadata(conn)

    if df_v5.empty:
        print("❌ 错误：MySQL 数据库中没有分时数据，请先同步 stk_min_kline。")
        return

    # 转为字典加速匹配
    v5_map = dict(zip(df_v5['symbol'], df_v5['v5_avg']))
    target_stocks = list(v5_map.keys())

    # 2. 从 QMT 获取今日实时竞价快照 (09:25:00 之后运行)
    print(f"正在获取今日实时竞价数据（监控范围: {len(target_stocks)} 只）...")
    xtdata.enable_hello = False
    ticks = xtdata.get_full_tick(target_stocks)

    if not ticks:
        print("❌ 未能获取实时快照，请确保 QMT 行情灯为绿色。")
        return

    # 3. 计算量比
    results = []
    ratios = []

    for symbol, tick in ticks.items():
        if symbol in v5_map:
            today_v = tick.get('volume', 0)
            base_v = v5_map[symbol]
            
            if base_v > 0:
                ratio = today_v / base_v
                ratios.append(ratio)
                
                # 挖掘抢筹异动
                # 条件：量比 > 10 且 价格 > 昨收
                if ratio > 10.0 and tick.get('lastPrice', 0) > tick.get('lastClose', 0):
                    # name_res = pd.read_sql(f"SELECT name FROM stocks WHERE symbol='{symbol}'", engine)
                    # name = name_res.iloc[0,0] if not name_res.empty else "未知"
                    # --- 修改 B: 直接从内存字典拿数据，不再查库 ---
                    clean_sector = sector_map.get(symbol, "其他")
                    stock_name = name_map.get(symbol, "未知")

                    results.append({
                        '代码': symbol,
                        '名称': stock_name,
                        '所属板块': clean_sector,
                        '竞价量比': round(ratio, 2),
                        '竞价涨幅%': round((tick['lastPrice']/tick['lastClose']-1)*100, 2) if tick.get('lastClose') else 0,
                        '竞价成交额(万)': round(tick['amount']/10000, 2)
                    })

    # 4. 输出市场评估报告
    if ratios:
        market_avg = sum(ratios) / len(ratios)
        print("\n" + "🏮" * 20)
        print(f"📊 大盘竞价热力报告 ({datetime.datetime.now().strftime('%H:%M:%S')})")
        print("-" * 45)
        print(f"🔥 全市场平均竞价量比: {market_avg:.2f}")
        
        # 情绪阈值
        if market_avg > 1.25:
            msg = "【沸腾】资金抢筹积极，做多情绪浓厚。"
        elif market_avg > 0.85:
            msg = "【平稳】多空相对均衡，跟随主线操作。"
        else:
            msg = "【低迷】资金入场意愿弱，防范回落风险。"
            
        print(f"🚩 盘面结论: {msg}")
        print("-" * 45)
        
        if results:
            today = datetime.date.today()
            # 存入数据库
            save_to_stock_pool(results, today)
            
            print("💡 竞价异动 Top 5（主力重金突击）:")
            top_5 = sorted(results, key=lambda x: x['竞价量比'], reverse=True)[:5]
            df_show = pd.DataFrame(top_5)
            print(df_show.to_string(index=False))
        print("🏮" * 20)
    else:
        print("未能计算出有效量比，请检查 QMT 实时连接。")

if __name__ == "__main__":
    get_auction_sentiment_with_mysql()