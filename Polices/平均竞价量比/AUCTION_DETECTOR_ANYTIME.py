import pandas as pd
from xtquant import xtdata
from sqlalchemy import create_engine, text
import datetime
import time

# --- 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def get_board_name(symbol):
    if symbol.startswith(('300', '301')): return '创业板'
    elif symbol.startswith('688'): return '科创板'
    elif symbol.startswith(('60', '00')): return '沪深主板'
    else: return '其他'

def get_auction_sentiment_anytime():
    print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 启动全天候竞价量比分析...")
    xtdata.enable_hello = False

    # 1. 历史基准提取 (保持不变)
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
    with engine.connect() as conn:
        df_v5 = pd.read_sql(text(history_sql), conn)
        df_names = pd.read_sql("SELECT symbol, name FROM stocks", conn)
        name_map = dict(zip(df_names['symbol'], df_names['name']))
    
    if df_v5.empty:
        print("❌ 错误：数据库中无历史分时数据。")
        return

    v5_map = dict(zip(df_v5['symbol'], df_v5['v5_avg']))
    target_stocks = list(v5_map.keys())

    # 2. 获取今日竞价量 (Today_V) - 增强防错版
    now = datetime.datetime.now()
    current_time = now.strftime("%H:%M:%S")
    today_str = now.strftime("%Y%m%d")
    today_v_map = {}

    if current_time < "09:25:00":
        print("⏳ 还没开盘。")
        return

    # 情况 A：竞价窗口期 (09:25 - 09:31)
    if "09:25:00" <= current_time < "09:31:00":
        print(">>> 正在读取实时竞价快照...")
        ticks = xtdata.get_full_tick(target_stocks)
        for sym, tick in ticks.items():
            today_v_map[sym] = tick.get('volume', 0)

    # 情况 B：盘中或收盘后 (09:31 以后)
    else:
        print(">>> 正在回溯今日第一根分时 K 线...")
        # 批量尝试下载今日数据
        xtdata.download_history_data2(target_stocks, period='1m', start_time=today_str)
        time.sleep(1) # 给下载留一点点缓冲时间

        # --- 核心改进：安全地获取字典键 ---
        res = xtdata.get_market_data_ex(
            field_list=['volume'],
            stock_list=target_stocks,
            period='1m',
            start_time=today_str + '093000',
            end_time=today_str + '093000'
        )
        
        # 检查 'volume' 键是否存在
        if not res or 'volume' not in res:
            print("⚠️ 警告：QMT 未能返回今日 'volume' 数据，可能是还没同步到本地。")
            # 尝试降级到 Tick
            ticks = xtdata.get_full_tick(target_stocks)
            for sym, tick in ticks.items():
                # 即使在盘中，如果没数，我们也只能猜
                today_v_map[sym] = tick.get('volume', 0) 
        else:
            today_data = res['volume']
            for sym in target_stocks:
                if sym in today_data and not today_data[sym].empty:
                    df_t = today_data[sym]
                    # 匹配 09:30:00 那一秒
                    match = df_t[df_t.index.map(lambda x: pd.to_datetime(x, unit='ms').strftime('%H:%M:%S') == '09:30:00')]
                    if not match.empty:
                        today_v_map[sym] = match['volume'].iloc[0]

    # 3. 计算与对齐 (加入单位修正)
    final_results = []
    ratios = []

    for sym in target_stocks:
        base_v = v5_map.get(sym, 0)
        today_v = today_v_map.get(sym, 0)
        
        if base_v > 0 and today_v > 0:
            ratio = today_v / base_v
            # 自动修复 QMT 手/股单位不统一 (100倍误差)
            if ratio > 50: ratio /= 100
            elif ratio < 0.02: ratio *= 100

            ratios.append(ratio)
            if ratio > 1.5:
                final_results.append({
                    '代码': sym,
                    '名称': name_map.get(sym, '未知'),
                    '板块': get_board_name(sym),
                    '竞价量比': round(ratio, 2)
                })

    # 4. 输出
    if ratios:
        avg_market = sum(ratios) / len(ratios)
        print("\n" + "🏮" * 20)
        print(f"📊 大盘竞价温度计 | 全市场平均量比: {avg_market:.2f}")
        print("-" * 50)
        
        if final_results:
            df_res = pd.DataFrame(final_results).sort_values('竞价量比', ascending=False)
            print("💡 竞价抢筹 Top 10:")
            print(df_res.head(10).to_string(index=False))
        print("🏮" * 20)
    else:
        print("未发现有效今日数据。")

if __name__ == "__main__":
    get_auction_sentiment_anytime()