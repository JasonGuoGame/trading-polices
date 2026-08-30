import os
# 禁用代理（确保直连国内服务器）
os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""
os.environ["http_proxy"] = ""
os.environ["https_proxy"] = ""

import datetime
import time
import pandas as pd
import efinance as ef
from sqlalchemy import create_engine, text

DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def sync_daily_data_via_efinance():
    print(f"[{datetime.datetime.now()}] 启动 eFinance 日线数据同步...")

    # 1. 获取起始日期
    with engine.connect() as conn:
        res = conn.execute(text("SELECT MAX(trade_date) FROM stk_daily_kline")).fetchone()
        last_date_raw = res[0]

    start_date = "20230101" if last_date_raw is None else str(last_date_raw).replace("-", "")
    today_str = datetime.datetime.now().strftime('%Y%m%d')

    # 2. 获取股票列表（优先取本地 DB 代码保底，若数据库为空则抓取全量列表）
    try:
        with engine.connect() as conn:
            res = conn.execute(text("SELECT DISTINCT symbol FROM stk_daily_kline")).fetchall()
            if res:
                all_stocks = [str(s[0]) for s in res]
                print(f"从本地数据库提取到 {len(all_stocks)} 只股票代码。")
            else:
                spot_df = ef.stock.get_realtime_quotes()
                all_stocks = spot_df['股票代码'].tolist()
                print(f"全量抓取到 {len(all_stocks)} 只 A 股代码。")
    except Exception as e:
        print(f"获取股票列表失败: {e}")
        return

    print(f"准备同步区间: {start_date} -> {today_str}")

    batch_size = 50
    success_count = 0

    # 3. 分批拉取与入库
    for i in range(0, len(all_stocks), batch_size):
        chunk = all_stocks[i : i + batch_size]
        
        # 批量获取行情数据 (efinance 支持传入代码列表)
        try:
            kline_dict = ef.stock.get_quote_history(
                stock_codes=chunk,
                beg=start_date,
                end=today_str,
                klt=101,  # 101 表示日线
                fqt=1     # 1 表示前复权
            )
        except Exception as e:
            print(f"抓取批次数据失败: {e}")
            continue

        batch_dfs = []
        if isinstance(kline_dict, dict):
            for stock_code, df in kline_dict.items():
                if isinstance(df, pd.DataFrame) and not df.empty:
                    df.rename(columns={
                        '股票代码': 'symbol',
                        '日期': 'trade_date',
                        '开盘': 'open',
                        '最高': 'high',
                        '最低': 'low',
                        '收盘': 'close',
                        '成交量': 'volume',
                        '成交额': 'amount',
                        '换手率': 'turnover_rate'
                    }, inplace=True)
                    
                    df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
                    df = df[['symbol', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'turnover_rate']]
                    batch_dfs.append(df)

        # 4. 执行 UPSERT 覆盖式写入
        if batch_dfs:
            final_df = pd.concat(batch_dfs, ignore_index=True)
            try:
                with engine.begin() as conn:
                    final_df.to_sql('temp_stk_daily', con=conn, if_exists='replace', index=False)
                    upsert_sql = text("""
                        INSERT INTO stk_daily_kline (symbol, trade_date, open, high, low, close, volume, amount, turnover_rate)
                        SELECT symbol, trade_date, open, high, low, close, volume, amount, turnover_rate 
                        FROM temp_stk_daily
                        ON DUPLICATE KEY UPDATE 
                            open=VALUES(open), high=VALUES(high), low=VALUES(low), close=VALUES(close),
                            volume=VALUES(volume), amount=VALUES(amount), turnover_rate=VALUES(turnover_rate);
                    """)
                    conn.execute(upsert_sql)
                    conn.execute(text("DROP TABLE IF EXISTS temp_stk_daily;"))
                success_count += len(final_df)
            except Exception as e:
                print(f"写入数据库异常: {e}")

        print(f"处理进度: [{min(i + batch_size, len(all_stocks))}/{len(all_stocks)}]")
        time.sleep(0.2)

    print(f"[{datetime.datetime.now()}] 同步完成！共处理/更新 {success_count} 条记录。")

if __name__ == "__main__":
    sync_daily_data_via_efinance()