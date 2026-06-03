import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
from xtquant import xtdata
import datetime
import time
import warnings

warnings.filterwarnings('ignore')
engine_review = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')

def get_sniper_watchlist():
    """
    修正后的两步查询逻辑
    """
    with engine_review.connect() as conn:
        date_query = text("SELECT MAX(trade_date) FROM stock_pools WHERE status = '首板狙击'")
        max_date_res = conn.execute(date_query).fetchone()
        
        if max_date_res is None or max_date_res[0] is None:
            return pd.DataFrame()
        
        target_date = max_date_res[0]
        stock_query = text("SELECT symbol, stock_name as name FROM stock_pools WHERE trade_date = :d AND status = '首板狙击'")
        return pd.read_sql(stock_query, conn, params={"d": target_date}), target_date

def monitor_intraday_v4():
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🚀 启动 V4 实时精准狙击系统...")
    
    watchlist, sig_date = get_sniper_watchlist()
    if watchlist.empty: 
        print("❌ 错误：未发现信号。")
        return
    
    target_symbols = watchlist['symbol'].tolist()
    name_map = dict(zip(watchlist['symbol'], watchlist['name']))
    today_str = datetime.datetime.now().strftime('%Y%m%d')

    # 预热订阅
    xtdata.download_history_data2(target_symbols, period='1m', start_time=today_str)
    for s in target_symbols:
        xtdata.subscribe_quote(s, period='tick', count=0)

    print(f"🎯 正在监控来自 {sig_date} 的名单: {list(name_map.values())}")
    triggered_today = set()

    while True:
        now = datetime.datetime.now()
        if now.time() > datetime.time(15, 0): break
        if datetime.time(11, 31) < now.time() < datetime.time(13, 0):
            time.sleep(30); continue

        # 1. 核心改进：获取全量实时快照 (获取真正的现价)
        realtime_ticks = xtdata.get_full_tick(target_symbols)
        
        # 2. 获取分钟线 (用于计算历史参考位)
        all_min_res = xtdata.get_market_data_ex(['close', 'high', 'low', 'volume', 'amount'], target_symbols, period='1m', count=240)
        
        status_table = []

        for symbol in target_symbols:
            if symbol in triggered_today: continue
            
            # A. 提取实时 Tick 价格
            tick = realtime_ticks.get(symbol)
            if not tick: continue
            curr_p = tick['lastPrice'] # <--- 这才是真正的实时现价
            
            df_min = all_min_res.get(symbol)
            if df_min is None or len(df_min) < 10: continue

            # 时间对齐处理
            times = [str(t).replace('-','').replace(':','').replace(' ','')[-6:] for t in df_min.index]
            df_min['time_key'] = times
            
            # B. 计算均价线 (VWAP)
            df_min['vwap'] = df_min['amount'].cumsum() / (df_min['volume'].cumsum() * 100 + 0.1)
            curr_vwap = df_min['vwap'].iloc[-1]
            
            # C. 锁定 10:00 前的阻力位
            morning_mask = (df_min['time_key'] >= "093000") & (df_min['time_key'] <= "100000")
            morning_df = df_min[morning_mask]
            if morning_df.empty: continue
            m_high = morning_df['high'].max()
            
            # D. 计算回撤深度
            after_10_mask = (df_min['time_key'] > "100000")
            after_10_df = df_min[after_10_mask]
            
            is_washed = False
            wash_depth = 0
            if not after_10_df.empty:
                min_p = after_10_df['low'].min()
                wash_depth = (m_high - min_p) / m_high * 100
                is_washed = wash_depth > 1.2  # 洗盘回调阈值

            # E. 判定触发 (使用 Tick 价格进行判断)
            is_above_vwap = curr_p >= curr_vwap
            # 突破判定：实时价 > 历史高点
            is_breaking = (curr_p >= m_high)
            
            # 计算距离突破位的即时距离
            dist_to_break = (curr_p - m_high) / m_high * 100

            if is_washed and is_above_vwap and is_breaking:
                print(f"\n🔥 狙击信号触发！【{name_map[symbol]}】实时价: {curr_p} 突破阻力: {m_high}")
                triggered_today.add(symbol)
            
            status_table.append({
                '名称': name_map[symbol],
                '实时现价': curr_p,  # 现在显示的是 Tick 价
                '阻力位': m_high,
                '均价线': round(curr_vwap, 2),
                '回踩深度%': round(wash_depth, 2),
                '距突破点%': round(dist_to_break, 2),
                '均线': "✅" if is_above_vwap else "❌",
                '洗盘': "✅" if is_washed else "⏳"
            })

        # --- 打印实时看板 ---
        if status_table:
            print("\033[H\033[J") 
            print(f"--- 实时狙击监控 ({now.strftime('%H:%M:%S')}) | 信号源: {sig_date} ---")
            print(pd.DataFrame(status_table).to_string(index=False))
            print("-" * 75)

        time.sleep(5) # 提高刷新频率到 5 秒

if __name__ == "__main__":
    monitor_intraday_v4()