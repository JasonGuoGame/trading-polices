import akshare as ak
import pandas as pd
from sqlalchemy import create_engine, text
import datetime
import time

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def add_suffix(code):
    """为代码添加后缀"""
    code = str(code).zfill(6)
    return code + ".SH" if code.startswith('6') else code + ".SZ"

def sync_基礎数据_via_akshare():
    print(f"[{datetime.datetime.now()}] 启动全市场基础信息同步 (AKShare 模式)...")

    # --- 步骤 A: 更新 stocks 表 (同步 ST 状态) ---
    print("正在获取全 A 股快照以更新名称和 ST 状态...")
    try:
        df_spot = ak.stock_zh_a_spot_em()
        # 整理数据
        df_stocks = pd.DataFrame()
        df_stocks['symbol'] = df_spot['代码'].apply(add_suffix)
        df_stocks['name'] = df_spot['名称']
        
        # 只保留主板和双创，过滤掉退市或其它
        df_stocks = df_stocks[df_stocks['symbol'].str.startswith(('60', '00', '30', '688'))]

        with engine.begin() as conn:
            df_stocks.to_sql('temp_stocks', con=conn, if_exists='replace', index=False)
            conn.execute(text("""
                INSERT INTO stocks (symbol, name)
                SELECT symbol, name FROM temp_stocks
                ON DUPLICATE KEY UPDATE name = VALUES(name);
            """))
            conn.execute(text("DROP TABLE temp_stocks;"))
        print(f"✅ stocks 表同步完成，共记录 {len(df_stocks)} 只股票。")
    except Exception as e:
        print(f"❌ 同步股票名称失败: {e}")

    # --- 步骤 B & C: 更新板块及成分股关系 ---
    # 我们同步“行业板块”和“概念板块”
    board_types = [
        {"name": "行业", "list_api": ak.stock_board_industry_name_em, "cons_api": ak.stock_board_industry_cons_em},
        {"name": "概念", "list_api": ak.stock_board_concept_name_em, "cons_api": ak.stock_board_concept_cons_em}
    ]

    all_sectors = []
    all_relations = []

    for bt in board_types:
        print(f"正在获取【{bt['name']}】板块列表...")
        try:
            df_boards = bt['list_api']()
            for _, b_row in df_boards.iterrows():
                board_name = b_row['板块名称']
                # 统一前缀，方便你之前的脚本识别
                full_board_name = f"{bt['name']}-{board_name}"
                all_sectors.append(full_board_name)
                
                # 获取该板块的成分股
                try:
                    df_cons = bt['cons_api'](symbol=board_name)
                    for _, c_row in df_cons.iterrows():
                        s_code = add_suffix(c_row['代码'])
                        all_relations.append({'symbol': s_code, 'sector_name': full_board_name})
                    
                    # 适当延迟，防止被封 IP
                    time.sleep(0.1)
                except:
                    continue
            print(f"已提取 {len(df_boards)} 个{bt['name']}板块的成分。")
        except Exception as e:
            print(f"❌ 获取{bt['name']}列表失败: {e}")

    # 写入数据库
    if all_sectors and all_relations:
        df_sectors = pd.DataFrame(all_sectors, columns=['name'])
        df_relations = pd.DataFrame(all_relations).drop_duplicates()

        with engine.begin() as conn:
            conn.execute(text("SET FOREIGN_KEY_CHECKS = 0;"))
            
            # 更新 sectors 表
            df_sectors.to_sql('temp_sectors', con=conn, if_exists='replace', index=False)
            conn.execute(text("INSERT IGNORE INTO sectors (name) SELECT name FROM temp_sectors;"))
            
            # 更新关系表 (全量刷新)
            print("正在全量刷新 stock_sector_relation 表...")
            conn.execute(text("TRUNCATE TABLE stock_sector_relation;"))
            df_relations.to_sql('stock_sector_relation', con=conn, if_exists='append', index=False, chunksize=5000)
            
            conn.execute(text("SET FOREIGN_KEY_CHECKS = 1;"))
            conn.execute(text("DROP TABLE temp_sectors;"))
        
        print(f"✅ 板块与关系同步完成！(共 {len(df_sectors)} 个板块, {len(df_relations)} 条对应关系)")

if __name__ == "__main__":
    sync_基礎数据_via_akshare()