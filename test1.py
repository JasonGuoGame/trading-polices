# --- 增加一个保存函数 ---
def save_performance_to_db(summary_df):
    if summary_df.empty: return
    try:
        with engine_review.begin() as conn:
            summary_df.to_sql('temp_perf_sync', con=conn, if_exists='replace', index=False)
            conn.execute(text("""
                INSERT INTO strategy_performance_history 
                (trade_date, strategy_name, signal_count, avg_return, win_rate, best_return, worst_return)
                SELECT trade_date, strategy, 信号总数, `平均收益%`, `胜率%`, `单笔最猛%`, `单笔最惨%` 
                FROM temp_perf_sync
                ON DUPLICATE KEY UPDATE 
                    signal_count = VALUES(signal_count), win_rate = VALUES(win_rate), avg_return = VALUES(avg_return);
            """))
        print(f"✅ 策略绩效历史已同步。")
    except Exception as e:
        print(f"❌ 写入失败: {e}")

# 修改 get_strategy_performance 函数中的汇总逻辑
# (在计算完 results 后)
df_res = pd.DataFrame(results)
# 增加 trade_date 维度进行分组
summary = df_res.groupby(['trade_date', 'strategy'])['return'].agg([
    ('信号总数', 'count'),
    ('平均收益%', lambda x: round(x.mean(), 2)),
    ('胜率%', lambda x: round((x > 0).mean() * 100, 2)),
    ('单笔最猛%', lambda x: round(x.max(), 2)),
    ('单笔最惨%', lambda x: round(x.min(), 2))
]).reset_index()

save_performance_to_db(summary)