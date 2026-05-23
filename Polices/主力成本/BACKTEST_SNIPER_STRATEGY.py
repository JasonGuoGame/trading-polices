import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime
import warnings

warnings.filterwarnings('ignore')

# --- 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def run_sniper_backtest():
    print(f"[{datetime.datetime.now()}] 启动‘重点狙击名单’持股胜率回测...")

    # 1. 获取异动表中的所有历史信号（受分时表 30 天限制）
    # 我们只取有分时数据支撑的最近 30 天信号
    with engine.connect() as conn:
        min_date_res = conn.execute(text("SELECT MIN(DATE(trade_time)) FROM stk_min_kline")).fetchone()[0]
        max_date_res = conn.execute(text("SELECT MAX(trade_date) FROM stk_capital_abnormal")).fetchone()[0]
        
        print(f"回测窗口：{min_date_res} 至 {max_date_res}")
        
        query_signals = text("""
            SELECT symbol, name, trade_date, surge_times, vol_ratio, surge_count
            FROM stk_capital_abnormal 
            WHERE trade_date >= :sd AND trade_date <= :ed
        """)
        df_signals = pd.read_sql(query_signals, conn, params={"sd": min_date_res, "ed": max_date_res})

    if df_signals.empty:
        print("未发现回测窗口内的异动信号。")
        return

    # 2. 准备日线价格数据 (用于计算买入后的收益)
    print("正在预加载日线价格数据...")
    df_daily = pd.read_sql("SELECT symbol, trade_date, open, close FROM stk_daily_kline", engine)
    df_daily = df_daily.sort_values(['symbol', 'trade_date'])

    results = []

    # 3. 逐个信号进行“成本穿透”过滤
    print(f"正在对 {len(df_signals)} 个初始信号进行主力成本校准...")
    
    for _, sig in df_signals.iterrows():
        sym = sig['symbol']
        t_date = sig['trade_date']
        times_list = [f"'{t}:00'" for t in sig['surge_times'].split(',')]
        
        try:
            # A. 计算主力成本
            query_min = f"""
                SELECT amount, volume FROM stk_min_kline 
                WHERE symbol = '{sym}' AND DATE(trade_time) = '{t_date}'
                AND TIME(trade_time) IN ({','.join(times_list)})
            """
            df_surges = pd.read_sql(query_min, engine)
            if df_surges.empty: continue
            
            mf_cost = df_surges['amount'].sum() / (df_surges['volume'].sum() * 100 + 0.01)
            
            # B. 获取当日收盘价进行“狙击名单”过滤
            # 过滤逻辑：主力获利在 -5% 到 +4% 之间
            curr_day_data = df_daily[(df_daily['symbol'] == sym) & (df_daily['trade_date'] == t_date)]
            if curr_day_data.empty: continue
            
            close_t0 = curr_day_data['close'].iloc[0]
            mf_profit = (close_t0 - mf_cost) / mf_cost
            
            if not (-0.05 <= mf_profit <= 0.04):
                continue # 不符合“重点狙击”条件，跳过

            # C. 跟踪未来 1-5 天收益
            # 逻辑：T+1日开盘买入，分别计算 T+1, T+2... T+5 的收盘价卖出收益
            stock_history = df_daily[df_daily['symbol'] == sym]
            future_data = stock_history[stock_history['trade_date'] > t_date].head(5)
            
            if len(future_data) < 1: continue
            
            entry_price = future_data['open'].iloc[0] # T+1 开盘买入
            
            pnl_row = {
                'symbol': sym,
                'date': t_date,
                'mf_profit': round(mf_profit*100, 2)
            }
            
            for day in range(1, 6):
                if len(future_data) >= day:
                    exit_price = future_data['close'].iloc[day-1]
                    # 计算扣除千分之1.5手续费后的净收益
                    pnl = (exit_price - entry_price) / entry_price - 0.0015
                    pnl_row[f'day{day}_pnl'] = pnl
                else:
                    pnl_row[f'day{day}_pnl'] = np.nan
            
            results.append(pnl_row)
            
        except:
            continue

    # 4. 统计结果
    if not results:
        print("经过成本过滤后，未找到符合‘重点狙击’条件的信号。")
        return

    df_res = pd.DataFrame(results)
    
    print("\n" + "="*50)
    print(f"📊 【重点狙击策略】持股周期胜率报告")
    print(f"信号样本数: {len(df_res)}")
    print("-" * 50)

    summary = []
    for d in range(1, 6):
        col = f'day{d}_pnl'
        valid_pnl = df_res[col].dropna()
        win_rate = (valid_pnl > 0).mean() * 100
        avg_ret = valid_pnl.mean() * 100
        summary.append({
            '持股天数': f"第 {d} 天",
            '胜率%': f"{win_rate:.2f}%",
            '平均收益%': f"{avg_ret:+.2f}%"
        })

    print(pd.DataFrame(summary).to_string(index=False))
    print("-" * 50)
    print("💡 结论参考：")
    print("1. 胜率最高的持股天数即为该策略的最佳离场点。")
    print("2. 若平均收益随天数增加而下降，说明该异动属于短线脉冲，应见好就收。")
    print("="*50)

if __name__ == "__main__":
    run_sniper_backtest()