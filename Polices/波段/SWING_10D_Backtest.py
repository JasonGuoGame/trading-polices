import pandas as pd
import numpy as np
from sqlalchemy import create_engine
import datetime

# --- 1. 配置参数 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

# 回测核心设置
HOLD_DAYS = 10         # 强制持仓天数
STOP_LOSS = -0.05      # 动态止损 (-5%)
START_DATE = '2023-01-01' # 回测起始日期

def run_swing_backtest():
    print(f"[{datetime.datetime.now()}] 正在加载全量历史行情进行波段回测...")
    
    # 获取全沪深 A 股历史数据
    query = f"""
    SELECT symbol, trade_date, open, high, low, close, volume 
    FROM stk_daily_kline 
    WHERE trade_date >= '{START_DATE}' 
    ORDER BY symbol, trade_date ASC
    """
    df_all = pd.read_sql(query, engine)
    
    if df_all.empty:
        print("错误：数据库中没有行情数据。")
        return

    trades = [] # 记录每一笔交易

    print("正在扫描策略信号并模拟 10 日持仓...")
    
    # 2. 按股票分组计算信号 (向量化加速)
    for symbol, df in df_all.groupby('symbol'):
        if len(df) < 40: continue
        
        # --- 计算技术指标 ---
        # A. 均线系统
        df['MA5'] = df['close'].rolling(5).mean()
        df['MA10'] = df['close'].rolling(10).mean()
        df['MA20'] = df['close'].rolling(20).mean()
        df['V_MA5'] = df['volume'].rolling(5).mean()
        
        # B. 20日高点突破 (不含当天)
        df['rolling_high_20'] = df['high'].shift(1).rolling(20).max()
        
        # --- 产生买入信号 (逻辑对齐之前的波段策略) ---
        # 1. 均线粘合度 < 3%
        ma_max = df[['MA5', 'MA10', 'MA20']].max(axis=1)
        ma_min = df[['MA5', 'MA10', 'MA20']].min(axis=1)
        df['cohesion'] = (ma_max - ma_min) / df['MA20']
        cond_cohesion = df['cohesion'] < 0.03
        
        # 2. 趋势排列 (MA5 > MA10 > MA20)
        cond_bullish = (df['MA5'] > df['MA10']) & (df['MA10'] > df['MA20'])
        
        # 3. 放量突破 20 日平台
        cond_breakout = (df['close'] > df['rolling_high_20']) & (df['volume'] > 2.0 * df['V_MA5'])
        
        # 汇总信号
        df['buy_signal'] = cond_cohesion & cond_bullish & cond_breakout
        
        # 3. 模拟 10 日交易逻辑
        signal_indices = df[df['buy_signal']].index
        last_exit_idx = -1 # 用于控制不重复持仓

        for idx in signal_indices:
            # 如果当前信号在前一笔交易的持仓期内，跳过（不重复开仓）
            if idx <= last_exit_idx: continue
            
            # T+1 开盘买入
            if idx + 1 >= len(df): break
            entry_price = df.loc[idx + 1, 'open']
            entry_date = df.loc[idx + 1, 'trade_date']
            
            exit_price = entry_price
            exit_date = None
            triggered_sl = False
            
            # 模拟持有 10 天
            for d in range(1, HOLD_DAYS + 1):
                curr_idx = idx + 1 + d
                if curr_idx >= len(df): 
                    # 到了历史尽头，强卖
                    curr_idx = len(df) - 1
                    exit_price = df.loc[curr_idx, 'close']
                    exit_date = df.loc[curr_idx, 'trade_date']
                    break
                
                # 检查期间是否有止损 (跌破 5%)
                if (df.loc[curr_idx, 'low'] - entry_price) / entry_price <= STOP_LOSS:
                    exit_price = entry_price * (1 + STOP_LOSS)
                    exit_date = df.loc[curr_idx, 'trade_date']
                    triggered_sl = True
                    last_exit_idx = curr_idx # 记录止损位置
                    break
                
                # 满 10 天卖出 (以第10天收盘价计)
                if d == HOLD_DAYS:
                    exit_price = df.loc[curr_idx, 'close']
                    exit_date = df.loc[curr_idx, 'trade_date']
                    last_exit_idx = curr_idx
            
            # 记录这笔交易
            pnl = (exit_price - entry_price) / entry_price
            trades.append({
                'symbol': symbol,
                'entry_date': entry_date,
                'exit_date': exit_date,
                'pnl': pnl,
                'type': 'StopLoss' if triggered_sl else 'NormalExit'
            })

    # 4. 统计回测报告
    if not trades:
        print("未发现任何符合条件的波段信号。")
        return

    report = pd.DataFrame(trades)
    win_rate = (report['pnl'] > 0).mean()
    avg_pnl = report['pnl'].mean()
    
    print("\n" + "="*50)
    print(f"📊 【波段逻辑】10日持仓回测报告")
    print(f"周期: {START_DATE} 至今")
    print("-" * 50)
    print(f"总交易次数: {len(report)}")
    print(f"平均胜率: {win_rate*100:.2f}%")
    print(f"平均每笔收益: {avg_pnl*100:.2f}%")
    print(f"止损退出次数: {len(report[report['type']=='StopLoss'])}")
    print(f"单笔最大获利: {report['pnl'].max()*100:.2f}%")
    print(f"单笔最大亏损: {report['pnl'].min()*100:.2f}%")
    
    # 计算盈亏比
    pos_pnl = report[report['pnl'] > 0]['pnl'].mean()
    neg_pnl = report[report['pnl'] < 0]['pnl'].mean()
    profit_factor = abs(pos_pnl / neg_pnl) if neg_pnl != 0 else 0
    print(f"盈亏比 (Profit Factor): {profit_factor:.2f}")
    print("="*50)

    # 打印表现最强的 10 笔交易
    print("\n最强波段案例:")
    print(report.sort_values('pnl', ascending=False).head(10)[['symbol', 'entry_date', 'pnl']])

if __name__ == "__main__":
    run_swing_backtest()