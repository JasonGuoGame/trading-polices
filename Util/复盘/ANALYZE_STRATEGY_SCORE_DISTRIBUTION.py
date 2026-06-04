import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime
import warnings

warnings.filterwarnings('ignore')

# --- 1. 数据库配置 ---
engine_quant = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')
engine_review = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')

def get_score_analysis():
    print(f"[{datetime.datetime.now()}] 启动多策略分数段收益穿透分析...")

    # 1. 提取信号
    with engine_review.connect() as conn:
        df_signals = pd.read_sql("SELECT symbol, trade_date, pool_type, status, score FROM stock_pools", conn)
    
    if df_signals.empty:
        print("❌ 错误：stock_pools 表中无信号数据。")
        return

    # 2. 策略映射逻辑
    def categorize_strategy(row):
        pt, st = row['pool_type'], str(row['status'])
        if st.startswith("赢家模式:"): return '6. 赢家跟随'
        if pt == 'short' and st == '短线爆发黑马': return '1. 短线黑马'
        if pt == 'short' and (st == '主升接力' or st == '启动突破'): return '2. 换手率量比'
        if pt == 'short' and st == '资金共振金叉': return '3. 0轴金叉共振'
        if pt == 'long' and st == '趋势确立': return '4. MACD+BOLL'
        if pt == 'long' and st == '长线牛': return '5. 价值长线'
        if pt == 'short' and st == '主力入场': return '主力入场'
        if pt == 'short' and st == '分歧反包': return '分歧反包'
        return '其他'

    df_signals['strategy_name'] = df_signals.apply(categorize_strategy, axis=1)
    df_signals = df_signals[df_signals['strategy_name'] != '其他']

    # 3. 准备 T+1 价格结算
    all_dates = pd.read_sql("SELECT DISTINCT trade_date FROM stk_daily_kline ORDER BY trade_date ASC", engine_quant)['trade_date'].tolist()
    date_to_next = {all_dates[i]: all_dates[i+1] for i in range(len(all_dates)-1)}

    symbols = tuple(df_signals['symbol'].unique())
    dates_raw = set(df_signals['trade_date'].unique())
    dates_next = [date_to_next[d] for d in dates_raw if d in date_to_next]
    combined_dates = tuple([d.strftime('%Y-%m-%d') for d in (list(dates_raw) + dates_next)])

    print(f"正在读取 {len(symbols)} 只个股的价格数据进行收益结算...")
    query_prices = text("SELECT symbol, trade_date, close FROM stk_daily_kline WHERE symbol IN :s AND trade_date IN :d")
    with engine_quant.connect() as conn:
        df_prices = pd.read_sql(query_prices, conn, params={"s": symbols, "d": combined_dates})
    
    price_map = df_prices.set_index(['symbol', 'trade_date'])['close'].to_dict()

    # 4. 计算收益率
    performance_data = []
    for _, sig in df_signals.iterrows():
        sym, t_date = sig['symbol'], sig['trade_date']
        t_next = date_to_next.get(t_date)
        
        if t_next:
            p_t = price_map.get((sym, t_date))
            p_next = price_map.get((sym, t_next))
            
            if p_t and p_next:
                ret = (p_next - p_t) / p_t * 100
                performance_data.append({
                    'trade_date': t_date,
                    'strategy_name': sig['strategy_name'],
                    'score': float(sig['score'] if sig['score'] is not None else 0),
                    'return': ret
                })

    if not performance_data:
        print("未发现符合回测时间窗的结算数据。")
        return

    df_perf = pd.DataFrame(performance_data)

    # --- 5. 核心修正：分段逻辑 (增加异常值处理) ---
    # 将分数限制在 0-100 之间，防止产生 NaN
    df_perf['score'] = df_perf['score'].clip(0, 100)
    
    # 定义区间
    bins = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 101] # 101是为了包含100分
    labels = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90]
    
    # 转换为分段起点
    df_perf['score_range_start'] = pd.cut(df_perf['score'], bins=bins, labels=labels, right=False)
    
    # 彻底解决 NaN 问题：删除无法分段的行（如果有的话）
    df_perf = df_perf.dropna(subset=['score_range_start'])
    
    # 计算分段终点
    df_perf['score_range_start'] = df_perf['score_range_start'].astype(int)
    df_perf['score_range_end'] = df_perf['score_range_start'] + 10

    # 6. 聚合统计
    print("正在生成分数段聚合统计指标...")
    analysis_res = df_perf.groupby(['trade_date', 'strategy_name', 'score_range_start', 'score_range_end'])['return'].agg([
        ('total_trades', 'count'),
        ('win_rate', lambda x: (x > 0).mean() * 100),
        ('avg_return', 'mean'),
        ('max_return', 'max'),
        ('max_drawdown', 'min')
    ]).reset_index()

    # 7. 写入数据库 (覆盖模式)
    try:
        with engine_review.begin() as conn:
            # 写入临时表
            analysis_res.to_sql('temp_score_analysis', con=conn, if_exists='replace', index=False)
            
            # 使用 UPSERT 逻辑同步到正式表
            upsert_sql = text("""
                INSERT INTO strategy_score_analysis 
                (trade_date, strategy_name, score_range_start, score_range_end, total_trades, win_rate, avg_return, max_return, max_drawdown)
                SELECT trade_date, strategy_name, score_range_start, score_range_end, total_trades, win_rate, avg_return, max_return, max_drawdown 
                FROM temp_score_analysis
                ON DUPLICATE KEY UPDATE 
                    total_trades = VALUES(total_trades),
                    win_rate = VALUES(win_rate),
                    avg_return = VALUES(avg_return),
                    max_return = VALUES(max_return),
                    max_drawdown = VALUES(max_drawdown);
            """)
            conn.execute(upsert_sql)
            conn.execute(text("DROP TABLE IF EXISTS temp_score_analysis"))
        
        print(f"✅ 成功更新 {len(analysis_res)} 条分数段效能数据。")

        # 8. 结果简报
        latest_day = analysis_res['trade_date'].max()
        print(f"\n📊 {latest_day} 高分段表现 (80分以上):")
        print("-" * 80)
        high_score_report = analysis_res[(analysis_res['trade_date'] == latest_day) & (analysis_res['score_range_start'] >= 80)]
        print(high_score_report.sort_values(['strategy_name', 'score_range_start']).to_string(index=False))

    except Exception as e:
        print(f"❌ 数据库写入失败: {e}")

if __name__ == "__main__":
    get_score_analysis()