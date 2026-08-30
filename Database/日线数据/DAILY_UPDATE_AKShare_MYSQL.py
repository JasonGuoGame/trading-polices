import os
import time
import datetime
import pandas as pd
import requests
import akshare as ak
from sqlalchemy import create_engine, text

# --- 1. 禁用代理，防止 VPN 拦截 HTTP 请求 ---
os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""
os.environ["http_proxy"] = ""
os.environ["https_proxy"] = ""

# --- 2. 伪装请求头 Header ---
session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9",
})
ak.stock_zh_a_hist.session = session

# --- 配置区 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)
PERIOD = 'daily'  # AKShare 日线参数
ADJUST = 'qfq'    # 复权类型: qfq(前复权), hfq(后复权), ""(不复权)
# --------------

def daily_increment_update_akshare():
    print(f"[{datetime.datetime.now()}] 启动 AKShare 数据同步任务...")

    # 1. 从本地数据库获取股票列表
    all_stocks = []
    try:
        with engine.connect() as conn:
            res = conn.execute(text("SELECT DISTINCT symbol FROM stk_daily_kline")).fetchall()
            if res:
                all_stocks = [str(s[0]) for s in res]
                print(f"从本地数据库提取到 {len(all_stocks)} 只股票代码。")
    except Exception as e:
        print(f"查询数据库股票列表失败: {e}")
        return

    if not all_stocks:
        print("❌ 数据库为空，请先导入初始化数据。")
        return

    # 2. 确定同步起始日期
    with engine.connect() as conn:
        res = conn.execute(text("SELECT MAX(trade_date) FROM stk_daily_kline")).fetchone()
        last_date_raw = res[0]
    
    if last_date_raw is None:
        start_dt = datetime.datetime.now() - datetime.timedelta(days=365)
    else:
        start_dt = pd.to_datetime(last_date_raw)
    
    start_time = start_dt.strftime('%Y%m%d')
    today_str = datetime.datetime.now().strftime('%Y%m%d')

    print(f"准备同步区间: {start_time} -> {today_str}")

    batch_size = 100
    success_count = 0

    # 3. 分批遍历股票同步入库
    for i in range(0, len(all_stocks), batch_size):
        chunk = all_stocks[i : i + batch_size]
        batch_dfs = []

        for db_symbol in chunk:
            # 过滤排除指数（如 000001.SH 上证指数/399001.SZ 深证成指），避免请求失败
            if db_symbol in ['000001.SH', '399001.SZ', '399006.SZ', '000688.SH']:
                continue

            # 核心逻辑：裁切带后缀的代码，取出纯6位纯数字（如 '000001.SZ' -> '000001'）
            clean_code = db_symbol.split('.')[0]

            retry_count = 3
            while retry_count > 0:
                try:
                    df = ak.stock_zh_a_hist(
                        symbol=clean_code,  # 传入纯 6 位代码
                        period=PERIOD,
                        start_date=start_time,
                        end_date=today_str,
                        adjust=ADJUST
                    )

                    if not df.empty:
                        df.rename(columns={
                            '日期': 'trade_date',
                            '开盘': 'open',
                            '最高': 'high',
                            '最低': 'low',
                            '收盘': 'close',
                            '成交量': 'volume',
                            '成交额': 'amount',
                            '换手率': 'turnover_rate'
                        }, inplace=True)

                        # 还原为本地数据库规范的带后缀代码，保持唯一键一致
                        df['symbol'] = db_symbol  
                        df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
                        df = df[['symbol', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'turnover_rate']]
                        batch_dfs.append(df)

                    time.sleep(0.05)  # 轻微限速
                    break

                except Exception as e:
                    retry_count -= 1
                    time.sleep(1)
                    if retry_count == 0:
                        print(f"⚠️ 股票 {db_symbol} 下载失败/超时，已跳过。")

        # 4. 执行临时表 UPSERT 覆盖式写入
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
                print(f"写入数据库失败: {e}")

        print(f"处理进度: {min(i + batch_size, len(all_stocks))}/{len(all_stocks)}")
        time.sleep(0.3)

    print(f"[{datetime.datetime.now()}] 同步完成！共处理 {success_count} 条记录。")

if __name__ == "__main__":
    daily_increment_update_akshare()