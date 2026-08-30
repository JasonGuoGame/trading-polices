# -*- coding: utf-8 -*-
import datetime
import pandas as pd
from sqlalchemy import create_engine, text

# --- Configuration ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
PERIOD = '1d'
# ---------------------

def init(ContextInfo):
    print(f"[{datetime.datetime.now()}] Starting QMT internal data synchronization task...")
    
    # Create SQLAlchemy Engine
    engine = create_engine(DB_URL)
    ContextInfo.engine = engine

    # 1. Fetch full list of A-share stocks
    all_stocks = ContextInfo.get_stock_list_in_sector('沪深A股')
    if not all_stocks:
        all_stocks = ContextInfo.get_stock_list_in_sector('ASHR')
    
    ContextInfo.stocks = all_stocks
    print(f"Fetched {len(all_stocks)} stocks.")

    # 2. Read latest update date from MySQL
    try:
        with engine.connect() as conn:
            res = conn.execute(text("SELECT MAX(trade_date) FROM stk_daily_kline")).fetchone()
            last_date_raw = res[0]
    except Exception as e:
        print(f"Database query failed (ignore if this is initial table creation): {e}")
        last_date_raw = None

    if last_date_raw is None:
        print("Database is empty, setting start date to 1 year ago...")
        start_dt = datetime.datetime.now() - datetime.timedelta(days=365)
    else:
        # Reset to the last date in the database to overwrite and update incomplete daily prices
        start_dt = pd.to_datetime(last_date_raw)

    start_time = start_dt.strftime('%Y%m%d')
    today_str = datetime.datetime.now().strftime('%Y%m%d')
    
    ContextInfo.start_time = start_time
    ContextInfo.today_str = today_str

    print(f"Preparing to download and sync data from {start_time} to {today_str}...")

    # 3. Request historical data download in bulk via QMT native API
    for symbol in all_stocks:
        ContextInfo.download_history_data(
            stock_code=symbol,
            period=PERIOD,
            start_time=start_time,
            end_time=today_str
        )
        
    print("Historical data download requests completed! Fetching local cache and writing to MySQL...")

    # 4. Read local data and execute UPSERT (temporary table merge)
    sync_to_mysql(ContextInfo)

def sync_to_mysql(ContextInfo):
    engine = ContextInfo.engine
    all_stocks = ContextInfo.stocks
    start_time = ContextInfo.start_time
    today_str = ContextInfo.today_str
    
    batch_size = 200
    success_count = 0

    for i in range(0, len(all_stocks), batch_size):
        chunk = all_stocks[i : i + batch_size]
        
        # Fetch local K-line data using QMT native API
        res_data = ContextInfo.get_market_data_ex(
            fields=['open', 'high', 'low', 'close', 'volume', 'amount'],
            stock_code=chunk,
            period=PERIOD,
            start_time=start_time,
            end_time=today_str
        )
        
        batch_dfs = []
        for stock in chunk:
            if stock in res_data and not res_data[stock].empty:
                df = res_data[stock].copy()
                df['symbol'] = stock
                # Format K-line index into date
                df['trade_date'] = pd.to_datetime(df.index.astype(str)).date
                df = df.reset_index(drop=True)
                
                # Fill turnover rate (default to 0.0 if not available)
                df['turnover_rate'] = 0.0
                
                df = df[['symbol', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'turnover_rate']]
                batch_dfs.append(df)

        # Execute temporary table UPSERT
        if batch_dfs:
            final_df = pd.concat(batch_dfs)
            try:
                with engine.begin() as conn:
                    # A. Write to temporary table
                    final_df.to_sql('temp_stk_daily', con=conn, if_exists='replace', index=False)
                    
                    # B. Execute INSERT ... ON DUPLICATE KEY UPDATE 
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
                    # C. Drop temporary table
                    conn.execute(text("DROP TABLE IF EXISTS temp_stk_daily;"))
                    
                success_count += len(final_df)
            except Exception as e:
                print(f"Error occurred while processing batch: {e}")

    print(f"[{datetime.datetime.now()}] Synchronization complete! Updated {success_count} records.")

def handlebar(ContextInfo):
    # Main strategy logic; leave empty if used solely for post-market data sync
    pass