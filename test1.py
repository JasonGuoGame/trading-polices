import os
import sys

# 1. 【核心修复】彻底屏蔽系统代理，防止东财/同花顺接口断线
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['all_proxy'] = ''
os.environ['no_proxy'] = '*'

import akshare as ak
import pandas as pd
from sqlalchemy import create_engine, text
import datetime
import warnings

warnings.filterwarnings('ignore')

# --- 2. 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def add_suffix(code):
    """为 6 位代码添加后缀"""
    code = str(code).zfill(6)
    if code.startswith('6'):
        return code + ".SH"
    else:
        return code + ".SZ"

def sync_stock_names_only():
    print(f"[{datetime.datetime.now()}] 启动股票名称与 ST 状态同步任务...")

    try:
        # 3. 获取全 A 股实时快照 
        # 这个接口包含最准确的实时简称（包含 ST, *ST, 退, 或是摘帽后的新名字）
        print("正在获取全市场个股快照...")
        df_spot = ak.stock_zh_a_spot_em()
        
        if df_spot.empty:
            print("❌ 错误：未能获取到行情快照数据。")
            return

        # 4. 提取并清洗数据
        # 我们只需要代码和名称两列
        df_final = pd.DataFrame()
        df_final['symbol'] = df_spot['代码'].apply(add_suffix)
        df_final['name'] = df_spot['名称']

        # 过滤：只要主板、创业板、科创板
        df_final = df_final[df_final['symbol'].str.startswith(('60', '00', '30', '688'))]

        print(f"整理完成，准备同步 {len(df_final)} 只个股名称...")

        # 5. 执行数据库 UPSERT (覆盖更新)
        # 这种写法不会影响数据库里的其他表
        with engine.begin() as conn:
            # 写入临时表
            df_final.to_sql('temp_stocks_update', con=conn, if_exists='replace', index=False)
            
            # 执行合并更新逻辑
            # 如果 symbol 存在，只更新 name；如果 symbol 不存在，则插入新行
            upsert_sql = text("""
                INSERT INTO stocks (symbol, name)
                SELECT symbol, name FROM temp_stocks_update
                ON DUPLICATE KEY UPDATE 
                    name = VALUES(name);
            """)
            conn.execute(upsert_sql)
            
            # 删除临时表
            conn.execute(text("DROP TABLE IF EXISTS temp_stocks_update;"))

        print(f"✅ 同步成功！stocks 表已更新，ST 状态已对齐最新行情。")

    except Exception as e:
        print(f"❌ 运行失败，原因: {e}")

if __name__ == "__main__":
    sync_stock_names_only()