import pandas as pd
from sqlalchemy import create_engine, text
import datetime
import warnings

warnings.filterwarnings('ignore')

# --- 1. 数据库配置 ---
# 行情及异动库
engine_quant = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')
# 策略及复盘库
engine_review = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')

def clean_table_by_date_rank(engine, db_name, table_name, keep_days=10):
    """
    通用清理函数：保留指定表最近的 N 个交易日数据
    """
    print(f"--- 正在维护 {db_name}.{table_name} ---")
    
    try:
        with engine.connect() as conn:
            # 1. 查出数据库中现有的最新 N 个交易日
            date_query = text(f"SELECT DISTINCT trade_date FROM {table_name} ORDER BY trade_date DESC LIMIT {keep_days}")
            date_rows = conn.execute(date_query).fetchall()
            existing_dates = [r[0] for r in date_rows]

            if len(existing_dates) < keep_days:
                print(f"💡 数据量不足 {keep_days} 天，无需清理。")
                return

            # 2. 确定截止线（第 7 个日期的值）
            cutoff_date = existing_dates[-1]
            print(f"📅 截止日期: {cutoff_date} | 保留 {existing_dates[0]} 至 {cutoff_date}")

            # 3. 开启事务执行物理删除
            with engine.begin() as trans_conn:
                del_sql = text(f"DELETE FROM {table_name} WHERE trade_date < :d")
                res = trans_conn.execute(del_sql, {"d": cutoff_date})
                print(f"✅ 清理完成，移除 {res.rowcount} 条旧记录。")

    except Exception as e:
        print(f"❌ 清理 {table_name} 出错: {e}")

def run_all_maintenance():
    print(f"[{datetime.datetime.now()}] 启动全系统数据库维护程序...")

    # A. 维护 quant_db 中的三张异动/资金流表
    quant_tables = [
        "stk_market_attack_log",   # 进攻/撤退日志
        "stk_sector_fund_flow",    # 板块资金流向
        "stk_capital_abnormal"     # 资金异动明细
    ]
    for tbl in quant_tables:
        clean_table_by_date_rank(engine_quant, "quant_db", tbl, keep_days=10)

    # B. 维护 trading_review 中的股票池表
    clean_table_by_date_rank(engine_review, "trading_review", "stock_pools", keep_days=10)

    print(f"\n[{datetime.datetime.now()}] 🎉 所有表已成功维持在最近 10 个交易日的容量！")

if __name__ == "__main__":
    run_all_maintenance()