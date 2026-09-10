import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime

# --- 1. 数据库配置（直接读取已入库的数据） ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def get_auction_sentiment_report():
    print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🚀 正在生成全市场竞价热力报告 (纯 DB 模式)...")

    # 获取今天的日期字符串 'YYYY-MM-DD'
    today_str = datetime.date.today().strftime('%Y-%m-%d')

    # 1. 查询今天的竞价数据，以及过去 5 个交易日的历史均值 (V5)
    # 通过一次 SQL 查询搞定，极大提升效率
    sql = """
    WITH hist_5d AS (
        -- 计算过去 5 个记录日的平均竞价金额
        SELECT symbol, AVG(auction_amount) as v5_avg_amount
        FROM (
            SELECT symbol, auction_amount,
                   ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY trade_date DESC) as rn
            FROM stk_auction_signal
            WHERE trade_date < CURDATE()
        ) t
        WHERE rn <= 5
        GROUP BY symbol
    )
    SELECT 
        curr.symbol,
        curr.name,
        curr.auction_amount as today_amount,  -- 今日竞价金额 (万元)
        curr.open_pct,                       -- 今日开盘涨幅 (%)
        h.v5_avg_amount                       -- 历史 5 日均额 (万元)
    FROM stk_auction_signal curr
    LEFT JOIN hist_5d h ON curr.symbol = h.symbol
    WHERE curr.trade_date = CURDATE();
    """
    
    try:
        with engine.connect() as conn:
            df = pd.read_sql(text(sql), conn)
    except Exception as e:
        print(f"❌ 数据库读取失败: {e}")
        return

    if df.empty:
        print(f"❌ 提示：数据库中未找到今日 ({today_str}) 的竞价数据。请确认 09:25 的 QMT 采集脚本已运行入库。")
        return

    # 2. 过滤有效交易股票（剔除停牌、今日无竞价金额的标的）
    df_valid = df[(df['today_amount'] > 0) & (df['v5_avg_amount'] > 0)].copy()

    if df_valid.empty:
        print("❌ 未能筛选出有效的比对数据（可能历史数据不足 5 天）。")
        return

    # 3. 计算单股量比（基于竞价成交额）
    df_valid['ratio'] = df_valid['today_amount'] / df_valid['v5_avg_amount']

    # 4. 统计指标计算
    all_ratios = df_valid['ratio'].values
    market_avg_ratio = np.mean(all_ratios)
    market_median_ratio = np.median(all_ratios)
    
    # 竞价总金额（数据库存的是万元，需转为亿元）
    total_amount_亿 = df_valid['today_amount'].sum() / 10000.0  
    
    total_valid_stocks = len(df_valid)
    up_count = len(df_valid[df_valid['open_pct'] > 0.05])
    down_count = len(df_valid[df_valid['open_pct'] < -0.05])
    flat_count = total_valid_stocks - up_count - down_count
    
    up_rate = (up_count / total_valid_stocks) * 100 if total_valid_stocks > 0 else 0

    # 5. 输出热力报告
    print("\n" + "🏮" * 25)
    print(f"📊 A股竞价热力报告 ({datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
    print("-" * 50)
    print(f"🔹 统计有效标的数:   {total_valid_stocks} 只")
    
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
    print("💡 注：报告数据完全基于数据库 stk_auction_signal 提取计算。")
    print("🏮" * 25 + "\n")

if __name__ == "__main__":
    get_auction_sentiment_report()