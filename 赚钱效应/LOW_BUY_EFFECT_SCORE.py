import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def analyze_low_buy_effect():
    print(f"[{datetime.datetime.now()}] 正在分析‘低吸模式’赚钱效应...")

    with engine.connect() as conn:
        # 1. 获取最近三个交易日 (t0:前天, t1:昨天, t2:今天)
        date_res = conn.execute(text("SELECT DISTINCT trade_date FROM stk_daily_kline ORDER BY trade_date DESC LIMIT 3")).fetchall()
        if len(date_res) < 3: return
        t2_today = date_res[0][0]
        t1_yesterday = date_res[1][0]
        t0_prev_day = date_res[2][0]

        # 2. 识别【昨日】符合低吸形态的股票
        # 条件：下影线占比高 且 盘中跌幅深
        low_buy_candidates_sql = text("""
            SELECT symbol, open, high, low, close, 
                   (SELECT close FROM stk_daily_kline WHERE symbol = k.symbol AND trade_date = :t0) as prev_close
            FROM stk_daily_kline k
            WHERE trade_date = :t1
              AND (symbol LIKE '60%' OR symbol LIKE '00%' OR symbol LIKE '30%')
        """)
        df_yest = pd.read_sql(low_buy_candidates_sql, conn, params={"t1": t1_yesterday, "t0": t0_prev_day})
        
        # 计算昨日形态
        df_yest['range'] = df_yest['high'] - df_yest['low']
        df_yest['body'] = (df_yest['close'] - df_yest['open']).abs()
        df_yest['lower_shadow'] = df_yest[['open', 'close']].min(axis=1) - df_yest['low']
        df_yest['max_dip'] = (df_yest['low'] - df_yest['prev_close']) / df_yest['prev_close']
        
        # 判定昨日为“低吸机会”的标准：
        # 1. 下影线是实体 2 倍以上 且 占全天振幅 60% 以上
        # 2. 或者盘中跌幅曾超过 -4%
        is_needle = (df_yest['lower_shadow'] > df_yest['body'] * 2) & (df_yest['lower_shadow'] / df_yest['range'] > 0.6)
        is_deep_water = df_yest['max_dip'] < -0.04
        
        yest_signals = df_yest[is_needle | is_deep_water].copy()
        
        if yest_signals.empty:
            print("昨日未发现明显的低吸形态标的。")
            return

        # 3. 追踪这些标的【今日】的表现
        yest_symbols = yest_signals['symbol'].tolist()
        today_sql = text("SELECT symbol, open, close, high, low FROM stk_daily_kline WHERE trade_date = :t2 AND symbol IN :symbols")
        df_today = pd.read_sql(today_sql, conn, params={"t2": t2_today, "symbols": yest_symbols})

        # 合并分析
        df_eval = pd.merge(yest_signals, df_today, on='symbol', suffixes=('_yest', '_today'))
        
        # --- 计算核心得分指标 ---
        # 1. 今日胜率 (今日收红)
        win_rate = (df_eval['close_today'] > df_eval['close_yest']).mean()
        # 2. 平均收益率
        avg_ret = (df_eval['close_today'] - df_eval['close_yest']) / df_eval['close_yest']
        # 3. 反包率 (今日收盘突破昨日最高点)
        fan_bao_rate = (df_eval['close_today'] > df_eval['high_yest']).mean()
        # 4. 亏损保护 (今日跌幅超过 -3% 的比例，越少得分越高)
        big_loss_rate = (df_eval['close_today'] / df_eval['close_yest'] - 1 < -0.03).mean()

    # --- 4. 评分模型 (100分制) ---
    score = (win_rate * 40) + (fan_bao_rate * 30) + (np.clip(avg_ret.mean() * 100, -5, 5) * 4) + ((1 - big_loss_rate) * 10)
    score = np.clip(score, 0, 100)

    # 结果输出
    print("\n" + "🧘" * 15)
    print(f"📊 A股【低吸模式】赚钱效应分析")
    print(f"📅 统计样本：昨日有 {len(yest_signals)} 只个股符合低吸形态")
    print("-" * 40)
    print(f"✅ 今日上涨比例 (胜率): {win_rate*100:.1f}%")
    print(f"🚀 今日反包比例: {fan_bao_rate*100:.1f}%")
    print(f"💰 平均套利收益: {avg_ret.mean()*100:+.2f}%")
    print(f"❌ 再次大跌比例: {big_loss_rate*100:.1f}%")
    print("-" * 40)
    print(f"🌡️ 低吸模式得分: {score:.1f}")

    if score >= 70:
        conclusion = "🟢 强：低吸第二天经常吃肉！市场承接力极强，跌下去就是买点。"
    elif score >= 45:
        conclusion = "🟡 中：震荡格局。部分标的能反包，部分标的横盘，需精选主线。"
    else:
        conclusion = "🔴 弱：低吸继续大跌！千万别抄底，恐慌盘还没出尽，接飞刀必亏。"

    print(f"🚩 结论: {conclusion}")
    print("🧘" * 15 + "\n")

if __name__ == "__main__":
    analyze_low_buy_effect()