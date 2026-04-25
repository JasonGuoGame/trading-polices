import pandas as pd
import numpy as np
from sqlalchemy import create_engine
import datetime

# --- 1. 配置参数 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

# 回测设置
HOLD_DAYS = 5          # 持股天数
STOP_LOSS = -0.05      # 止损比例 (-5%)
TAKE_PROFIT = 0.15     # 止盈比例 (+15%)
START_DATE = '2023-01-01' # 回测起始日期

def run_backtest():
    print(f"[{datetime.datetime.now()}] 正在从数据库加载回测数据...")
    
    # 获取全量历史行情
    query = f"SELECT symbol, trade_date, open, high, low, close, volume FROM stk_daily_kline WHERE trade_date >= '{START_DATE}' ORDER BY symbol, trade_date ASC"
    df_all = pd.read_sql(query, engine)
    
    if df_all.empty:
        print("数据库中没有行情数据。")
        return

    trades = [] # 存储每笔交易的结果

    print("正在扫描历史信号并模拟交易...")
    
    # 2. 按股票分组计算信号
    for symbol, df in df_all.groupby('symbol'):
        if len(df) < 80: continue
        
        # --- 计算技术指标 ---
        # A. 前期高点：过去60天最高价（避开最近10天，寻找稍微久远一点的压力位）
        df['prev_high'] = df['high'].shift(10).rolling(60).max()
        # B. 60日均线：确认大趋势向上
        df['ma60'] = df['close'].rolling(60).mean()
        # C. 10日均量：判断缩量
        df['vol_ma10'] = df['volume'].rolling(10).mean()
        
        # --- 产生信号 (Signal Generation) ---
        # 1. 趋势：价格在60日线上
        cond_trend = df['close'] > df['ma60']
        # 2. 突破：最近10天内曾突破过前期高点 3% 以上
        df['has_broken'] = (df['close'] > df['prev_high'] * 1.03).rolling(10).max() == 1
        # 3. 回踩：当前价格距离前期高点在 [-1.5%, +2%] 之间
        df['dist'] = (df['close'] - df['prev_high']) / df['prev_high']
        cond_retest = (df['dist'] >= -0.015) & (df['dist'] <= 0.02)
        # 4. 缩量：成交量小于10日均量的 1.2 倍
        cond_vol = df['volume'] < df['vol_ma10'] * 1.2
        
        # 汇总买入信号
        df['buy_signal'] = cond_trend & df['has_broken'] & cond_retest & cond_vol
        
        # 3. 模拟持仓逻辑 (Trade Simulation)
        signal_indices = df[df['buy_signal']].index
        
        processed_until = -1 # 防止同一段行情重复买入

        for idx in signal_indices:
            if idx <= processed_until: continue
            
            # 买入点：信号发生后的下一个交易日开盘 (T+1 Open)
            if idx + 1 >= len(df): break
            entry_price = df.loc[idx + 1, 'open']
            entry_date = df.loc[idx + 1, 'trade_date']
            
            # 模拟持有 HOLD_DAYS 天
            exit_price = entry_price
            exit_date = None
            
            # 遍历未来 N 天检查止损止盈
            for j in range(1, HOLD_DAYS + 1):
                curr_idx = idx + 1 + j
                if curr_idx >= len(df): 
                    # 到达历史终点，强制卖出
                    curr_idx = len(df) - 1
                    exit_price = df.loc[curr_idx, 'close']
                    exit_date = df.loc[curr_idx, 'trade_date']
                    break
                
                high_p = df.loc[curr_idx, 'high']
                low_p = df.loc[curr_idx, 'low']
                close_p = df.loc[curr_idx, 'close']
                
                # 检查止损
                if (low_p - entry_price) / entry_price <= STOP_LOSS:
                    exit_price = entry_price * (1 + STOP_LOSS)
                    exit_date = df.loc[curr_idx, 'trade_date']
                    break
                # 检查止盈
                elif (high_p - entry_price) / entry_price >= TAKE_PROFIT:
                    exit_price = entry_price * (1 + TAKE_PROFIT)
                    exit_date = df.loc[curr_idx, 'trade_date']
                    break
                # 到期卖出
                if j == HOLD_DAYS:
                    exit_price = close_p
                    exit_date = df.loc[curr_idx, 'trade_date']

            # 记录交易结果
            pnl = (exit_price - entry_price) / entry_price
            trades.append({
                'symbol': symbol,
                'entry_date': entry_date,
                'entry_price': entry_price,
                'exit_date': exit_date,
                'exit_price': exit_price,
                'pnl': pnl
            })
            
            processed_until = idx + HOLD_DAYS # 保护期：持有期间不重复开仓

    # 4. 统计回测结果
    if not trades:
        print("未发现任何符合条件的交易机会。")
        return

    report = pd.DataFrame(trades)
    win_rate = (report['pnl'] > 0).mean()
    avg_pnl = report['pnl'].mean()
    total_pnl = (1 + report['pnl']).prod() - 1
    
    print("\n" + "="*50)
    print(f"📈 【牛回头】策略回测报告 ({START_DATE} 至今)")
    print("-" * 50)
    print(f"总交易次数: {len(report)}")
    print(f"胜率 (Win Rate): {win_rate*100:.2f}%")
    print(f"平均单笔收益: {avg_pnl*100:.2f}%")
    print(f"盈亏比 (Profit/Loss Ratio): {abs(report[report['pnl']>0]['pnl'].mean() / report[report['pnl']<0]['pnl'].mean()):.2f}")
    print(f"策略累积收益率: {total_pnl*100:.2f}%")
    print("="*50)

    # 展示表现最好的 5 笔交易
    print("\n最成功的 5 笔交易:")
    print(report.sort_values('pnl', ascending=False).head(5)[['symbol', 'entry_date', 'pnl']])

if __name__ == "__main__":
    run_backtest()