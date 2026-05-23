import pandas as pd
from sqlalchemy import create_engine, text
import datetime
import warnings

warnings.filterwarnings('ignore')

# --- 1. 数据库配置 (指向 trading_review 库) ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review'
engine = create_engine(DB_URL)

def maintain_stock_pool_capacity():
    print(f"[{datetime.datetime.now()}] 启动 stock_pools 表 7 日库容清理任务...")

    try:
        with engine.connect() as conn:
            # 步骤 A: 查出数据库中目前存在的所有不重复交易日，并按从新到旧排序
            date_query = text("SELECT DISTINCT trade_date FROM stock_pools ORDER BY trade_date DESC LIMIT 7")
            date_rows = conn.execute(date_query).fetchall()
            
            # 转换为日期列表
            existing_dates = [r[0] for r in date_rows]

            # 校验：如果数据库里总共还不到 7 天的数据，则无需清理
            if len(existing_dates) < 7:
                print(f"💡 当前数据库仅存有 {len(existing_dates)} 个交易日的数据，未达到 7 日清理门槛。")
                return

            # 步骤 B: 确定截止日期 (列表中的最后一个即为第 7 个最新日期)
            cutoff_date = existing_dates[-1]
            print(f"📅 截止日期确定为: {cutoff_date} (保留此日及之后的数据)")

            # 步骤 C: 执行删除操作 (开启事务)
            with engine.begin() as trans_conn:
                delete_sql = text("DELETE FROM stock_pools WHERE trade_date < :d")
                result = trans_conn.execute(delete_sql, {"d": cutoff_date})
                print(f"✅ 清理成功！已物理删除 {result.rowcount} 条 7 日前的陈旧数据。")

    except Exception as e:
        print(f"❌ 运行失败，原因: {e}")

if __name__ == "__main__":
    maintain_stock_pool_capacity()