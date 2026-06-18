import pandas as pd
from xtquant import xtdata
from sqlalchemy import create_engine, text
import datetime
import sys
import numpy as np

# --- 1. 数据库配置（仅用于读取历史基准量 V5） ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def get_auction_sentiment_report():
    print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🚀 正在生成全市场竞价热力报告...")

    # 1. 从 MySQL 提取全市场 5 日历史开盘均量 (V5) 作为基准
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
    
    try:
        with engine.connect() as conn:
            df_v5 = pd.read_sql(text(history_sql), conn)
    except Exception as e:
        print(f"❌ 数据库读取失败: {e}")
        return

    if df_v5.empty:
        print("❌ 错误：MySQL 中没有足够的历史数据来计算 V5 基准量。")
        return

    v5_map = dict(zip(df_v5['symbol'], df_v5['v5_avg']))
    target_stocks = list(v5_map.keys())

    # 2. 从 QMT 获取今日实时竞价快照 (请在 09:25:01 之后运行)
    xtdata.enable_hello = False
    ticks = xtdata.get_full_tick(target_stocks)

    if not ticks:
        print("❌ 未能获取实时快照，请检查 QMT 行情连接状态。")
        return

    # 3. 计算全市场统计指标
    all_ratios = []
    total_amount = 0
    up_count = 0
    down_count = 0
    flat_count = 0
    total_valid_stocks = 0

    for symbol, tick in ticks.items():
        if symbol in v5_map:
            today_v = tick.get('volume', 0)
            base_v = v5_map[symbol]
            last_close = tick.get('lastClose', 0)
            last_price = tick.get('lastPrice', 0)
            
            # 过滤掉停牌或无交易数据的股票
            if base_v > 0 and last_close > 0 and today_v > 0:
                ratio = today_v / base_v
                all_ratios.append(ratio)
                
                total_amount += tick.get('amount', 0)
                total_valid_stocks += 1
                
                # 计算涨跌分布
                change_pct = (last_price / last_close - 1) * 100
                if change_pct > 0.05: # 略微考虑滑点，0.05% 以上计入红盘
                    up_count += 1
                elif change_pct < -0.05:
                    down_count += 1
                else:
                    flat_count += 1

    # 4. 输出最终热力报告
    if all_ratios:
        market_avg_ratio = np.mean(all_ratios)
        market_median_ratio = np.median(all_ratios)
        total_amount_亿 = total_amount / 100000000
        up_rate = (up_count / total_valid_stocks) * 100

        print("\n" + "🏮" * 25)
        print(f"📊 A股竞价热力报告 ({datetime.datetime.now().strftime('%H:%M:%S')})")
        print("-" * 50)
        
        # 指标 A: 活跃度
        print(f"🔹 全市场平均竞价量比: {market_avg_ratio:.2f}")
        print(f"🔹 全市场量比中位数:   {market_median_ratio:.2f}")
        
        # 指标 B: 资金参与度
        print(f"🔹 竞价成交总金额:     {total_amount_亿:.2f} 亿元")
        
        # 指标 C: 涨跌强度
        print(f"🔹 竞价红盘率:         {up_rate:.1f}%")
        print(f"🔹 涨跌分布: 📈红盘({up_count}) | 📉绿盘({down_count}) | ⚪平盘({flat_count})")
        
        print("-" * 50)
        
        # 情绪综合评定逻辑
        if market_avg_ratio > 1.3 and up_rate > 65 and total_amount_亿 > 40:
            sentiment = "🔥 极度亢奋（资金疯狂抢筹）"
        elif market_avg_ratio > 1.0 and up_rate > 50:
            sentiment = "⭐ 情绪活跃（多头占优）"
        elif market_avg_ratio < 0.8 and up_rate < 40:
            sentiment = "❄️ 情绪低迷（资金观望为主）"
        else:
            sentiment = "🌀 情绪平淡（多空均衡）"
            
        print(f"🚩 盘面结论: {sentiment}")
        print("-" * 50)
        print("💡 注：量比基于过去 5 日竞价成交量计算；成交总额反映全市场资金参与热度。")
        print("🏮" * 25 + "\n")
    else:
        print("未能计算出有效量比数据，请确认当前处于交易时段或 QMT 数据已更新。")

if __name__ == "__main__":
    get_auction_sentiment_report()