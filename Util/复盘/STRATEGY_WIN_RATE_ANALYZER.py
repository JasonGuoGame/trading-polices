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
    """确保绩效历史表结构正确"""
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS strategy_performance_history (
        trade_date DATE,
        strategy_name VARCHAR(100),
        signal_count INT,
        avg_return DECIMAL(10, 4),
        win_rate DECIMAL(10, 4),
        best_return DECIMAL(10, 4),
        worst_return DECIMAL(10, 4),
        market_up_count INT COMMENT '当日大盘上涨家数',
        market_pct_chg DECIMAL(10, 4) COMMENT '当日大盘平均涨幅%',
        PRIMARY KEY (trade_date, strategy_name)
    ) ENGINE=InnoDB;
    """
    with engine_review.begin() as conn:
        conn.execute(text(create_table_sql))

def get_strategy_performance():
    print(f"[{datetime.datetime.now()}] 启动绩效同步（精准日期对齐版）...")
    init_db()

    # 1. 提取信号
    with engine_review.connect() as conn:
        df_signals = pd.read_sql("SELECT symbol, trade_date, status, pool_type FROM stock_pools", conn)
    
    if df_signals.empty:
        print("❌ 错误：stock_pools 表中无信号数据。")
        return

    # 2. 策略归类逻辑
    def categorize_strategy(row):
        pt, st = row['pool_type'], str(row['status'])
        if st.startswith("赢家模式:"): return '6. 模式赢家跟随'
        if pt == 'short' and st == '短线爆发黑马': return '1. 短线黑马股'
        if pt == 'long' and st == '长线牛': return '2. 价值长线股'
        if pt == 'short' and st == '资金共振金叉': return '3. 0轴金叉资金共振'
        if pt == 'long' and st == '趋势确立': return '4. MACD+BOLL趋势'
        if pt == 'short' and (st == '主升接力' or st == '启动突破'): return '5. 换手率+量比动能'
        if pt == 'short' and st == '分歧反包': return '分歧反包'
        if pt == 'short' and st == '主力入场': return '主力入场'
        if pt == 'short' and st == '竞价异动': return '竞价异动'
        return '其他'

    df_signals['strategy_group'] = df_signals.apply(categorize_strategy, axis=1)
    df_signals = df_signals[df_signals['strategy_group'] != '其他']

    # 3. 获取所有交易日序列并建立映射
    all_dates = pd.read_sql("SELECT DISTINCT trade_date FROM stk_daily_kline ORDER BY trade_date ASC", engine_quant)['trade_date'].tolist()
    date_to_next = {all_dates[i]: all_dates[i+1] for i in range(len(all_dates)-1)}
    date_to_prev = {all_dates[i]: all_dates[i-1] for i in range(1, len(all_dates))}

    # 4. 获取所有相关日期的行情数据 (用于计算大盘和个股收益)
    print("正在加载历史行情并计算大盘背景...")
    # 获取涉及到的所有日期：信号日 T, 结算日 T+1, 前置日 T-1 (算大盘涨幅用)
    unique_signal_dates = set(df_signals['trade_date'].unique())
    all_needed_dates = set()
    for d in unique_signal_dates:
        all_needed_dates.add(d)
        if d in date_to_next: all_needed_dates.add(date_to_next[d])
        if d in date_to_prev: all_needed_dates.add(date_to_prev[d])
    
    date_params = tuple([d.strftime('%Y-%m-%d') for d in all_needed_dates])

    query_prices = text("SELECT symbol, trade_date, close FROM stk_daily_kline WHERE trade_date IN :d")
    with engine_quant.connect() as conn:
        df_prices_all = pd.read_sql(query_prices, conn, params={"d": date_params})
    
    # 建立查找字典 (symbol, date) -> close
    price_map = df_prices_all.set_index(['symbol', 'trade_date'])['close'].to_dict()

    # --- 5. 核心修正：计算【T日当天】的大盘环境数据 ---
    # 大盘数据 T = 价格(T) vs 价格(T-1)
    market_daily_stats = {}
    for t_date in unique_signal_dates:
        t_prev = date_to_prev.get(t_date)
        if not t_prev: continue
        
        day_t = df_prices_all[df_prices_all['trade_date'] == t_date]
        day_prev = df_prices_all[df_prices_all['trade_date'] == t_prev]
        
        m_df = pd.merge(day_t, day_prev, on='symbol', suffixes=('_t', '_prev'))
        if not m_df.empty:
            m_df['ret'] = (m_df['close_t'] - m_df['close_prev']) / m_df['close_prev'] * 100
            market_daily_stats[t_date] = {
                'm_up': int((m_df['ret'] > 0).sum()),
                'm_ret': float(m_df['ret'].mean())
            }

    # --- 6. 计算【T日信号】在【T+1日】的表现 ---
    print("正在结算策略表现...")
    results = []
    for _, sig in df_signals.iterrows():
        sym, t_date = sig['symbol'], sig['trade_date']
        t_next = date_to_next.get(t_date)
        
        if t_next:
            p_t = price_map.get((sym, t_date))
            p_next = price_map.get((sym, t_next))
            if p_t and p_next:
                ret = (p_next - p_t) / p_t * 100
                results.append({
                    'trade_date': t_date,
                    'strategy': sig['strategy_group'],
                    'return': ret
                })

    if not results:
        print("💡 今日暂无可结算的完整交易对（需等待明日行情更新）。")
        return

    # 7. 聚合策略统计
    df_res = pd.DataFrame(results)
    summary = df_res.groupby(['trade_date', 'strategy'])['return'].agg([
        ('signal_count', 'count'),
        ('avg_return', 'mean'),
        ('win_rate', lambda x: (x > 0).mean() * 100),
        ('best_return', 'max'),
        ('worst_return', 'min')
    ]).reset_index()

    # --- 8. 映射大盘数据：确保 T日的信号对应 T日的大盘 ---
    summary['market_up_count'] = summary['trade_date'].map(lambda x: market_daily_stats.get(x, {}).get('m_up', 0))
    summary['market_pct_chg'] = summary['trade_date'].map(lambda x: market_daily_stats.get(x, {}).get('m_ret', 0))

    # 9. 写入数据库 (使用 ON DUPLICATE KEY UPDATE 覆盖)
    print(f"正在覆盖更新 {len(summary)} 条历史记录...")
    try:
        with engine_review.begin() as conn:
            summary.to_sql('temp_perf_final', con=conn, if_exists='replace', index=False)
            
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
            
        print("✅ 绩效库同步成功！日期与大盘环境已精准对齐。")
        
        # 10. 打印最后一日结算情况
        latest_date = summary['trade_date'].max()
        latest_data = summary[summary['trade_date'] == latest_date]
        print(f"\n📊 历史回溯复盘 | 信号日: {latest_date}")
        print(f"🌡️ 当日大盘：上涨 {int(latest_data['market_up_count'].iloc[0])} 家 | 均涨 {latest_data['market_pct_chg'].iloc[0]:.2f}%")
        print(latest_data.sort_values('win_rate', ascending=False)[['strategy', 'win_rate', 'avg_return']].to_string(index=False))

    except Exception as e:
        print(f"❌ 同步失败: {e}")

if __name__ == "__main__":
    get_strategy_performance()