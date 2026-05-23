import pandas as pd
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def analyze_auction_trend():
    print("正在从数据库提取分时数据并分析竞价趋势...")

    # 1. 获取过去 15 个交易日的开盘瞬间数据 (确保有足够的窗口算均值)
    # 逻辑：只取 09:30:00 的 K 线
    sql = """
    SELECT symbol, DATE(trade_time) as trade_date, volume
    FROM stk_min_kline
    WHERE TIME(trade_time) = '09:30:00'
      AND trade_time >= DATE_SUB(NOW(), INTERVAL 20 DAY)
    ORDER BY trade_date ASC
    """
    
    with engine.connect() as conn:
        df = pd.read_sql(text(sql), conn)

    if df.empty:
        print("❌ 数据库中无分时数据。")
        return

    # 2. 数据透视：行是日期，列是股票代码
    df_pivot = df.pivot(index='trade_date', columns='symbol', values='volume')
    # 剔除全天停牌太多的日期或异常列
    df_pivot = df_pivot.dropna(axis=1, thresh=len(df_pivot)*0.7) 

    # 3. 计算每只股票每天的“竞价量比”
    # 量比 = 当日量 / 过去 5 日均量
    # rolling(5).mean() 计算 5 日移动平均
    v5_ma = df_pivot.shift(1).rolling(5).mean()
    vol_ratios = df_pivot / v5_ma

    # 4. 计算“全市场平均竞价量比”序列
    # 我们对每一行（每一天）求平均，得到全市场的情绪指标
    market_ratio_series = vol_ratios.mean(axis=1).dropna()

    if len(market_ratio_series) < 2:
        print("数据样本不足，无法计算趋势。")
        return

    # 5. 提取今日与历史数据进行对比
    latest_val = market_ratio_series.iloc[-1]
    latest_date = market_ratio_series.index[-1]
    
    # 过去 5 天（不含今日）的平均量比水平
    hist_avg = market_ratio_series.iloc[-6:-1].mean()
    
    # 计算放大倍数
    expansion_rate = (latest_val - hist_avg) / hist_avg * 100

    # 6. 输出结果报告
    print("\n" + "📈" * 15)
    print(f"📊 全市场竞价动能趋势报告")
    print(f"📅 最新交易日: {latest_date}")
    print("-" * 40)
    print(f"🔥 今日平均竞价量比: {latest_val:.2f}")
    print(f"⏳ 近5日历史平均水平: {hist_avg:.2f}")
    print(f"🚀 动能变动幅度: {expansion_rate:+.2f}%")
    print("-" * 40)

    # 7. 研判逻辑
    if expansion_rate > 20 and latest_val > 1.2:
        conclusion = "🚩 结论：【显著放大】！主力资金进场急迫，未来1-3天看涨信号强烈。"
    elif expansion_rate > 5:
        conclusion = "🚩 结论：【温和放量】。市场情绪回暖，具备操作价值。"
    elif expansion_rate < -15:
        conclusion = "🚩 结论：【明显萎缩】。资金参与度骤降，注意回调风险。"
    else:
        conclusion = "🚩 结论：【波澜不惊】。存量博弈，维持震荡研判。"

    print(conclusion)
    
    # 打印最近 5 天的数值看趋势
    print("\n最近 5 日全市场量比走势:")
    for date, val in market_ratio_series.tail(5).items():
        print(f"   {date}: {val:.2f}")
    print("📈" * 15 + "\n")

if __name__ == "__main__":
    analyze_auction_trend()