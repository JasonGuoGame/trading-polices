import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime

# --- 1. 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def calculate_market_dipbuy_metrics():
    print(f"[{datetime.datetime.now()}] 正在评估低吸（抄底）模式赚钱效应...")

    with engine.connect() as conn:
        # A. 获取最近三个交易日 (t2:今天, t1:昨天, t0:前天)
        # 逻辑：我们在 t1 找信号，看在 t2 的表现
        date_res = conn.execute(text("SELECT DISTINCT trade_date FROM stk_daily_kline ORDER BY trade_date DESC LIMIT 3")).fetchall()
        if len(date_res) < 3:
            print("数据不足（需要至少3天日线数据）。")
            return
        
        t2_today = date_res[0][0]
        t1_yesterday = date_res[1][0]
        t0_prev_day = date_res[2][0]

        # B. 步骤 1：识别【昨日】的低吸候选股
        # 逻辑：长下影线 或 盘中跌幅 > 4%
        yest_setup_sql = text("""
            SELECT symbol, open, high, low, close, 
                   (SELECT close FROM stk_daily_kline WHERE symbol = k.symbol AND trade_date = :t0) as prev_close
            FROM stk_daily_kline k
            WHERE trade_date = :t1
              AND (symbol LIKE '60%' OR symbol LIKE '00%')
        """)
        df_yest = pd.read_sql(yest_setup_sql, conn, params={"t1": t1_yesterday, "t0": t0_prev_day})
        
        # 计算昨日形态
        df_yest['range'] = df_yest['high'] - df_yest['low']
        df_yest['body'] = (df_yest['close'] - df_yest['open']).abs()
        df_yest['lower_shadow'] = df_yest[['open', 'close']].min(axis=1) - df_yest['low']
        df_yest['max_dip'] = (df_yest['low'] - df_yest['prev_close']) / df_yest['prev_close']
        
        # 定义长下影：下影线 > 实体 2 倍 且 占全天振幅 60% 以上
        is_long_shadow = (df_yest['lower_shadow'] > df_yest['body'] * 2) & (df_yest['lower_shadow'] / (df_yest['range'] + 0.01) > 0.6)
        # 定义深水：盘中跌幅 > 4%
        is_deep_water = df_yest['max_dip'] < -0.04
        
        yest_setups = df_yest[is_long_shadow | is_deep_water].copy()
        
        if yest_setups.empty:
            print(f"昨日 ({t1_yesterday}) 全市场未发现低吸形态，跳过今日评估。")
            return

        # C. 步骤 2：追踪这些标的在【今日】的表现
        yest_symbols = yest_setups['symbol'].tolist()
        today_perf_sql = text("""
            SELECT symbol, close as today_close, high as today_high 
            FROM stk_daily_kline 
            WHERE trade_date = :t2 AND symbol IN :symbols
        """)
        df_today = pd.read_sql(today_perf_sql, conn, params={"t2": t2_today, "symbols": yest_symbols})

        # 合并分析
        df_eval = pd.merge(yest_setups, df_today, on='symbol')
        
        # --- 计算核心指标 ---
        # 1. 长下影数量
        long_lower_shadow_count = len(yest_setups[is_long_shadow])
        
        # 2. 低吸成功率 (今日收盘 > 昨日收盘)
        win_count = len(df_eval[df_eval['today_close'] > df_eval['close']])
        dipbuy_success_rate = (win_count / len(df_eval)) * 100
        
        # 3. 次日平均收益
        df_eval['ret'] = (df_eval['today_close'] - df_eval['close']) / df_eval['close']
        avg_next_day_return = df_eval['ret'].mean() * 100
        
        # 4. 深水反包数量 (今日收盘价 > 昨日最高价)
        deep_water_reversal_count = len(df_eval[df_eval['today_close'] >= df_eval['high']])

    # --- D. 汇总并写入数据库 (UPSERT) ---
    result_metrics = {
        'trade_date': t2_today,
        'long_lower_shadow_count': int(long_lower_shadow_count),
        'dipbuy_success_rate': float(round(dipbuy_success_rate, 2)),
        'avg_next_day_return': float(round(avg_next_day_return, 2)),
        'deep_water_reversal_count': int(deep_water_reversal_count)
    }

    df_save = pd.DataFrame([result_metrics])
    
    try:
        with engine.begin() as conn:
            df_save.to_sql('temp_dipbuy_metrics', con=conn, if_exists='replace', index=False)
            upsert_sql = text("""
                INSERT INTO market_dipbuy_metrics (trade_date, long_lower_shadow_count, dipbuy_success_rate, avg_next_day_return, deep_water_reversal_count)
                SELECT * FROM temp_dipbuy_metrics
                ON DUPLICATE KEY UPDATE 
                    long_lower_shadow_count = VALUES(long_lower_shadow_count),
                    dipbuy_success_rate = VALUES(dipbuy_success_rate),
                    avg_next_day_return = VALUES(avg_next_day_return),
                    deep_water_reversal_count = VALUES(deep_water_reversal_count);
            """)
            conn.execute(upsert_sql)
            conn.execute(text("DROP TABLE IF EXISTS temp_dipbuy_metrics;"))

        # --- 控制台输出报告 ---
        print("\n" + "🧘" * 10 + f" 今日低吸环境评估 ({t2_today}) " + "🧘" * 10)
        print("-" * 75)
        print(f"📌 昨日潜在低吸样本: {len(df_eval):<4} 只")
        print(f"✅ 今日低吸成功率: {dipbuy_success_rate:.1f}%")
        print(f"💰 次日平均收益: {avg_next_day_return:+.2f}%")
        print(f"🔄 深水成功反包数: {deep_water_reversal_count}")
        print("-" * 75)

        if dipbuy_success_rate > 60 and avg_next_day_return > 1.0:
            msg = "🟢 强：低吸不仅有肉，而且是大肉！市场承接力极强，跌了就有人买。"
        elif dipbuy_success_rate > 45:
            msg = "🟡 中：有局部肉，但分化大。需结合主线个股操作。"
        else:
            msg = "🔴 弱：抄底大面！昨日的长下影今天全成了‘吊颈线’。操作：严禁抄底。"
        print(f"💡 研判：{msg}\n")

    except Exception as e:
        print(f"❌ 数据库操作失败: {e}")

if __name__ == "__main__":
    calculate_market_dipbuy_metrics()