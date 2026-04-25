import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime

# 由于我的资金异动数据库数据太少，目前无法回测。

# --- 1. 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def run_backtest():
    print(f"[{datetime.datetime.now()}] 正在启动回测引擎...")

    # --- 步骤 A: 计算每日大盘上涨家数 ---
    print("正在计算每日大盘赚钱效应（上涨家数）...")
    # 逻辑：当日收盘价 > 昨日收盘价 记为上涨
    breadth_sql = """
    SELECT trade_date, COUNT(*) as up_count
    FROM (
        SELECT symbol, trade_date, close, 
               LAG(close) OVER (PARTITION BY symbol ORDER BY trade_date) as prev_close
        FROM stk_daily_kline
    ) t
    WHERE close > prev_close
    GROUP BY trade_date
    """
    df_breadth = pd.read_sql(breadth_sql, engine)
    # 筛选上涨家数 >= 2800 的日期
    valid_dates = df_breadth[df_breadth['up_count'] >= 2800]['trade_date'].tolist()
    print(f"历史上一共有 {len(valid_dates)} 个交易日满足‘上涨家数 >= 2800’")

    # --- 步骤 B: 提取异动信号 ---
    print("正在提取主力资金异动信号...")
    # 只要在满足大盘条件的日期发生的异动
    if not valid_dates:
        print("未找到满足大盘条件的日期，回测中止。")
        return

    dates_str = "','".join([str(d) for d in valid_dates])
    signal_sql = f"""
    SELECT symbol, name, trade_date, surge_count 
    FROM stk_capital_abnormal 
    WHERE trade_date IN ('{dates_str}')
    """
    df_signals = pd.read_sql(signal_sql, engine)
    print(f"在普涨日中，共发现 {len(df_signals)} 个资金异动信号。")

    if df_signals.empty:
        print("没有符合条件的异动信号。")
        return

    # --- 步骤 C: 计算收益率 (5日持仓) ---
    print("正在计算信号触发后 5 日的预期收益...")
    # 获取所有价格数据用于计算涨跌
    price_sql = "SELECT symbol, trade_date, open, close FROM stk_daily_kline"
    df_prices = pd.read_sql(price_sql, engine)
    df_prices = df_prices.sort_values(['symbol', 'trade_date'])

    # 计算 T+1 开盘买入价 和 T+5 收盘卖出价
    # shift(-1) 是明天开盘，shift(-5) 是5天后收盘
    df_prices['buy_price'] = df_prices.groupby('symbol')['open'].shift(-1)
    df_prices['sell_price'] = df_prices.groupby('symbol')['close'].shift(-5)
    
    # 合并信号与价格
    df_results = pd.merge(df_signals, df_prices, on=['symbol', 'trade_date'], how='inner')
    
    # 计算单笔盈亏 (扣除 0.15% 手续费)
    df_results['pnl'] = (df_results['sell_price'] - df_results['buy_price']) / df_results['buy_price'] - 0.0015
    
    # 剔除无法成交的数据 (比如最后5天产生的信号没有卖出价)
    df_results = df_results.dropna(subset=['pnl'])

    # --- 步骤 D: 统计分析 ---
    if df_results.empty:
        print("计算完毕，但有效交易数据为空（可能信号离现在太近，不足5天）。")
        return

    win_rate = (df_results['pnl'] > 0).mean()
    avg_ret = df_results['pnl'].mean()
    total_ret = (1 + df_results['pnl']).prod() - 1
    
    print("\n" + "="*50)
    print(f"📊 【大盘共振+异动】策略回测报告")
    print("-" * 50)
    print(f"回测信号总数: {len(df_results)}")
    print(f"🎯 胜率 (Win Rate): {win_rate*100:.2f}%")
    print(f"💰 平均单笔收益: {avg_ret*100:.2f}%")
    print(f"📈 累计收益率 (假设每笔复利): {total_ret*100:.2f}%")
    print(f"🔝 单笔最高收益: {df_results['pnl'].max()*100:.2f}%")
    print(f"🔻 单笔最大亏损: {df_results['pnl'].min()*100:.2f}%")
    
    # 按照异动次数进行分组，看看是不是异动越多越准
    print("\n💡 异动次数(surge_count)与胜率的关系:")
    grouped = df_results.groupby('surge_count')['pnl'].agg(['count', 'mean', lambda x: (x > 0).mean()])
    grouped.columns = ['信号数', '平均收益', '胜率']
    print(grouped)
    
    print("="*50)

if __name__ == "__main__":
    run_backtest()