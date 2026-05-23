import os
import sys
import time
import akshare as ak
import pandas as pd
from xtquant import xtdata
from sqlalchemy import create_engine, text
import datetime
import warnings

# 1. 彻底屏蔽系统代理干扰 (针对 AKShare)
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['all_proxy'] = ''
os.environ['no_proxy'] = '*'

warnings.filterwarnings('ignore')

# --- 2. 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def add_suffix(code):
    """代码补全后缀"""
    code = str(code).zfill(6)
    return code + ".SH" if code.startswith('6') else code + ".SZ"

def get_qmt_market_values(symbol_list):
    """
    利用 MiniQMT 本地数据计算总市值
    逻辑：总股本(TotalVolume) * 最新价(lastPrice)
    """
    print(f"正在通过 MiniQMT 计算 {len(symbol_list)} 只个股的本地市值...")
    
    # 一次性获取全量快照，提高效率
    ticks = xtdata.get_full_tick(symbol_list)
    
    mv_records = []
    for symbol in symbol_list:
        detail = xtdata.get_instrument_detail(symbol)
        tick = ticks.get(symbol, {})
        
        if detail and tick:
            total_shares = detail.get('TotalVolume', 0)
            last_price = tick.get('lastPrice', 0)
            
            if total_shares > 0 and last_price > 0:
                # 计算总市值 (单位：亿元)
                total_mv = (total_shares * last_price) / 1e8
                mv_records.append({
                    'symbol': symbol,
                    'total_mv': round(total_mv, 2)
                })
    
    return pd.DataFrame(mv_records)

def sync_fundamental_and_mv():
    print(f"[{datetime.datetime.now()}] 启动 [财务指标 + QMT本地市值] 同步任务...")

    try:
        # --- 步骤 A: 从 AKShare 获取财务报表 (ROE, 净利增长) ---
        target_date = "20231231"
        print(f"正在从 AKShare 获取 {target_date} 业绩报表...")
        df_yjbb = ak.stock_yjbb_em(date=target_date)
        
        if df_yjbb.empty:
            print("❌ 未能获取到财报数据，请检查网络。")
            return

        # 初始清洗
        df_base = pd.DataFrame()
        df_base['symbol'] = df_yjbb['股票代码'].apply(add_suffix)
        df_base['name'] = df_yjbb['股票简称']
        df_base['roe'] = pd.to_numeric(df_yjbb['净资产收益率'], errors='coerce').fillna(0)
        df_base['net_profit_growth'] = pd.to_numeric(df_yjbb['净利润-同比增长'], errors='coerce').fillna(0)

        # 只保留主板和双创
        df_base = df_base[df_base['symbol'].str.startswith(('60', '00', '30', '688'))]
        symbol_list = df_base['symbol'].tolist()

        # --- 步骤 B: 调用 MiniQMT 逻辑计算市值 ---
        df_qmt_mv = get_qmt_market_values(symbol_list)

        # --- 步骤 C: 数据合并 ---
        print("合并财务数据与本地市值数据...")
        df_final = pd.merge(df_base, df_qmt_mv, on='symbol', how='left')
        df_final['total_mv'] = df_final['total_mv'].fillna(0) # 没算出来的设为0

        # --- 步骤 D: 写入数据库 (覆盖更新 roe, net_profit_growth, total_mv) ---
        print(f"整理完成，准备同步 {len(df_final)} 条个股记录到 stk_fundamental 表...")
        
        with engine.begin() as conn:
            # 1. 写入临时表
            df_final.to_sql('temp_fund_qmt_sync', con=conn, if_exists='replace', index=False)
            
            # 2. 执行 UPSERT：更新三个核心字段
            upsert_sql = text("""
                INSERT INTO stk_fundamental (symbol, name, roe, net_profit_growth, total_mv)
                SELECT symbol, name, roe, net_profit_growth, total_mv FROM temp_fund_qmt_sync
                ON DUPLICATE KEY UPDATE 
                    name = VALUES(name),
                    roe = VALUES(roe),
                    net_profit_growth = VALUES(net_profit_growth),
                    total_mv = VALUES(total_mv);
            """)
            conn.execute(upsert_sql)
            conn.execute(text("DROP TABLE IF EXISTS temp_fund_qmt_sync;"))

        print(f"✅ 同步成功！ROE、净利增长与【总市值】已全部更新到位。")

    except Exception as e:
        print(f"❌ 运行失败，原因: {e}")

if __name__ == "__main__":
    # 运行前请确保 MiniQMT 客户端已打开并登录
    sync_fundamental_and_mv()