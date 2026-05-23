import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def analyze_market_tolerance():
    print(f"[{datetime.datetime.now()}] 正在扫描全市场‘市场容错率’效应...")

    with engine.connect() as conn:
        # 1. 获取最近两个交易日
        date_res = conn.execute(text("SELECT DISTINCT trade_date FROM stk_daily_kline ORDER BY trade_date DESC LIMIT 2")).fetchall()
        if len(date_res) < 2: 
            print("数据不足。")
            return
        today, yesterday = date_res[0][0], date_res[1][0]

        # 2. 识别【昨日】炸板股
        # 我们只从昨日数据中提取 symbol, yest_close, yest_high
        yest_sql = text("""
            SELECT symbol, close as yest_close, high as yest_high,
                   (SELECT close FROM stk_daily_kline WHERE symbol = k.symbol AND trade_date < :yest ORDER BY trade_date DESC LIMIT 1) as pre_close
            FROM stk_daily_kline k
            WHERE trade_date = :yest
              AND (symbol LIKE '60%' OR symbol LIKE '00%')
        """)
        df_yest_raw = pd.read_sql(yest_sql, conn, params={"yest": yesterday})
        
        # 判定昨日涨停价
        df_yest_raw['limit_p'] = (df_yest_raw['pre_close'] * 1.098).astype(float).round(2)
        
        # 筛选昨日炸板股条件：最高触及涨停，但收盘跌破涨停，且昨日涨幅 > 2% (排除冲高回落的烂板)
        yest_signals = df_yest_raw[
            (df_yest_raw['yest_high'].astype(float) >= df_yest_raw['limit_p']) & 
            (df_yest_raw['yest_close'].astype(float) < df_yest_raw['limit_p']) &
            (df_yest_raw['yest_close'].astype(float) > df_yest_raw['pre_close'].astype(float) * 1.02)
        ].copy()

        if yest_signals.empty:
            print(f"昨日 ({yesterday}) 无显著炸板标的，跳过分析。")
            return

        # 3. 提取这些标的【今日】的表现
        # 修正点：今日查询只取 symbol 和 today_close，绝不取重复列名
        yest_symbols = yest_signals['symbol'].tolist()
        today_sql = text("""
            SELECT symbol, close as today_close, high as today_high
            FROM stk_daily_kline 
            WHERE trade_date = :today AND symbol IN :symbols
        """)
        df_today = pd.read_sql(today_sql, conn, params={"today": today, "symbols": yest_symbols})

        # 4. 合并数据
        # 因为 df_today 只有 today_close，合并后 yest_close 依然保持原名
        df_eval = pd.merge(yest_signals, df_today, on='symbol')

        # 强制转换为 float 避免 Decimal 运算错误
        df_eval['today_close'] = df_eval['today_close'].astype(float)
        df_eval['yest_close'] = df_eval['yest_close'].astype(float)
        df_eval['yest_high'] = df_eval['yest_high'].astype(float)

        # 5. 计算指标
        # 修复收益率计算
        df_eval['ret'] = (df_eval['today_close'] - df_eval['yest_close']) / df_eval['yest_close']
        
        avg_repair_ret = df_eval['ret'].mean() * 100
        # 反包率：今日收盘价超过昨日最高价（炸板位）
        reverse_rate = (df_eval['today_close'] >= df_eval['yest_high']).mean() * 100
        # 亏钱率：今日跌幅超过 5%
        kill_rate = (df_eval['ret'] <= -0.05).mean() * 100

    # 6. 评分模型 (100分制)
    score = 0
    score += np.clip((avg_repair_ret + 1) / 3 * 40, 0, 40) # 修复分
    score += np.clip(reverse_rate / 30 * 30, 0, 30)        # 反包分
    score += np.clip(30 - (kill_rate * 3), 0, 30)          # 风险分扣除
    
    score = round(np.clip(score, 0, 100), 1)

    # 7. 结果展示
    print("\n" + "🛡️" * 15)
    print(f"📊 A股【市场容错率】分析报告 ({today})")
    print(f"📌 观察样本：昨日炸板股共 {len(df_eval)} 只")
    print("-" * 40)
    print(f"✅ 平均修复强度: {avg_repair_ret:+.2f}%")
    print(f"🔄 强势反包比例: {reverse_rate:.1f}%")
    print(f"💀 暴力杀跌比例: {kill_rate:.1f}%")
    print("-" * 40)
    print(f"🌡️ 市场容错分: {score}")

    if score >= 75:
        msg = "🟢 极高：炸板大概率反包，情绪处于亢奋期。建议：大胆参与热点分歧。"
    elif score >= 50:
        msg = "🟡 中等：个股分化。建议：只看核心龙头的修复，跟风股不碰。"
    else:
        msg = "🔴 危险：炸板即跌停，容错率为负。建议：严格控制仓位，严禁追高。"
    print(f"🚩 结论: {msg}")
    print("🛡️" * 15 + "\n")

if __name__ == "__main__":
    analyze_market_tolerance()