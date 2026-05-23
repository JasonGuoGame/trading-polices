import pandas as pd
from xtquant import xtdata
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def get_board_name(symbol):
    if symbol.startswith(('300', '301')): return '创业板'
    elif symbol.startswith('688'): return '科创板'
    elif symbol.startswith(('60', '00')): return '沪深主板'
    else: return '其他'

def get_low_level_auction_explosion():
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 启动‘双表联动’低位爆量扫描...")
    xtdata.enable_hello = False

    # 1. 步骤一：从日线表提取 120 日高低价区间
    print("正在从 stk_daily_kline 计算 120 日价格区间...")
    daily_sql = """
    SELECT symbol, MAX(high) as high_120, MIN(low) as low_120
    FROM (
        SELECT symbol, high, low,
               ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY trade_date DESC) as rn
        FROM stk_daily_kline
    ) t
    WHERE rn <= 120
    GROUP BY symbol
    """
    
    # 2. 步骤二：从分时表提取“昨日”集合竞价成交量
    print("正在从 stk_min_kline 提取昨日竞价基准...")
    minute_sql = """
    SELECT symbol, volume as yest_auction_vol
    FROM stk_min_kline
    WHERE trade_time = (
        SELECT MAX(trade_time) 
        FROM stk_min_kline 
        WHERE TIME(trade_time) = '09:30:00' AND DATE(trade_time) < CURDATE()
    )
    """

    try:
        with engine.connect() as conn:
            df_daily = pd.read_sql(text(daily_sql), conn)
            df_minute = pd.read_sql(text(minute_sql), conn)
            # 预加载名称
            df_names = pd.read_sql("SELECT symbol, name FROM stocks", conn)
            name_map = dict(zip(df_names['symbol'], df_names['name']))
    except Exception as e:
        print(f"数据库读取失败: {e}")
        return

    # 3. 合并两表数据到主字典
    # 将日线区间和分时量能合并到一个 map 中
    combined_df = pd.merge(df_daily, df_minute, on='symbol', how='inner')
    hist_map = combined_df.set_index('symbol').to_dict('index')
    target_stocks = list(hist_map.keys())

    if not target_stocks:
        print("❌ 错误：未能通过双表关联锁定有效股票池。")
        return

    # 4. 获取今日实时快照 (09:25 后运行)
    print(f"正在读取今日实时快照 (分析范围: {len(target_stocks)} 只)...")
    ticks = xtdata.get_full_tick(target_stocks)
    
    all_results = []

    for symbol, tick in ticks.items():
        if symbol in hist_map:
            h_data = hist_map[symbol]
            today_v = tick.get('volume', 0)
            yest_v = h_data['yest_auction_vol']
            curr_p = tick.get('lastPrice', 0)
            
            if yest_v > 0 and curr_p > 0:
                # A. 计算今日/昨日竞价爆量倍数
                vol_ratio = today_v / yest_v
                # 单位修正逻辑 (QMT 特色股/手对齐)
                if vol_ratio > 80: vol_ratio /= 100
                elif vol_ratio < 0.01: vol_ratio *= 100
                
                # B. 计算价格位置分 (基于 120 日日线数据)
                l_120 = h_data['low_120']
                h_120 = h_data['high_120']
                
                if h_120 > l_120:
                    position_score = (curr_p - l_120) / (h_120 - l_120)
                else:
                    position_score = 1.0

                # --- 核心筛选门槛 ---
                # 1. 爆量倍数 >= 2.0
                # 2. 价格位置 <= 0.35 (处于半年波动区间的底部 35% 位置)
                # 3. 价格不低于昨收（红盘或平开）
                if vol_ratio >= 2.0 and position_score <= 0.35 and curr_p >= tick.get('lastClose', 0):
                    all_results.append({
                        '代码': symbol,
                        '名称': name_map.get(symbol, '未知'),
                        '板块': get_board_name(symbol),
                        '爆量倍数': round(vol_ratio, 2),
                        '位置分': round(position_score, 2),
                        '竞价涨幅%': round((curr_p/tick['lastClose']-1)*100, 2) if tick.get('lastClose') else 0,
                        '今日成交(万)': round(tick['amount']/10000, 2)
                    })

    if not all_results:
        print("今日暂未发现符合【低位+爆量】特征的个股。")
        return

    # 5. 分板块排序并输出
    df_res = pd.DataFrame(all_results)
    
    print("\n" + "💎" * 30)
    print(f"🚀 今日【双表联动】低位潜力+竞价爆量选股报告")
    print("逻辑：日线 120 日低位 + 分时昨日竞价爆量对比")
    print("-" * 85)

    boards = ['沪深主板', '创业板', '科创板']
    for b in boards:
        b_df = df_res[df_res['板块'] == b].sort_values('爆量倍数', ascending=False).head(10)
        if not b_df.empty:
            print(f"\n📍 {b} 爆量前 10 名:")
            print(b_df[['代码', '名称', '爆量倍数', '位置分', '竞价涨幅%', '今日成交(万)']].to_string(index=False))
            print("." * 85)

    print("\n" + "💎" * 30)

if __name__ == "__main__":
    get_low_level_auction_explosion()


# 0.1 左右：处于地板价。如果此时爆量，极大概率是主力“挖坑”结束后的第一笔吸筹。
# 0.3 左右：底部横盘期。如果爆量，是**“平台突破”**的信号