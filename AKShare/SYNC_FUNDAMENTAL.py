import akshare as ak
import pandas as pd
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def add_suffix(code):
    if str(code).startswith('6'):
        return str(code) + ".SH"
    else:
        return str(code) + ".SZ"

def sync_shareholders_data():
    print(f"[{datetime.datetime.now()}] 正在从 AKShare 获取最新筹码数据...")
    
    try:
        # 1. 获取原始数据
        df_raw = ak.stock_zh_a_gdhs()
        
        if df_raw.empty:
            print("未能获取到数据。")
            return

        # 2. 根据你提供的列名进行精确映射
        # 代码 -> symbol
        # 名称 -> name
        # 股东户数-增减比例 -> holder_change_rate
        # 股东户数-本次 -> holder_count
        # 户均持股市值 -> avg_hold_amount
        # 公告日期 -> last_update
        
        df_final = pd.DataFrame()
        df_final['symbol'] = df_raw['代码'].apply(add_suffix)
        df_final['name'] = df_raw['名称']
        
        # 使用你提供的具体列名提取数据
        df_final['holder_change_rate'] = pd.to_numeric(df_raw['股东户数-增减比例'], errors='coerce')
        df_final['holder_count'] = pd.to_numeric(df_raw['股东户数-本次'], errors='coerce')
        df_final['avg_hold_amount'] = pd.to_numeric(df_raw['户均持股市值'], errors='coerce')
        df_final['last_update'] = pd.to_datetime(df_raw['公告日期']).dt.date

        # 3. 数据清洗：剔除无效行
        df_final = df_final.dropna(subset=['holder_change_rate'])

        # 4. 写入数据库
        print(f"整理完成，准备同步 {len(df_final)} 条记录到 stk_fundamental...")
        
        with engine.begin() as conn:
            # 写入临时表
            df_final.to_sql('temp_fund_sync', con=conn, if_exists='replace', index=False)
            
            # 执行 UPSERT (存在则更新，不存在则插入)
            upsert_sql = text("""
                INSERT INTO stk_fundamental (symbol, name, holder_count, holder_change_rate, avg_hold_amount, last_update)
                SELECT symbol, name, holder_count, holder_change_rate, avg_hold_amount, last_update FROM temp_fund_sync
                ON DUPLICATE KEY UPDATE 
                    holder_count = VALUES(holder_count),
                    holder_change_rate = VALUES(holder_change_rate),
                    avg_hold_amount = VALUES(avg_hold_amount),
                    last_update = VALUES(last_update);
            """)
            conn.execute(upsert_sql)
            conn.execute(text("DROP TABLE IF EXISTS temp_fund_sync;"))
            
        print(f"✅ 筹码分布数据同步成功！")

    except Exception as e:
        print(f"❌ 同步过程中发生错误: {e}")

if __name__ == "__main__":
    sync_shareholders_data()