import pandas as pd
from xtquant import xtdata
from sqlalchemy import create_engine, text
import datetime
import time

# --- 配置区 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)
PERIOD = '1d'
# --------------

def daily_increment_update():
    xtdata.enable_hello = False
    print(f"[{datetime.datetime.now()}] 启动数据同步任务（支持随时覆盖更新）...")

    # 1. 获取全沪深 A 股列表
    all_stocks = xtdata.get_stock_list_in_sector('沪深A股')
    if not all_stocks:
        print("错误：无法连接 MiniQMT 或获取股票列表失败。")
        return

    # 2. 确定同步起始日期
    with engine.connect() as conn:
        res = conn.execute(text("SELECT MAX(trade_date) FROM stk_daily_kline")).fetchone()
        last_date_raw = res[0]
    
    if last_date_raw is None:
        print("数据库为空，设定起始日期为一年前...")
        start_dt = datetime.datetime.now() - datetime.timedelta(days=365)
    else:
        # 【关键修改】：起始日期设为数据库最后一天的日期（不加1天）
        # 这样可以重新获取最后一天的最新数据，从而覆盖掉之前不完整的“收盘价”
        start_dt = pd.to_datetime(last_date_raw)
    
    start_time = start_dt.strftime('%Y%m%d')
    today_str = datetime.datetime.now().strftime('%Y%m%d')

    print(f"准备同步从 {start_time} 到 {today_str} 的数据...")

    # 3. 批量下发下载指令
    # batch_size = 200
    # for i in range(0, len(all_stocks), batch_size):
    #     chunk = all_stocks[i : i + batch_size]
    #     for stock in chunk:
    #         xtdata.download_history_data(stock, period=PERIOD, start_time=start_time, incrementally=True)
    #     print(f"下发下载指令进度: {min(i + batch_size, len(all_stocks))}/{len(all_stocks)}")

    # print("等待数据落盘 (5秒)...")
    # time.sleep(5)
    batch_size = 200
    for i in range(0, len(all_stocks), batch_size):
        chunk = all_stocks[i : i + batch_size]
        
        for stock in chunk:
            retry_count = 2  # 设置重试次数
            while retry_count > 0:
                try:
                    # 关键：incremental=True 时，QMT只同步缺的数据，速度很快
                    xtdata.download_history_data(stock, period=PERIOD, start_time=start_time, incrementally=True)
                    # --- 改进1：强制限速，给 MiniQMT 喘息时间 ---
                    time.sleep(0.01) 
                    break # 成功则退出重试
                except RuntimeError as e:
                    if "timeout" in str(e).lower():
                        retry_count -= 1
                        # --- 改进2：超时后等待并重试 ---
                        time.sleep(1) 
                        if retry_count == 0:
                            print(f"⚠️ 股票 {stock} 下载持续超时，已跳过。")
                    else:
                        print(f"❌ 股票 {stock} 发生非超时错误: {e}")
                        break
        
        print(f"已处理下载指令: {min(i + batch_size, len(all_stocks))}/{len(all_stocks)}")
        # 每处理一个大批次，多休息一会儿
        time.sleep(2)

    # --- 改进3：增加等待数据落盘的时间 ---
    print("下载指令下发完毕，等待数据最终落盘 (15秒)...")
    time.sleep(15)

    # 4. 读取数据并执行 UPSERT (覆盖更新)
    success_count = 0
    for i in range(0, len(all_stocks), batch_size):
        chunk = all_stocks[i : i + batch_size]
        
        res_data = xtdata.get_local_data(
            stock_list=chunk,
            period=PERIOD,
            start_time=start_time,
            end_time=today_str,
            count=-1,
            field_list=['open', 'high', 'low', 'close', 'volume', 'amount', 'turnoverRate']
        )
        
        batch_dfs = []
        for stock in chunk:
            if stock in res_data and not res_data[stock].empty:
                df = pd.DataFrame(res_data[stock])
                df['symbol'] = stock
                df['trade_date'] = pd.to_datetime(df.index, unit='ms').date
                df = df.reset_index(drop=True)
                
                # 换手率逻辑
                if 'turnoverRate' in df.columns:
                    df.rename(columns={'turnoverRate': 'turnover_rate'}, inplace=True)
                else:
                    detail = xtdata.get_instrument_detail(stock)
                    fs = detail.get('FloatVolume', 0)
                    df['turnover_rate'] = (df['volume'] / fs * 100) if fs > 0 else 0

                df = df[['symbol', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'turnover_rate']]
                batch_dfs.append(df)
        
        # --- 核心修改：使用临时表执行 UPSERT ---
        if batch_dfs:
            final_df = pd.concat(batch_dfs)
            try:
                with engine.begin() as conn:
                    # A. 写入临时表
                    final_df.to_sql('temp_stk_daily', con=conn, if_exists='replace', index=False)
                    
                    # B. 执行 INSERT ... ON DUPLICATE KEY UPDATE 
                    # 这要求你的正式表必须有主键 (symbol, trade_date)
                    upsert_sql = text("""
                        INSERT INTO stk_daily_kline (symbol, trade_date, open, high, low, close, volume, amount, turnover_rate)
                        SELECT * FROM temp_stk_daily
                        ON DUPLICATE KEY UPDATE 
                            open = VALUES(open),
                            high = VALUES(high),
                            low = VALUES(low),
                            close = VALUES(close),
                            volume = VALUES(volume),
                            amount = VALUES(amount),
                            turnover_rate = VALUES(turnover_rate);
                    """)
                    conn.execute(upsert_sql)
                    # C. 删除临时表
                    conn.execute(text("DROP TABLE IF EXISTS temp_stk_daily;"))
                    
                success_count += len(final_df)
            except Exception as e:
                print(f"写入批次发生错误: {e}")
        
        if i % 1000 == 0:
            print(f"处理进度: {min(i + batch_size, len(all_stocks))}/{len(all_stocks)}")

    print(f"[{datetime.datetime.now()}] 同步完成！已处理并更新 {success_count} 条记录。")

if __name__ == "__main__":
    # 【前提提醒】请确保你的数据库表已经设置了主键，否则 UPSERT 不生效
    # ALTER TABLE stk_daily_kline ADD PRIMARY KEY (symbol, trade_date);
    daily_increment_update()