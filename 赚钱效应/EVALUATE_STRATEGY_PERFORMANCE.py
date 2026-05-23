import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def get_strategy_performance():
    print(f"[{datetime.datetime.now()}] 启动策略绩效回测结算系统...")

    with engine.connect() as conn:
        # 1. 获取最近两个交易日
        dates = conn.execute(text("SELECT DISTINCT trade_date FROM stk_daily_kline ORDER BY trade_date DESC LIMIT 2")).fetchall()
        if len(dates) < 2:
            print("数据不足，无法进行次日绩效结算。")
            return
        
        t_today = dates[0][0]      # 今日（结算日）
        t_yest = dates[1][0]       # 昨日（信号日）
        print(f"📈 结算逻辑：昨日({t_yest}) 产生信号 -> 今日({t_today}) 收盘表现")

        # 2. 定义要评估的策略字典 {策略名: SQL筛选语句}
        # 注意：这里的 SQL 是针对 t_yest（昨日）运行的
        strategies = {
            "均线粘合突破": f"""
                SELECT symbol FROM stk_factors 
                WHERE trade_date = '{t_yest}' AND f_ma_cohesion < 0.02 AND f_vol_ratio > 2.0
            """,
            "MACD-0轴金叉": f"""
                SELECT symbol FROM stk_factors 
                WHERE trade_date = '{t_yest}' AND f_macd_dif > f_macd_dea AND f_macd_dif > 0
            """,
            "主力异动抢筹": f"""
                SELECT symbol FROM stk_capital_abnormal 
                WHERE trade_date = '{t_yest}' AND surge_count >= 3
            """,
            "低位爆量起爆": f"""
                SELECT symbol FROM stk_factors 
                WHERE trade_date = '{t_yest}' AND f_vol_ratio > 3.0 AND f_dist_high > 0.3
            """
        }

        performance_results = []

        # 3. 循环评估每个策略
        for name, sql in strategies.items():
            # A. 找出昨日符合条件的股票
            sig_df = pd.read_sql(text(sql), conn)
            sig_list = sig_df['symbol'].tolist()
            
            if not sig_list:
                performance_results.append({
                    'trade_date': t_today, 'strategy_name': name, 'signal_count': 0,
                    'avg_return': 0, 'win_rate': 0, 'best_return': 0, 'worst_return': 0
                })
                continue

            # B. 计算这些股票在今日的收益率
            # 收益率 = (今日收盘 - 昨日收盘) / 昨日收盘
            perf_sql = text("""
                SELECT 
                    (k_t.close - k_y.close) / k_y.close * 100 as ret
                FROM stk_daily_kline k_t
                JOIN stk_daily_kline k_y ON k_t.symbol = k_y.symbol
                WHERE k_t.trade_date = :today AND k_y.trade_date = :yest
                  AND k_t.symbol IN :symbols
            """)
            returns = pd.read_sql(perf_sql, conn, params={"today": t_today, "yest": t_yest, "symbols": sig_list})['ret']

            if not returns.empty:
                performance_results.append({
                    'trade_date': t_today,
                    'strategy_name': name,
                    'signal_count': len(returns),
                    'avg_return': float(returns.mean()),
                    'win_rate': float((returns > 0).mean() * 100),
                    'best_return': float(returns.max()),
                    'worst_return': float(returns.min())
                })

    # 4. 写入数据库
    if performance_results:
        df_final = pd.DataFrame(performance_results)
        try:
            with engine.begin() as conn:
                df_final.to_sql('temp_perf', con=conn, if_exists='replace', index=False)
                # 使用 INSERT IGNORE 或 REPLACE 确保日期+策略唯一（假设你给这两列加了唯一索引）
                upsert_sql = text("""
                    INSERT INTO strategy_daily_performance (trade_date, strategy_name, signal_count, avg_return, win_rate, best_return, worst_return)
                    SELECT trade_date, strategy_name, signal_count, avg_return, win_rate, best_return, worst_return FROM temp_perf
                """)
                conn.execute(upsert_sql)
                conn.execute(text("DROP TABLE temp_perf"))
            
            # --- 结果展示 ---
            print("\n" + "🏆" * 10 + f" 策略绩效排行榜 ({t_today}) " + "🏆" * 10)
            print("-" * 85)
            print(df_final.sort_values('avg_return', ascending=False).to_string(index=False))
            print("-" * 85)
            
            best_strat = df_final.sort_values('avg_return', ascending=False).iloc[0]
            print(f"💡 结论：今日最赚钱模式是【{best_strat['strategy_name']}】，平均收益 {best_strat['avg_return']:.2f}%")
            print("🏆" * 32 + "\n")

        except Exception as e:
            print(f"❌ 写入绩效表失败: {e}")

if __name__ == "__main__":
    get_strategy_performance()