import pandas as pd
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine_review = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')
engine = create_engine(DB_URL)

def analyze_market_sentiment():
    print(f"[{datetime.datetime.now()}] 正在计算大盘全景数据...")

    # 1. 获取数据库中最新的两个交易日
    with engine.connect() as conn:
        date_query = text("SELECT DISTINCT trade_date FROM stk_daily_kline ORDER BY trade_date DESC LIMIT 2")
        dates = [row[0] for row in conn.execute(date_query).fetchall()]
    
    if len(dates) < 2:
        print("数据库数据不足（至少需要2个交易日），无法对比。")
        return

    today = dates[0]
    yesterday = dates[1]
    print(f"对比日期: {yesterday} (昨日) vs {today} (今日)")

    # 2. 读取这两个交易日的所有数据
    query = f"""
    SELECT symbol, trade_date, open, close, volume, amount 
    FROM stk_daily_kline 
    WHERE trade_date IN ('{today}', '{yesterday}')
    """
    df_all = pd.read_sql(query, engine)

    # 3. 数据透视：把两天的数据并列，方便计算
    # 将 symbol 设为索引，trade_date 转为列
    df_pivot = df_all.pivot(index='symbol', columns='trade_date', values=['close', 'amount'])
    
    # 丢掉任何一天有缺失值的股票（停牌等）
    df_pivot = df_pivot.dropna()

    # 提取今日和昨日的收盘价与成交额
    close_today = df_pivot['close'][today]
    close_yesterday = df_pivot['close'][yesterday]
    amount_today = df_pivot['amount'][today]
    amount_yesterday = df_pivot['amount'][yesterday]

    # 4. 计算涨跌指标
    pct_change = (close_today - close_yesterday) / close_yesterday * 100
    
    up_count = (pct_change > 0).sum()
    down_count = (pct_change < 0).sum()
    flat_count = (pct_change == 0).sum()

    # 5. 计算涨跌停 (主板规则：>= 9.8% 涨停, <= -9.8% 跌停)
    # 注：因为主板有四舍五入，通常 9.9% 就算涨停了
    limit_up = (pct_change >= 9.8).sum()
    limit_down = (pct_change <= -9.8).sum()

    # 6. 计算全市场成交额变化 (Amount)
    # 量化中通常看“成交额”比“成交量”更准，因为成交额代表了真实的资金流入流出
    total_amount_today = amount_today.sum() / 1e8      # 亿元
    total_amount_yesterday = amount_yesterday.sum() / 1e8 # 亿元
    amount_diff = total_amount_today - total_amount_yesterday
    amount_ratio = (amount_diff / total_amount_yesterday) * 100

    vol_status = "增量" if amount_diff > 0 else "缩量"

    # --- 结果展示 ---
    print("\n" + "="*40)
    print(f"📊 大盘盘面评估汇总 ({today})")
    print("-" * 40)
    print(f"📈 上涨家数: {up_count:<6} | 📉 下跌家数: {down_count:<6}")
    print(f"↔️  持平家数: {flat_count:<6}")
    print(f"🔥 涨停家数: {limit_up:<6} | ❄️  跌停家数: {limit_down:<6}")
    print("-" * 40)
    print(f"💰 今日总成交额: {total_amount_today:.2f} 亿")
    print(f"💰 昨日总成交额: {total_amount_yesterday:.2f} 亿")
    print(f"📊 资金变动: {vol_status} {abs(amount_diff):.2f} 亿 ({amount_ratio:+.2f}%)")
    print("-" * 40)

    # 7. 简单的行情综合判断
    if up_count > down_count * 2 and amount_diff > 0:
        print("💡 综合判断：多头占绝对优势，放量上涨，行情火热！")
    elif down_count > up_count * 2:
        print("💡 综合判断：空头力量强大，市场情绪低迷，注意风险。")
    elif abs(amount_ratio) < 5:
        print("💡 综合判断：存量博弈，交投平淡，震荡格局。")
    elif amount_diff < -100:
        print("💡 1 综合判断：大幅缩量，市场观望情绪浓厚。")
    print("="*40 + "\n")

    # ==========================================
    # 保存市场宽度数据到 market_breadths
    # ==========================================

    total_stocks = up_count + down_count + flat_count

    save_sql = text("""
    INSERT INTO market_breadths (
        trade_date,
        total_stocks,
        advancers,
        decliners,
        flat,
        limit_up,
        limit_down,
        created_at
    )
    VALUES (
        :trade_date,
        :total_stocks,
        :advancers,
        :decliners,
        :flat,
        :limit_up,
        :limit_down,
        :created_at
    )
    ON DUPLICATE KEY UPDATE
        total_stocks = VALUES(total_stocks),
        advancers = VALUES(advancers),
        decliners = VALUES(decliners),
        flat = VALUES(flat),
        limit_up = VALUES(limit_up),
        limit_down = VALUES(limit_down),
        created_at = VALUES(created_at)
    """)

    with engine_review.begin() as conn:
        conn.execute(
            save_sql,
            {
                "trade_date": today,
                "total_stocks": int(total_stocks),
                "advancers": int(up_count),
                "decliners": int(down_count),
                "flat": int(flat_count),
                "limit_up": int(limit_up),
                "limit_down": int(limit_down),
                "created_at": datetime.datetime.now()
            }
        )

    print(f"✅ 市场宽度数据已保存: {today}")

if __name__ == "__main__":
    analyze_market_sentiment()