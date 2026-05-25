import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime
import warnings

warnings.filterwarnings('ignore')

# --- 1. 数据库配置 ---
engine_quant = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')
engine_review = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')

def init_db():
    """确保绩效历史表存在，并包含大盘统计字段"""
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS strategy_performance_history (
        trade_date DATE,
        strategy_name VARCHAR(100),
        signal_count INT,
        avg_return DECIMAL(10, 4),
        win_rate DECIMAL(10, 4),
        best_return DECIMAL(10, 4),
        worst_return DECIMAL(10, 4),
        market_up_count INT COMMENT '全场上涨家数',
        market_pct_chg DECIMAL(10, 4) COMMENT '全场平均涨幅%',
        PRIMARY KEY (trade_date, strategy_name)
    ) ENGINE=InnoDB;
    """
    with engine_review.begin() as conn:
        conn.execute(text(create_table_sql))
        # 兼容性升级旧表字段
        for col, col_type in [("market_up_count", "INT"), ("market_pct_chg", "DECIMAL(10, 4)")]:
            try:
                conn.execute(text(f"ALTER TABLE strategy_performance_history ADD COLUMN {col} {col_type};"))
            except:
                pass

def get_strategy_performance():
    print(f"[{datetime.datetime.now()}] 启动历史绩效同步系统 (全量覆盖模式)...")
    init_db()

    # 1. 提取信号
    with engine_review.connect() as conn:
        df_signals = pd.read_sql("SELECT symbol, trade_date, status, pool_type FROM stock_pools", conn)
    
    if df_signals.empty:
        print("❌ 错误：stock_pools 表中无信号数据。")
        return

    # 2. 策略归类
    def categorize_strategy(row):
        pt, st = row['pool_type'], str(row['status'])
        if st.startswith("赢家模式:"): return '6. 模式赢家跟随'
        if pt == 'short' and st == '短线爆发黑马': return '1. 短线黑马股'
        if pt == 'long' and st == '长线牛': return '2. 价值长线股'
        if pt == 'short' and st == '资金共振金叉': return '3. 0轴金叉资金共振'
        if pt == 'long' and st == '趋势确立': return '4. MACD+BOLL趋势'
        if pt == 'short' and (st == '主升接力' or st == '启动突破'): return '5. 换手率+量比动能'
        return '其他'

    df_signals['strategy_group'] = df_signals.apply(categorize_strategy, axis=1)
    df_signals = df_signals[df_signals['strategy_group'] != '其他']

    # 3. 准备日期序列
    query_dates = "SELECT DISTINCT trade_date FROM stk_daily_kline ORDER BY trade_date ASC"
    all_dates = pd.read_sql(query_dates, engine_quant)['trade_date'].tolist()
    date_to_next = {all_dates[i]: all_dates[i+1] for i in range(len(all_dates)-1)}

    # 4. 预加载行情数据
    unique_dates = set(df_signals['trade_date'].unique())
    for d in list(unique_dates):
        if d in date_to_next: unique_dates.add(date_to_next[d])
    
    date_params = tuple([d.strftime('%Y-%m-%d') for d in unique_dates])

    print("正在加载价格快照...")
    query_prices = text("SELECT symbol, trade_date, close FROM stk_daily_kline WHERE trade_date IN :d")
    with engine_quant.connect() as conn:
        df_prices_all = pd.read_sql(query_prices, conn, params={"d": date_params})
    
    price_map = df_prices_all.set_index(['symbol', 'trade_date'])['close'].to_dict()

    # 5. 计算每日大盘背景
    market_bg_data = {}
    for t_date in df_signals['trade_date'].unique():
        t_next = date_to_next.get(t_date)
        if not t_next: continue
        
        day_t = df_prices_all[df_prices_all['trade_date'] == t_date]
        day_next = df_prices_all[df_prices_all['trade_date'] == t_next]
        m_df = pd.merge(day_t, day_next, on='symbol', suffixes=('_t', '_next'))
        
        if not m_df.empty:
            m_df['ret'] = (m_df['close_next'] - m_df['close_t']) / m_df['close_t'] * 100
            market_bg_data[t_date] = {
                'm_up': int((m_df['ret'] > 0).sum()),
                'm_ret': float(m_df['ret'].mean())
            }

    # 6. 计算个股策略收益
    results = []
    for _, sig in df_signals.iterrows():
        sym, t_date = sig['symbol'], sig['trade_date']
        t_next = date_to_next.get(t_date)
        if t_next:
            p_t, p_next = price_map.get((sym, t_date)), price_map.get((sym, t_next))
            if p_t and p_next:
                results.append({'trade_date': t_date, 'strategy': sig['strategy_group'], 'return': (p_next - p_t) / p_t * 100})

    if not results:
        print("未发现可结算数据。")
        return

    # 7. 聚合统计
    df_res = pd.DataFrame(results)
    summary = df_res.groupby(['trade_date', 'strategy'])['return'].agg([
        ('signal_count', 'count'),
        ('avg_return', 'mean'),
        ('win_rate', lambda x: (x > 0).mean() * 100),
        ('best_return', 'max'),
        ('worst_return', 'min')
    ]).reset_index()

    summary['market_up_count'] = summary['trade_date'].map(lambda x: market_bg_data.get(x, {}).get('m_up', 0))
    summary['market_pct_chg'] = summary['trade_date'].map(lambda x: market_bg_data.get(x, {}).get('m_ret', 0))

    # 8. 写入数据库 (核心修改：使用 ON DUPLICATE KEY UPDATE)
    print(f"正在覆盖更新 {len(summary)} 条统计记录...")
    try:
        with engine_review.begin() as conn:
            summary.to_sql('temp_perf_final', con=conn, if_exists='replace', index=False)
            
            # 使用 UPSERT 逻辑：如果 trade_date + strategy_name 重复，则更新所有字段
            upsert_sql = text("""
                INSERT INTO strategy_performance_history 
                (trade_date, strategy_name, signal_count, avg_return, win_rate, best_return, worst_return, market_up_count, market_pct_chg)
                SELECT trade_date, strategy, signal_count, avg_return, win_rate, best_return, worst_return, market_up_count, market_pct_chg
                FROM temp_perf_final
                ON DUPLICATE KEY UPDATE 
                    signal_count = VALUES(signal_count),
                    avg_return = VALUES(avg_return),
                    win_rate = VALUES(win_rate),
                    best_return = VALUES(best_return),
                    worst_return = VALUES(worst_return),
                    market_up_count = VALUES(market_up_count),
                    market_pct_chg = VALUES(market_pct_chg);
            """)
            conn.execute(upsert_sql)
            conn.execute(text("DROP TABLE IF EXISTS temp_perf_final"))
            
        print("✅ 绩效库已同步完成 (已执行覆盖更新)。")
        
        latest = summary[summary['trade_date'] == summary['trade_date'].max()]
        print(f"\n📊 今日战报 ({summary['trade_date'].max()})")
        print(latest.sort_values('win_rate', ascending=False)[['strategy', 'win_rate', 'avg_return']].to_string(index=False))

    except Exception as e:
        print(f"❌ 同步失败: {e}")

if __name__ == "__main__":
    get_strategy_performance()