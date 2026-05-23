import os
import time
import datetime
import pandas as pd
import akshare as ak

from sqlalchemy import create_engine, text
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import requests

# =========================
# 1. 禁用代理
# =========================
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['all_proxy'] = ''
os.environ['no_proxy'] = '*'

# =========================
# 2. 数据库配置
# =========================
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'

engine = create_engine(
    DB_URL,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=False
)

# =========================
# 3. 初始化数据库
# =========================
def init_db():
    sql = """
    CREATE TABLE IF NOT EXISTS stk_sector_fund_flow (
        sector_name VARCHAR(100),
        trade_date DATE,
        net_inflow_amount DECIMAL(18,2),
        net_inflow_rate DECIMAL(10,2),
        top_stock_name VARCHAR(100),
        PRIMARY KEY (sector_name, trade_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """

    with engine.begin() as conn:
        conn.execute(text(sql))

# =========================
# 4. 创建稳定 Session
# =========================
def create_session():

    session = requests.Session()

    retry = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504]
    )

    adapter = HTTPAdapter(max_retries=retry)

    session.mount('http://', adapter)
    session.mount('https://', adapter)

    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/122.0 Safari/537.36"
        )
    })

    return session

# =========================
# 5. 获取行业资金流
# =========================
def fetch_sector_moneyflow():

    for i in range(3):

        try:

            print(f"正在获取行业资金流... 第 {i+1} 次尝试")

            df = ak.stock_sector_fund_flow_rank(indicator="今日")

            if df is not None and not df.empty:
                return df

        except Exception as e:

            print(f"获取失败: {e}")

            time.sleep(3)

    return pd.DataFrame()

# =========================
# 6. 主逻辑
# =========================
def analyze_and_save_money_flow():

    print(f"\n[{datetime.datetime.now()}] 开始同步资金流")

    init_db()

    target_date = datetime.date.today()

    try:

        # -------------------------
        # 获取数据
        # -------------------------
        df_raw = fetch_sector_moneyflow()

        if df_raw.empty:
            print("资金流为空，可能非交易时间或东方财富限制")
            return

        print("\nAKShare 返回字段：")
        print(df_raw.columns.tolist())

        # -------------------------
        # 防止字段变更
        # -------------------------
        required_columns = [
            '名称',
            '今日主力净流入-净额',
            '今日主力净流入-净占比',
            '今日主力净流入最大股'
        ]

        for col in required_columns:
            if col not in df_raw.columns:
                print(f"缺少字段: {col}")
                return

        # -------------------------
        # 数据清洗
        # -------------------------
        df_res = pd.DataFrame()

        df_res['sector_name'] = df_raw['名称']

        df_res['net_inflow_amount'] = (
            pd.to_numeric(
                df_raw['今日主力净流入-净额'],
                errors='coerce'
            ) / 1e8
        ).round(2)

        df_res['net_inflow_rate'] = pd.to_numeric(
            df_raw['今日主力净流入-净占比'],
            errors='coerce'
        ).round(2)

        df_res['top_stock_name'] = df_raw['今日主力净流入最大股']

        df_res['trade_date'] = target_date

        # 去空值
        df_res = df_res.dropna()

        print(f"\n成功获取 {len(df_res)} 条行业资金流数据")

        # -------------------------
        # 删除旧数据
        # -------------------------
        with engine.begin() as conn:

            delete_sql = text("""
                DELETE FROM stk_sector_fund_flow
                WHERE trade_date = :t_date
            """)

            conn.execute(delete_sql, {
                "t_date": target_date
            })

        # -------------------------
        # 插入新数据
        # 注意：
        # to_sql 不要放在事务 conn 中
        # -------------------------
        df_res.to_sql(
            'stk_sector_fund_flow',
            con=engine,
            if_exists='append',
            index=False,
            chunksize=500,
            method='multi'
        )

        # -------------------------
        # 输出TOP10
        # -------------------------
        inflow_top = (
            df_res[df_res['net_inflow_amount'] > 0]
            .sort_values(
                'net_inflow_amount',
                ascending=False
            )
            .head(10)
        )

        print("\n" + "="*80)
        print(f"{target_date} 行业资金净流入 TOP10")
        print("="*80)

        print(
            inflow_top[
                [
                    'sector_name',
                    'net_inflow_amount',
                    'net_inflow_rate',
                    'top_stock_name'
                ]
            ].to_string(index=False)
        )

        print("\n✅ 资金流同步完成")

    except Exception as e:

        print("\n❌ 程序运行失败")
        print(type(e))
        print(e)

# =========================
# main
# =========================
if __name__ == "__main__":

    analyze_and_save_money_flow()