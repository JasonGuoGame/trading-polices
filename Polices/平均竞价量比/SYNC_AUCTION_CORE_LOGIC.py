import pandas as pd
import numpy as np
from xtquant import xtdata
from sqlalchemy import create_engine, text
import datetime
import time

# --- 1. 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def add_suffix(code):
    code = str(code).zfill(6)
    return code + ".SH" if code.startswith('6') else code + ".SZ"

def sync_auction_by_table_history():
    print(f"[{datetime.datetime.now()}] 启动闭环竞价量比分析系统...")
    xtdata.enable_hello = False
    today = datetime.date.today()

    # 1. 获取全市场股票及基本信息
    all_stocks = [s for s in xtdata.get_stock_list_in_sector('沪深A股') if s.startswith(('60', '00', '30', '688'))]
    
    # 2. 从数据库中一次性提取所有股票的历史竞价平均额 (过去5个记录日)
    # 这样可以避免在循环里查询数据库，极大提升运行速度
    print("正在从本地表 stk_auction_signal 提取历史均值...")
    hist_avg_sql = """
    SELECT symbol, AVG(auction_amount) as hist_avg_amt, COUNT(trade_date) as record_count
    FROM (
        SELECT symbol, auction_amount, trade_date,
               ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY trade_date DESC) as rn
        FROM stk_auction_signal
        WHERE trade_date < CURDATE()
    ) t
    WHERE rn <= 5
    GROUP BY symbol
    """
    with engine.connect() as conn:
        df_hist = pd.read_sql(text(hist_avg_sql), conn)
    
    # 转为字典映射：{ 'symbol': (hist_avg_amt, record_count) }
    hist_map = df_hist.set_index('symbol')[['hist_avg_amt', 'record_count']].to_dict('index')

    # 3. 获取今日 09:25 实时快照
    print(f"正在捕获实时竞价快照 (标的数: {len(all_stocks)})...")
    ticks = xtdata.get_full_tick(all_stocks)
    
    final_records = []

    for symbol in all_stocks:
        tick = ticks.get(symbol)
        if not tick or tick.get('lastPrice', 0) == 0:
            continue
        
        # A. 提取今日竞价数据
        # 09:25:00 这一秒，amount 就是竞价成交额 (元)
        # 转为万元以便存储和观察
        curr_auc_amt = tick.get('amount', 0) / 10000 
        curr_price = tick.get('lastPrice', 0)
        last_close = tick.get('lastClose', 0)
        open_pct = (curr_price / last_close - 1) * 100 if last_close > 0 else 0
        
        # B. 获取历史均值 (仅从本表计算)
        hist_info = hist_map.get(symbol, {'hist_avg_amt': 0, 'record_count': 0})
        v5_avg_amt = hist_info['hist_avg_amt']
        count = hist_info['record_count']

        # C. 计算量比：只有当历史记录够 5 天时才计算，否则设为 None
        avg_ratio = None
        if count >= 5 and v5_avg_amt > 0:
            avg_ratio = round(curr_auc_amt / v5_avg_amt, 2)
            
        # D. 准备入库记录
        detail = xtdata.get_instrument_detail(symbol)
        final_records.append({
            'symbol': symbol,
            'trade_date': today,
            'name': detail.get('InstrumentName', '未知'),
            'auction_amount': round(curr_auc_amt, 2),
            'open_pct': round(open_pct, 2),
            'avg_ratio': avg_ratio # 可能为 None (Null)
        })

    # 4. 批量 UPSERT 入库
    if final_records:
        df_final = pd.DataFrame(final_records)
        print(f"整理完成，准备更新 {len(df_final)} 条记录...")
        
        try:
            with engine.begin() as conn:
                # 写入临时表
                df_final.to_sql('temp_auction_sync', con=conn, if_exists='replace', index=False)
                
                # 执行覆盖更新
                upsert_sql = text("""
                    INSERT INTO stk_auction_signal (symbol, trade_date, name, auction_amount, open_pct, avg_ratio)
                    SELECT symbol, trade_date, name, auction_amount, open_pct, avg_ratio FROM temp_auction_sync
                    ON DUPLICATE KEY UPDATE 
                        auction_amount = VALUES(auction_amount),
                        open_pct = VALUES(open_pct),
                        avg_ratio = VALUES(avg_ratio);
                """)
                conn.execute(upsert_sql)
                conn.execute(text("DROP TABLE IF EXISTS temp_auction_sync;"))
            
            # 展示今日量比最高的几只 (仅显示已经计算出 ratio 的)
            print("\n" + "🔥" * 10 + " 今日竞价量比 TOP 10 (基于5日历史表) " + "🔥" * 10)
            if 'avg_ratio' in df_final.columns:
                top_10 = df_final.dropna(subset=['avg_ratio']).sort_values('avg_ratio', ascending=False).head(10)
                if not top_10.empty:
                    print(top_10[['symbol', 'name', 'avg_ratio', 'open_pct']].to_string(index=False))
                else:
                    print("提示：数据积累中，尚未有股票满足 5 日均值计算条件。")
            
            print("-" * 80)
            print("✅ 竞价数据已同步至数据库。")

        except Exception as e:
            print(f"❌ 数据库写入失败: {e}")

if __name__ == "__main__":
    sync_auction_by_table_history()