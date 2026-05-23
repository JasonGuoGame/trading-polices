import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def analyze_limit_up_sentiment():
    print(f"[{datetime.datetime.now()}] 正在扫描全市场‘连板接力’情绪...")

    with engine.connect() as conn:
        # 1. 获取最近两个交易日
        date_res = conn.execute(text("SELECT DISTINCT trade_date FROM stk_daily_kline ORDER BY trade_date DESC LIMIT 2")).fetchall()
        if len(date_res) < 2: return
        today, yesterday = date_res[0][0], date_res[1][0]

        # 2. 获取今日和昨日的行情（仅限主板，因为你的连板规则基于10%）
        query_sql = text("""
            SELECT k.symbol, k.trade_date, k.open, k.high, k.low, k.close,
                   (SELECT close FROM stk_daily_kline WHERE symbol = k.symbol AND trade_date < k.trade_date ORDER BY trade_date DESC LIMIT 1) as prev_close
            FROM stk_daily_kline k
            WHERE k.trade_date IN (:t, :y)
              AND (k.symbol LIKE '60%' OR k.symbol LIKE '00%')
        """)
        df = pd.read_sql(query_sql, conn, params={"t": today, "y": yesterday})

    # 数据预处理
    df['limit_up_price'] = (df['prev_close'] * 1.098).round(2)
    df['is_lu'] = df['close'] >= df['limit_up_price']
    df['is_touch_lu'] = df['high'] >= df['limit_up_price']
    df['is_ld'] = df['close'] <= (df['prev_close'] * 0.902).round(2)

    # 分离今日和昨日数据
    df_today = df[df['trade_date'] == today].copy()
    df_yest = df[df['trade_date'] == yesterday].copy()

    # --- 指标计算 ---
    
    # 1. 涨停总数
    lu_count = df_today['is_lu'].sum()
    
    # 2. 炸板率
    touch_count = df_today['is_touch_lu'].sum()
    exploded_count = touch_count - lu_count
    exploded_rate = (exploded_count / touch_count * 100) if touch_count > 0 else 0
    
    # 3. 晋级率 (昨日涨停今日继续涨停)
    yest_lu_symbols = df_yest[df_yest['is_lu']]['symbol'].tolist()
    today_lu_symbols = df_today[df_today['is_lu']]['symbol'].tolist()
    promoted_stocks = set(yest_lu_symbols).intersection(set(today_lu_symbols))
    promotion_rate = (len(promoted_stocks) / len(yest_lu_symbols) * 100) if yest_lu_symbols else 0
    
    # 4. 连板高度 (这里简化处理，只查昨日涨停今日晋级的股票)
    # 若需精确高度，需递归查前天。此处用晋级表现作为权重。
    max_height = 2 if len(promoted_stocks) > 0 else (1 if lu_count > 0 else 0)
    # (建议结合之前的连板高度脚本获取真实高度，这里假设为 H)
    
    # 5. 负反馈：昨日涨停今日跌停 (大面)
    yest_lu_today_ld = df_today[df_today['symbol'].isin(yest_lu_symbols) & df_today['is_ld']]
    death_count = len(yest_lu_today_ld)

    # --- 3. 评分模型 (100分制) ---
    score = 0
    
    # A. 数量分 (30分)：>80家满分
    score += np.clip(lu_count / 80 * 30, 0, 30)
    
    # B. 晋级分 (30分)：晋级率 > 40% 满分
    score += np.clip(promotion_rate / 40 * 30, 0, 30)
    
    # C. 炸板扣分 (20分)：炸板率 < 10% 满分，每增加1%扣0.5分
    score += np.clip(20 - (exploded_rate - 10) * 0.5, 0, 20)
    
    # D. 稳定性分 (20分)：每出现一家涨停转跌停扣5分
    score += np.clip(20 - (death_count * 5), 0, 20)

    # --- 4. 结果输出 ---
    print("\n" + "🚩" * 15)
    print(f"📊 A股【连板接力】情绪打分报告")
    print(f"📅 日期: {today}")
    print("-" * 40)
    print(f"📈 涨停数量: {lu_count:<4} (分值贡献: {np.clip(lu_count/80*30,0,30):.1f}/30)")
    print(f"🚀 接力晋级: {promotion_rate:.1f}% (分值贡献: {np.clip(promotion_rate/40*30,0,30):.1f}/30)")
    print(f"💣 炸板比率: {exploded_rate:.1f}% (分值贡献: {np.clip(20-(exploded_rate-10)*0.5,0,20):.1f}/20)")
    print(f"💀 连板杀跌: {death_count:<4} 家 (分值贡献: {np.clip(20-(death_count*5),0,20):.1f}/20)")
    print("-" * 40)
    print(f"🌡️ 情绪综合得分: {score:.1f}")

    if score >= 75:
        weather = "🟢 强：高标持续晋级，连板赚钱效应爆棚！操作：大胆打板，拥抱龙头。"
    elif score >= 50:
        weather = "🟡 中：情绪分化。炸板开始增多，操作：汰弱留强，只做核心。"
    else:
        weather = "🔴 弱：接力亏钱效应。高标跌停，炸板如潮。操作：空仓或转入低吸。"

    print(f"💡 研判结论: {weather}")
    print("🚩" * 15 + "\n")

if __name__ == "__main__":
    analyze_limit_up_sentiment()