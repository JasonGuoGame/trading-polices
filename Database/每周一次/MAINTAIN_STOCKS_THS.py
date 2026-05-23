import os
import sys
# 彻底屏蔽系统代理干扰
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['all_proxy'] = ''
os.environ['no_proxy'] = '*'

import akshare as ak
import pandas as pd
from sqlalchemy import create_engine, text
import datetime
import time
import random
import warnings

warnings.filterwarnings('ignore')

# --- 1. 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def add_suffix(code):
    """为 6 位代码添加后缀"""
    code = str(code).zfill(6)
    return code + ".SH" if code.startswith('6') else code + ".SZ"

def get_ths_cons_api():
    """
    动态检测 AKShare 中同花顺成分股的接口名称
    """
    if hasattr(ak, "stock_board_cons_ths"):
        return ak.stock_board_cons_ths
    if hasattr(ak, "stock_board_concept_cons_ths"):
        return ak.stock_board_concept_cons_ths
    return None

def sync_via_ths_logic():
    print(f"[{datetime.datetime.now()}] 🚀 启动全市场基础信息同步 (同花顺数据源)...")

    # 1. 自动检测接口
    cons_api = get_ths_cons_api()
    if not cons_api:
        print("❌ 错误：在你的 AKShare 版本中找不到同花顺成分股接口，请运行 pip install akshare --upgrade")
        return

    all_stock_info = {} # {symbol: name} -> 处理ST
    all_relations = []  # [{'symbol':, 'sector_name':}]
    all_sector_names = []

    # 2. 获取同花顺概念列表
    print("正在获取同花顺概念板块列表...")
    try:
        # 这个接口通常返回：日期, 概念名称, 代码, 网址
        df_boards = ak.stock_board_concept_name_ths()
        if df_boards.empty:
            print("❌ 获取概念列表为空。")
            return
        
        # 自动识别名称列
        name_col = next((c for c in df_boards.columns if '名称' in c or 'name' in c.lower()), None)
        board_list = df_boards[name_col].tolist()
        
        print(f"共发现 {len(board_list)} 个概念板块，开始穿透抓取成分股...")

        # 为了防止被封 IP，我们只抓取前 300 个核心热门板块（已足够覆盖大部分股票）
        # 如果需要全量，请去掉 [:300]
        for b_name in board_list[:300]:
            full_sector_name = f"概念-{b_name}"
            all_sector_names.append(full_sector_name)
            
            try:
                # 调用检测到的成分股接口
                df_cons = cons_api(symbol=b_name)
                
                if df_cons is None or df_cons.empty:
                    continue

                # 识别列名
                c_code_col = next((c for c in df_cons.columns if '代码' in c or 'code' in c.lower()), None)
                c_name_col = next((c for c in df_cons.columns if '名称' in c or 'name' in c.lower()), None)

                for _, row in df_cons.iterrows():
                    symbol = add_suffix(row[c_code_col])
                    s_name = row[c_name_col]
                    
                    # 过滤主板和双创
                    if symbol.startswith(('60', '00', '30', '688')):
                        all_stock_info[symbol] = s_name
                        all_relations.append({'symbol': symbol, 'sector_name': full_sector_name})

                print(f"   已完成: {b_name} (含 {len(df_cons)} 只股票)")
                # 同花顺反爬严，必须延迟
                time.sleep(random.uniform(0.8, 2.0))

            except Exception as e:
                print(f"   ⚠️ 跳过板块 {b_name}: {e}")
                time.sleep(5) # 报错了多歇一会儿
                continue

    except Exception as e:
        print(f"❌ 运行失败: {e}")
        return

    # --- 3. 数据库持久化 ---
    if not all_stock_info:
        print("❌ 未能获取到任何数据。")
        return

    print(f"\n📊 抓取总结：获得股票 {len(all_stock_info)} 只, 板块关系 {len(all_relations)} 条。")

    try:
        with engine.begin() as conn:
            conn.execute(text("SET FOREIGN_KEY_CHECKS = 0;"))
            
            # A. 更新 stocks 表 (同步 ST 状态)
            df_stocks = pd.DataFrame([{'symbol': k, 'name': v} for k, v in all_stock_info.items()])
            df_stocks.to_sql('temp_stocks_ths', con=conn, if_exists='replace', index=False)
            conn.execute(text("""
                INSERT INTO stocks (symbol, name)
                SELECT symbol, name FROM temp_stocks_ths
                ON DUPLICATE KEY UPDATE name = VALUES(name);
            """))
            
            # B. 更新 sectors 表
            df_sectors = pd.DataFrame(list(set(all_sector_names)), columns=['name'])
            df_sectors.to_sql('temp_sectors_ths', con=conn, if_exists='replace', index=False)
            conn.execute(text("INSERT IGNORE INTO sectors (name) SELECT name FROM temp_sectors_ths;"))
            
            # C. 物理刷新关系表
            print("正在重新构建板块关系表...")
            conn.execute(text("TRUNCATE TABLE stock_sector_relation;"))
            df_rel = pd.DataFrame(all_relations).drop_duplicates()
            df_rel.to_sql('stock_sector_relation', con=conn, if_exists='append', index=False, chunksize=5000)
            
            conn.execute(text("SET FOREIGN_KEY_CHECKS = 1;"))
            conn.execute(text("DROP TABLE IF EXISTS temp_stocks_ths;"))
            conn.execute(text("DROP TABLE IF EXISTS temp_sectors_ths;"))
        
        print(f"✅ 同步成功！股票名称和同花顺板块关系已全部更新。")

    except Exception as e:
        print(f"❌ 数据库写入失败: {e}")

if __name__ == "__main__":
    sync_via_ths_logic()