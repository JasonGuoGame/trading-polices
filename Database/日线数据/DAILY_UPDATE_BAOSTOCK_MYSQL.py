import os
# 1. 强制禁用代理，防止 BaoStock 底层 Socket 被拦截
os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""
os.environ["http_proxy"] = ""
os.environ["https_proxy"] = ""

import datetime
import baostock as bs
import pandas as pd
from sqlalchemy import create_engine, text

DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def get_a_stock_list_robust():
    """具备三级降级机制的全量 A 股代码获取器"""
    a_stocks = []
    
    # 策略 1：动态尝试最近 5 天中的历史交易日
    for day_offset in range(3, 8):
        target_day = (datetime.datetime.now() - datetime.timedelta(days=day_offset)).strftime('%Y-%m-%d')
        rs_stocks = bs.query_all_stock(day=target_day)
        
        stock_list = []
        while (rs_stocks.error_code == '0') & rs_stocks.next():
            stock_list.append(rs_stocks.get_row_data())

        if stock_list:
            df_stocks = pd.DataFrame(stock_list, columns=rs_stocks.fields)
            a_stocks = df_stocks[df_stocks['code'].str.match(r'^(sh\.60|sz\.00|sz\.30|sh\.68)')]['code'].tolist()
            if a_stocks:
                print(f"通过 BaoStock 成功获取到 {target_day} 的 {len(a_stocks)} 只股票列表。")
                return a_stocks

    # 策略 2：若 BaoStock 列表接口失效，直接读取数据库已有代码（MiniQMT 历史数据）
    print("⚠️ BaoStock 在线列表获取失败，尝试从数据库读取已有股票代码...")
    try:
        with engine.connect() as conn:
            res = conn.execute(text("SELECT DISTINCT symbol FROM stk_daily_kline")).fetchall()
            if res:
                for (s,) in res:
                    prefix = 'sh.' if str(s).startswith(('60', '68')) else 'sz.'
                    a_stocks.append(f"{prefix}{s}")
                print(f"从本地数据库保底提取到 {len(a_stocks)} 只历史股票代码。")
                return a_stocks
    except Exception as e:
        print(f"读取数据库代码失败: {e}")

    return a_stocks

def sync_daily_data_via_baostock():
    # 1. 登录 BaoStock
    lg = bs.login()
    if lg.error_code != '0':
        print(f"BaoStock 登录失败: {lg.error_msg}")
        return

    print(f"[{datetime.datetime.now()}] 启动 BaoStock 数据同步...")

    # 2. 查询起始日期
    with engine.connect() as conn:
        res = conn.execute(text("SELECT MAX(trade_date) FROM stk_daily_kline")).fetchone()
        last_date_raw = res[0]

    start_date = "2026-08-01" if last_date_raw is None else str(last_date_raw)
    end_date = datetime.datetime.now().strftime('%Y-%m-%d')

    # 3. 获取股票列表
    a_stocks = get_a_stock_list_robust()

    if not a_stocks:
        print("❌ 无法获取任何股票代码，任务终止。")
        bs.logout()
        return

    print(f"准备同步区间: {start_date} -> {end_date}")

    # 4. 循环拉取数据并入库 (UPSERT 覆盖模式)
    batch_dfs = []
    for idx, code in enumerate(a_stocks, start=1):
        rs = bs.query_history_k_data_plus(
            code,
            "date,code,open,high,low,close,volume,amount,turn",
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag="2"  # 前复权
        )
        
        data_list = []
        while (rs.error_code == '0') & rs.next():
            data_list.append(rs.get_row_data())
            
        if data_list:
            df = pd.DataFrame(data_list, columns=rs.fields)
            df.rename(columns={
                'date': 'trade_date',
                'code': 'symbol',
                'turn': 'turnover_rate'
            }, inplace=True)
            
            df['symbol'] = df['symbol'].str.split('.').str[1]
            num_cols = ['open', 'high', 'low', 'close', 'volume', 'amount', 'turnover_rate']
            df[num_cols] = df[num_cols].apply(pd.to_numeric, errors='coerce').fillna(0)
            batch_dfs.append(df)

        # 每 200 只股票提交一次数据库
        if idx % 250 == 0 or idx == len(a_stocks):
            if batch_dfs:
                final_df = pd.concat(batch_dfs, ignore_index=True)
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
                batch_dfs = []
            print(f"已同步进度: [{idx}/{len(a_stocks)}]")

    bs.logout()
    print("数据同步完成！")

if __name__ == "__main__":
    sync_daily_data_via_baostock()