import os

# 1. 彻底屏蔽系统代理干扰
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['all_proxy'] = ''
os.environ['no_proxy'] = '*'

import akshare as ak
import pandas as pd
from sqlalchemy import create_engine, text
import datetime

# --- 2. 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def init_db():
    """初始化资金流向表"""
    sql = """
    CREATE TABLE IF NOT EXISTS stk_sector_fund_flow (
        sector_name VARCHAR(100) COMMENT '行业名称',
        trade_date DATE COMMENT '交易日期',
        net_inflow_amount DECIMAL(18, 2) COMMENT '主力净流入额(亿)',
        net_inflow_rate DECIMAL(10, 2) COMMENT '主力净流入占比%',
        top_stock_name VARCHAR(100) COMMENT '领涨龙头',
        PRIMARY KEY (sector_name, trade_date)
    ) ENGINE=InnoDB;
    """
    with engine.begin() as conn:
        conn.execute(text(sql))

def analyze_and_save_money_flow():
    print(f"[{datetime.datetime.now()}] 启动今日资金流向深度分析与入库流程...")
    init_db()

    # 获取今天的日期
    target_date = datetime.date.today()

    try:
        # 1. 从 AKShare 获取行业资金流向排行
        df_raw = ak.stock_sector_fund_flow_rank(indicator="今日")
        if df_raw.empty:
            print("未能获取到数据，请确认当前是否在交易时间。")
            return

        # 2. 数据清洗与整理
        df_res = pd.DataFrame()
        df_res['sector_name'] = df_raw['名称']
        df_res['net_inflow_amount'] = (pd.to_numeric(df_raw['今日主力净流入-净额'], errors='coerce') / 1e8).round(2)
        df_res['net_inflow_rate'] = pd.to_numeric(df_raw['今日主力净流入-净占比'], errors='coerce').round(2)
        df_res['top_stock_name'] = df_raw['今日主力净流入最大股']
        df_res['trade_date'] = target_date

        # 3. 写入数据库 (核心修改：先删除旧数据，再插入新数据)
        print(f"正在替换数据库中 {target_date} 的原有记录并更新数据...")
        
        with engine.begin() as conn:
            # A. 删除该日期下已有的所有记录 (实现替换逻辑)
            delete_sql = text("DELETE FROM stk_sector_fund_flow WHERE trade_date = :t_date")
            conn.execute(delete_sql, {"t_date": target_date})
            
            # B. 批量写入新抓取到的数据
            # 使用 if_exists='append'，因为上面已经清空了当天的坑位
            df_res.to_sql('stk_sector_fund_flow', con=conn, if_exists='append', index=False, chunksize=1000)
        
        # 4. 控制台预览
        inflow_top = df_res[df_res['net_inflow_amount'] > 0].sort_values('net_inflow_amount', ascending=False).head(10)
        print("\n" + "💰" * 8 + f" {target_date} 资金净流入 TOP 10 (已更新) " + "💰" * 8)
        print("-" * 75)
        print(inflow_top[['sector_name', 'net_inflow_amount', 'net_inflow_rate', 'top_stock_name']].to_string(index=False))
        
        print(f"\n✅ 同步完成！已全量替换 {target_date} 的资金流向数据。")

    except Exception as e:
        print(f"❌ 运行失败: {e}")

if __name__ == "__main__":
    analyze_and_save_money_flow()