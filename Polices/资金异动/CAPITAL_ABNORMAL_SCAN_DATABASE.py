import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def init_db():
    """初始化异动记录表"""
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS stk_capital_abnormal (
        symbol VARCHAR(20),
        name VARCHAR(100),
        trade_date DATE,
        vol_ratio DECIMAL(10, 2) COMMENT '日线爆量倍数',
        surge_count INT COMMENT '分时脉冲次数',
        max_surge_ret DECIMAL(10, 2) COMMENT '单分最大涨幅%',
        surge_times TEXT COMMENT '异动具体时间点',
        last_update DATETIME COMMENT '记录最后更新时间',
        PRIMARY KEY (symbol, trade_date)
    ) ENGINE=InnoDB;
    """
    with engine.begin() as conn:
        conn.execute(text(create_table_sql))

def analyze_intraday_surge(symbol, date_str):
    """
    分析分时数据，检测是否有‘主力脉冲拉升’记录
    """
    query = f"""
    SELECT trade_time, close, volume, amount 
    FROM stk_min_kline 
    WHERE symbol = '{symbol}' AND DATE(trade_time) = '{date_str}'
    ORDER BY trade_time ASC
    """
    df_min = pd.read_sql(query, engine)
    if len(df_min) < 50: return None

    # 计算分钟涨幅和相对量能
    df_min['ret'] = df_min['close'].pct_change()
    df_min['vol_ma10'] = df_min['volume'].rolling(10).mean()
    df_min['vol_ratio'] = df_min['volume'] / (df_min['vol_ma10'].shift(1) + 1)

    # 寻找主力脉冲：单分钟涨幅 > 0.8% 且 成交量放大 5 倍以上
    surges = df_min[(df_min['ret'] > 0.008) & (df_min['vol_ratio'] > 5.0)]
    
    if not surges.empty:
        return {
            'surge_count': len(surges),
            'max_surge_ret': round(surges['ret'].max() * 100, 2),
            # 将所有异动时间点转为逗号分隔的字符串存入数据库
            'surge_times': ",".join(surges['trade_time'].dt.strftime('%H:%M').tolist())
        }
    return None

def save_to_mysql(results_list, trade_date):
    """
    将分析结果保存到数据库，支持重复运行覆盖更新
    """
    if not results_list:
        return

    df = pd.DataFrame(results_list)
    df['trade_date'] = trade_date
    df['last_update'] = datetime.datetime.now()

    # 映射 DataFrame 列名到数据库表列名
    df_to_save = df.rename(columns={
        '代码': 'symbol',
        '名称': 'name',
        '爆量倍数': 'vol_ratio',
        '分时脉冲次数': 'surge_count',
        '单分最大涨幅%': 'max_surge_ret',
        '异动时间点': 'surge_times'
    })

    try:
        with engine.begin() as conn:
            # 1. 写入临时表
            df_to_save.to_sql('temp_abnormal', con=conn, if_exists='replace', index=False)
            
            # 2. 执行 UPSERT (存在则更新，不存在则插入)
            upsert_sql = text("""
                INSERT INTO stk_capital_abnormal (symbol, name, trade_date, vol_ratio, surge_count, max_surge_ret, surge_times, last_update)
                SELECT symbol, name, trade_date, vol_ratio, surge_count, max_surge_ret, surge_times, last_update FROM temp_abnormal
                ON DUPLICATE KEY UPDATE 
                    surge_count = VALUES(surge_count),
                    max_surge_ret = VALUES(max_surge_ret),
                    surge_times = VALUES(surge_times),
                    last_update = VALUES(last_update),
                    vol_ratio = VALUES(vol_ratio);
            """)
            conn.execute(upsert_sql)
            conn.execute(text("DROP TABLE IF EXISTS temp_abnormal;"))
        print(f"✅ 已成功保存/更新 {len(df)} 条异动记录至数据库。")
    except Exception as e:
        print(f"❌ 数据库写入失败: {e}")

def run_capital_monitor():
    # init_db() # 确保表存在
    print(f"[{datetime.datetime.now()}] 启动资金异动扫描...")

    # 1. 第一步：日线初筛 (找出今日爆量个股)
    latest_date_res = pd.read_sql("SELECT MAX(trade_date) FROM stk_factors", engine)
    latest_date = latest_date_res.iloc[0, 0]
    
    if latest_date is None:
        print("错误：因子库为空，请先同步因子数据。")
        return

    initial_query = f"""
        SELECT f.symbol, f.f_vol_ratio, s.name 
        FROM stk_factors f
        JOIN stocks s ON f.symbol = s.symbol
        WHERE f.trade_date = '{latest_date}' 
          AND f.f_vol_ratio > 2.5 
        #   AND (f.symbol LIKE '60%%' OR f.symbol LIKE '00%%')
          AND s.name NOT LIKE '%%ST%%'
    """
    candidates = pd.read_sql(initial_query, engine)
    print(f"日线爆量个股: {len(candidates)} 只，开始扫描分时脉冲...")

    results = []
    for i, row in candidates.iterrows():
        sym = row['symbol']
        surge_info = analyze_intraday_surge(sym, latest_date)
        
        if surge_info:
            results.append({
                '代码': sym,
                '名称': row['name'],
                '爆量倍数': round(row['f_vol_ratio'], 2),
                '分时脉冲次数': surge_info['surge_count'],
                '单分最大涨幅%': surge_info['max_surge_ret'],
                '异动时间点': surge_info['surge_times']
            })

    # 2. 输出展示并保存
    if results:
        df_res = pd.DataFrame(results).sort_values('分时脉冲次数', ascending=False)
        print("\n" + "🚨" * 10)
        print(df_res.to_string(index=False))
        print("🚨" * 10)
        
        # 写入数据库
        save_to_mysql(results, latest_date)
    else:
        print("\n今日暂未发现显著的资金抢筹异动。")

if __name__ == "__main__":
    run_capital_monitor()