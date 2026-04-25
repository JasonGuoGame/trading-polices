import pandas as pd
from sqlalchemy import create_engine

# --- 配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def run_simple_backtest():
    # 1. 读取所有因子和行情数据
    print("正在加载数据进行回测...")
    df_factors = pd.read_sql("SELECT * FROM stk_factors", engine)
    df_kline = pd.read_sql("SELECT symbol, trade_date, close FROM stk_daily_kline", engine)
    
    # 2. 计算“未来 5 日收益率” (这是回测的关键：前瞻收益)
    # 逻辑：对于每一行，计算它 5 天后的价格相对于现在的涨幅
    df_kline = df_kline.sort_values(['symbol', 'trade_date'])
    df_kline['next_5d_close'] = df_kline.groupby('symbol')['close'].shift(-5)
    df_kline['target_return'] = (df_kline['next_5d_close'] - df_kline['close']) / df_kline['close'] * 100
    
    # 3. 将因子与未来收益率合并
    df_test = pd.merge(df_factors, df_kline[['symbol', 'trade_date', 'target_return']], 
                       on=['symbol', 'trade_date'], how='inner')
    
    # 4. 定义你的策略筛选条件
    # 例子：均线粘合度 < 0.02 且 RSI < 50
    signal_mask = (df_test['f_ma_cohesion'] < 0.02) & (df_test['f_rsi_14'] < 50)
    
    signals = df_test[signal_mask].dropna(subset=['target_return'])
    
    # 5. 展示回测结果
    print("\n" + "="*40)
    print(f"📊 策略回测报告")
    print("-" * 40)
    print(f"信号触发次数: {len(signals)}")
    print(f"5日平均收益率: {signals['target_return'].mean():.2f}%")
    print(f"胜率 (5日后上涨): {(signals['target_return'] > 0).mean()*100:.2f}%")
    print(f"单笔最大涨幅: {signals['target_return'].max():.2f}%")
    print(f"单笔最大跌幅: {signals['target_return'].min():.2f}%")
    print("="*40)

if __name__ == "__main__":
    run_simple_backtest()