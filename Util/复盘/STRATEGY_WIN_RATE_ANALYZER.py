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
    # 1. 创建基础表
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
    # 2. 动态检查并添加大盘列 (针对旧表升级)
    add_col_sql_1 = "ALTER TABLE strategy_performance_history ADD COLUMN IF NOT EXISTS market_up_count INT;"
    add_col_sql_2 = "ALTER TABLE strategy_performance_history ADD COLUMN IF NOT EXISTS market_pct_chg DECIMAL(10, 4);"
    
    with engine_review.begin() as conn:
        conn.execute(text(create_table_sql))
        # 兼容旧版本 MySQL 不支持 ADD COLUMN IF NOT EXISTS 的情况
        try:
            conn.execute(text("ALTER TABLE strategy_performance_history ADD COLUMN market_up_count INT;"))
            conn.execute(text("ALTER TABLE strategy_performance_history ADD COLUMN market_pct_chg DECIMAL(10, 4);"))
        except:
            pass # 列已存在则跳过

def get_strategy_performance():
    print(f"[{datetime.datetime.now()}] 启动历史绩效与大盘背景同步系统...")
    init_db()

    # 1. 提取所有信号
    with engine_review.connect() as conn:
        df_signals = pd.read_sql("SELECT symbol, trade_date, status, pool_type FROM stock_pools", conn)
    
    if df_signals.empty:
        print("❌ 错误：stock_pools 表中无数据。")
        return

    # 2. 策略归类
    def categorize_strategy(row):
        pt, st = row['pool_type'], str(row['status'])
        if st.startswith("赢家模式:"): return '6. 模式赢家跟随'
        if pt == 'short' and st == '短线爆发黑马': return '1. 短线黑马股'
        if pt == 'long' and st == '长线牛': return '2. 价值长线股'
        if pt == 'short' and st == '资金共振金叉': return '3. 0轴金叉资金共振'
        if pt == 'long' and st == '趋势确立': return '4. MACD+BOLL趋势'
        if pt == 'short' and st in ['主升接力', '启动突破']: return '5. 换手率+量比动能'
        return '其他'

    df_signals['strategy_group'] = df_signals.apply(categorize_strategy, axis=1)
    df_signals = df_signals[df_signals['strategy_group'] != '其他']

    # 3. 准备日期映射 (T -> T+1)
    query_dates = "SELECT DISTINCT trade_date FROM stk_daily_kline ORDER BY trade_date ASC"
    all_dates = pd.read_sql(query_dates, engine_quant)['trade_date'].tolist()
    date_to_next = {all_dates[i]: all_dates[i+1] for i in range(len(all_dates)-1)}

    # 4. 获取所有涉及到的价格数据
    unique_symbols = tuple(df_signals['symbol'].unique())
    # 我们需要获取所有信号日和大盘结算日的价格
    relevant_dates = set(df_signals['trade_date'].unique())
    for d in list(relevant_dates):
        if d in date_to_next:
            relevant_dates.add(date_to_next[d])
    
    date_params = tuple([d.strftime('%Y-%m-%d') for d in relevant_dates])

    print("正在拉取历史行情数据...")
    # 获取个股收盘价
    query_prices = text("SELECT symbol, trade_date, close FROM stk_daily_kline WHERE trade_date IN :d")
    with engine_quant.connect() as conn:
        df_prices_all = pd.read_sql(query_prices, conn, params={"d": date_params})
    
    # 建立极速查找字典 (symbol, date) -> close
    price_map = df_prices_all.set_index(['symbol', 'trade_date'])['close'].to_dict()

    # 5. 计算大盘每日背景数据 (UpCount 和 MarketAvgReturn)
    print("正在计算每日大盘环境背景...")
    market_bg_data = {}
    for t_date in df_signals['trade_date'].unique():
        t_next = date_to_next.get(t_date)
        if not t_next: continue
        
        # 找出这两天都有数据的股票
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
    print("正在计算策略收益矩阵...")
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

    # 7. 聚合统计并合并大盘背景
    df_res = pd.DataFrame(results)
    summary = df_res.groupby(['trade_date', 'strategy'])['return'].agg([
        ('signal_count', 'count'),
        ('avg_return', 'mean'),
        ('win_rate', lambda x: (x > 0).mean() * 100),
        ('best_return', 'max'),
        ('worst_return', 'min')
    ]).reset_index()

    # 合并大盘字段
    summary['market_up_count'] = summary['trade_date'].map(lambda x: market_bg_data.get(x, {}).get('m_up', 0))
    summary['market_pct_chg'] = summary['trade_date'].map(lambda x: market_bg_data.get(x, {}).get('m_ret', 0))

    # 8. 写入数据库 (INSERT IGNORE 保护)
    print(f"正在归档 {len(summary)} 条统计记录...")
    try:
        with engine_review.begin() as conn:
            summary.to_sql('temp_perf_final', con=conn, if_exists='replace', index=False)
            
            # 使用 INSERT IGNORE：如果 trade_date + strategy_name 已经存在，则跳过
            upsert_sql = text("""
                INSERT IGNORE INTO strategy_performance_history 
                (trade_date, strategy_name, signal_count, avg_return, win_rate, best_return, worst_return, market_up_count, market_pct_chg)
                SELECT trade_date, strategy, signal_count, avg_return, win_rate, best_return, worst_return, market_up_count, market_pct_chg
                FROM temp_perf_final
            """)
            conn.execute(upsert_sql)
            conn.execute(text("DROP TABLE IF EXISTS temp_perf_final"))
            
        print("✅ 历史绩效同步成功！已自动避开重复记录。")
        
        # 9. 打印今日战报
        latest = summary[summary['trade_date'] == summary['trade_date'].max()]
        print(f"\n📊 盘面复盘：上涨家数 {int(latest['market_up_count'].iloc[0])} | 市场平均涨幅 {latest['market_pct_chg'].iloc[0]:.2f}%")
        print(latest.sort_values('win_rate', ascending=False)[['strategy', 'win_rate', 'avg_return']].to_string(index=False))

    except Exception as e:
        print(f"❌ 同步失败: {e}")

if __name__ == "__main__":
    get_strategy_performance()