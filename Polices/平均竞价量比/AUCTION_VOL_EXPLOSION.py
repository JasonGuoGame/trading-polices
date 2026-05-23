import pandas as pd
from xtquant import xtdata
from sqlalchemy import create_engine, text
import datetime

# --- 1. 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def get_board_name(symbol):
    """板块分类逻辑"""
    if symbol.startswith(('300', '301')): return '创业板'
    elif symbol.startswith('688'): return '科创板'
    elif symbol.startswith(('60', '00')): return '沪深主板'
    else: return '其他'

def get_auction_explosion():
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 正在对比今日与昨日集合竞价量能...")
    xtdata.enable_hello = False

    # 1. 从 MySQL 获取“昨日”集合竞价成交量 (标记为 09:30:00 的 K 线)
    # 我们精准提取数据库中最近一个交易日的 09:30 数据
    history_sql = """
    SELECT symbol, volume as yest_vol
    FROM stk_min_kline
    WHERE trade_time = (
        SELECT MAX(trade_time) 
        FROM stk_min_kline 
        WHERE TIME(trade_time) = '09:30:00' 
          AND DATE(trade_time) < CURDATE()
    )
    """
    
    with engine.connect() as conn:
        df_yest = pd.read_sql(text(history_sql), conn)
        # 预加载名称映射
        df_names = pd.read_sql("SELECT symbol, name FROM stocks", conn)
        name_map = dict(zip(df_names['symbol'], df_names['name']))
    
    if df_yest.empty:
        print("❌ 错误：数据库中没有昨天的分时数据。")
        return

    yest_vol_map = dict(zip(df_yest['symbol'], df_yest['yest_vol']))
    target_stocks = list(yest_vol_map.keys())

    # 2. 获取“今日”开盘竞价快照
    # 策略：09:25 - 09:30 之间直接用 Tick 的 Volume
    print(f"正在读取今日实时快照 (监控范围: {len(target_stocks)} 只)...")
    ticks = xtdata.get_full_tick(target_stocks)

    if not ticks:
        print("❌ 无法获取实时数据。")
        return

    all_results = []

    for symbol, tick in ticks.items():
        if symbol in yest_vol_map:
            # 今日量（09:30前，volume字段即为竞价量）
            today_v = tick.get('volume', 0)
            yest_v = yest_vol_map[symbol]
            
            if yest_v > 0 and today_v > 0:
                # 计算爆量倍数 (今日竞价 / 昨日竞价)
                ratio = today_v / yest_v
                
                # QMT 数据单位自动对齐逻辑 (防止 100 倍误差)
                if ratio > 80: ratio = ratio / 100
                elif ratio < 0.01: ratio = ratio * 100

                # 筛选条件：1. 爆量 2. 价格不低于昨收（排除砸盘）
                if ratio >= 1.0 and tick.get('lastPrice', 0) >= tick.get('lastClose', 0):
                    all_results.append({
                        '代码': symbol,
                        '名称': name_map.get(symbol, '未知'),
                        '板块': get_board_name(symbol),
                        '爆量倍数': round(ratio, 2),
                        '竞价涨幅%': round((tick['lastPrice']/tick['lastClose']-1)*100, 2) if tick.get('lastClose') else 0,
                        '今日竞价额(万)': round(tick['amount']/10000, 2)
                    })

    if not all_results:
        print("未能计算出今日爆量数据。")
        return

    df_res = pd.DataFrame(all_results)

    # 3. 分板块提取前 10 名
    print("\n" + "🔥" * 25)
    print(f"🚀 今日【竞价爆量抢筹】TOP 10 榜单 (对比昨日竞价)")
    print("-" * 50)

    boards = ['沪深主板', '创业板', '科创板']
    for b in boards:
        # 过滤板块并排序
        b_top10 = df_res[df_res['板块'] == b].sort_values('爆量倍数', ascending=False).head(10)
        
        if b_top10.empty:
            continue
            
        print(f"\n💎 {b} 爆量前10名:")
        print("-" * 65)
        # 重置索引并打印
        print(b_top10[['代码', '名称', '爆量倍数', '竞价涨幅%', '今日竞价额(万)']].to_string(index=False))
    
    print("\n" + "🔥" * 25)

if __name__ == "__main__":
    # 建议在 09:25:30 以后运行效果最佳
    get_auction_explosion()