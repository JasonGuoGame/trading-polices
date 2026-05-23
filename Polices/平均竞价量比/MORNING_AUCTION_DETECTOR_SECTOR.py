import pandas as pd
from xtquant import xtdata
from sqlalchemy import create_engine, text
import datetime

# --- 1. 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def get_board_name(symbol):
    """根据代码前缀判断板块"""
    if symbol.startswith(('300', '301')):
        return '创业板'
    elif symbol.startswith('688'):
        return '科创板'
    elif symbol.startswith(('60', '00')):
        return '沪深主板'
    else:
        return '其他'

def get_auction_sentiment_with_mysql():
    print(f"[{datetime.datetime.now()}] 启动分板块‘竞价量比’探测器...")

    # 1. 从 MySQL 提取 5 日历史开盘均量 (V5)
    print("正在从 MySQL 计算历史开盘基准量...")
    history_sql = """
    SELECT symbol, AVG(volume) as v5_avg
    FROM (
        SELECT symbol, volume,
               ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY trade_time DESC) as rn
        FROM stk_min_kline
        WHERE TIME(trade_time) = '09:30:00'
          AND DATE(trade_time) < CURDATE()
    ) t
    WHERE rn <= 5
    GROUP BY symbol
    """
    
    with engine.connect() as conn:
        df_v5 = pd.read_sql(text(history_sql), conn)
        # 预先加载所有股票名称，避免循环内重复查询数据库
        df_names = pd.read_sql("SELECT symbol, name FROM stocks", conn)
        name_map = dict(zip(df_names['symbol'], df_names['name']))
    
    if df_v5.empty:
        print("❌ 错误：MySQL 中无分时数据。")
        return

    v5_map = dict(zip(df_v5['symbol'], df_v5['v5_avg']))
    target_stocks = list(v5_map.keys())

    # 2. 从 QMT 获取今日实时竞价快照
    print(f"获取今日竞价快照 (范围: {len(target_stocks)} 只)...")
    xtdata.enable_hello = False
    ticks = xtdata.get_full_tick(target_stocks)

    if not ticks:
        print("❌ 未能获取实时快照。")
        return

    # 3. 计算量比并归类
    all_results = []

    for symbol, tick in ticks.items():
        if symbol in v5_map:
            today_v = tick.get('volume', 0)
            base_v = v5_map[symbol]
            
            if base_v > 0 and today_v > 0:
                ratio = today_v / base_v
                # 单位修正逻辑：如果量比异常大（如>50），通常是手/股单位不统一
                if ratio > 50: ratio = ratio / 100

                # 只要价格不低于昨收的个股（排除利空低开）
                if tick.get('lastPrice', 0) >= tick.get('lastClose', 0):
                    board = get_board_name(symbol)
                    all_results.append({
                        '代码': symbol,
                        '名称': name_map.get(symbol, '未知'),
                        '板块': board,
                        '竞价量比': round(ratio, 2),
                        '竞价涨幅%': round((tick['lastPrice']/tick['lastClose']-1)*100, 2) if tick.get('lastClose') else 0,
                        '成交额(万)': round(tick['amount']/10000, 2)
                    })

    if not all_results:
        print("未能计算出有效数据。")
        return

    # 转为 DataFrame 进行分组处理
    df_res = pd.DataFrame(all_results)

    # 4. 输出各板块报告
    print("\n" + "🏮" * 25)
    print(f"🚀 全市场竞价热力分布报告 ({datetime.datetime.now().strftime('%H:%M:%S')})")
    print("-" * 50)

    boards = ['沪深主板', '创业板', '科创板']
    
    for b_name in boards:
        b_df = df_res[df_res['板块'] == b_name]
        
        if b_df.empty:
            print(f"\n【{b_name}】: 今日无显著异动。")
            continue

        # 计算该板块平均量比
        avg_ratio = b_df['竞价量比'].mean()
        print(f"\n🔹 {b_name} | 平均竞价量比: {avg_ratio:.2f}")
        
        # 提取 Top 5
        top_5 = b_df.sort_values('竞价量比', ascending=False).head(5)
        print(top_5[['代码', '名称', '竞价量比', '竞价涨幅%', '成交额(万)']].to_string(index=False))
        print("." * 50)

    print("\n" + "🏮" * 25)

if __name__ == "__main__":
    get_auction_sentiment_with_mysql()