import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime

# --- 1. 配置参数 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

HOLD_DAYS = 5          # 持股 5 天 (中军适合做短波段)
TOP_SECTORS_COUNT = 3  # 每天选取排名前 3 的主线
START_DATE = '2024-01-01'

def run_zhongjun_backtest():
    print(f"[{datetime.datetime.now()}] 正在初始化中军策略回测引擎...")

    # A. 获取所有交易日
    with engine.connect() as conn:
        date_query = text(f"SELECT DISTINCT trade_date FROM stk_daily_kline WHERE trade_date >= '{START_DATE}' ORDER BY trade_date ASC")
        all_dates = [row[0] for row in conn.execute(date_query).fetchall()]

    if len(all_dates) < 10:
        print("数据量不足。")
        return

    # B. 预加载板块映射表 (减少循环内查询)
    query_rel = text("""
        SELECT r.symbol, r.sector_name 
        FROM stock_sector_relation r
        WHERE r.sector_name LIKE '概念-%%' OR r.sector_name LIKE 'THY%%' OR r.sector_name LIKE 'SW1%%'
    """)
    with engine.connect() as conn:
        df_relation = pd.read_sql(query_rel, conn)

    trades = []
    active_positions = {} # 用于记录当前持仓，防止重复买入

    # C. 核心回测循环
    # 我们从第 3 个交易日开始，因为需要前 3 天的数据算热力
    print(f"开始模拟交易，总计 {len(all_dates)-3} 个交易日...")
    
    for i in range(3, len(all_dates)):
        curr_date = all_dates[i]
        lookback_dates = all_dates[i-2 : i+1] # 最近3天
        
        # 1. 提取这3天的行情数据
        dates_str = "','".join([d.strftime('%Y-%m-%d') for d in lookback_dates])
        query_kline = f"SELECT symbol, trade_date, open, close, amount FROM stk_daily_kline WHERE trade_date IN ('{dates_str}')"
        df_klines = pd.read_sql(query_kline, engine)
        
        if df_klines.empty: continue

        # 2. 计算当日板块热力排行 (逻辑同 FIND_THEME_LEADER)
        df_today = df_klines[df_klines['trade_date'] == curr_date].copy()
        df_today['pct_chg'] = (df_today['close'] - df_today['open']) / df_today['open'] * 100
        
        df_merged = pd.merge(df_relation, df_today, on='symbol')
        
        sector_scores = []
        for sector, s_df in df_merged.groupby('sector_name'):
            if len(s_df) < 6: continue
            
            # 计算广度和热力
            breadth = (len(s_df[s_df['pct_chg'] > 2.5]) / len(s_df)) * 100
            total_amt = s_df['amount'].sum() / 1e8
            
            # 简化版评分 (不计算3日持续性以加速回测)
            score = (breadth * 0.5) + (min(total_amt/10, 10) * 3) + (s_df['pct_chg'].mean() * 2)
            
            # 找到该板块中军
            zj_row = s_df.sort_values('amount', ascending=False).iloc[0]
            
            sector_scores.append({
                'sector': sector,
                'score': score,
                'zj_symbol': zj_row['symbol'],
                'zj_amount': zj_row['amount']
            })
        
        if not sector_scores: continue
        
        # 3. 选出评分前 3 的板块中军
        top_sectors = sorted(sector_scores, key=lambda x: x['score'], reverse=True)[:TOP_SECTORS_COUNT]

        # 4. 执行买入逻辑 (假设次日开盘买入)
        if i + 1 >= len(all_dates): break
        next_date = all_dates[i+1]
        
        for item in top_sectors:
            sym = item['zj_symbol']
            # 如果该中军已经在持仓中，跳过
            if sym in active_positions and active_positions[sym] > curr_date:
                continue
            
            # 获取 T+1 买入价格和 T+1+HOLD_DAYS 卖出价格
            exit_idx = i + 1 + HOLD_DAYS
            if exit_idx >= len(all_dates): exit_idx = len(all_dates) - 1
            
            sell_date = all_dates[exit_idx]
            
            # 查询买入价和卖出价
            query_trade = f"SELECT trade_date, open, close FROM stk_daily_kline WHERE symbol='{sym}' AND trade_date IN ('{next_date}', '{sell_date}')"
            df_trade = pd.read_sql(query_trade, engine)
            
            if len(df_trade) < 2: continue
            
            entry_p = df_trade[df_trade['trade_date'] == next_date]['open'].values[0]
            exit_p = df_trade[df_trade['trade_date'] == sell_date]['close'].values[0]
            
            pnl = (exit_p - entry_p) / entry_p
            
            trades.append({
                'date': curr_date,
                'symbol': sym,
                'sector': item['sector'],
                'pnl': pnl
            })
            
            # 更新持仓锁定期
            active_positions[sym] = sell_date

        if i % 20 == 0:
            print(f"进度: {i}/{len(all_dates)}")

    # 5. 统计结果
    report = pd.DataFrame(trades)
    if report.empty:
        print("无交易记录。")
        return

    print("\n" + "="*50)
    print(f"📊 【主线中军策略】回测报告")
    print("-" * 50)
    print(f"测试周期: {START_DATE} 至今")
    print(f"总交易次数: {len(report)}")
    print(f"胜率: {(report['pnl'] > 0).mean()*100:.2f}%")
    print(f"平均单笔收益: {report['pnl'].mean()*100:.2f}%")
    print(f"累计收益率: {(1+report['pnl']).prod() - 1:.2f}")
    
    # 分板块统计
    print("\n各板块中军表现(前5):")
    sector_pnl = report.groupby('sector')['pnl'].agg(['count', 'mean']).sort_values('mean', ascending=False)
    print(sector_pnl.head(5))
    print("="*50)

if __name__ == "__main__":
    run_zhongjun_backtest()