import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime
import warnings
import sys

# 忽略警告
warnings.filterwarnings('ignore')

# --- 引入全局配置 ---
# 这里使用简化的路径添加方式
sys.path.append(r"C:\ws\trading-polices\config")
import config  # 导入你的全局配置文件

# --- 1. 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

# --- 2. 数据库表初始化 ---
def init_db():
    # 建议将 sector_name 设置为 VARCHAR(200)，防止两个板块名拼接后溢出
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS stk_capital_abnormal (
        symbol VARCHAR(20),
        name VARCHAR(100),
        sector_name VARCHAR(200) COMMENT '所属热门板块',
        trade_date DATE,
        vol_ratio DECIMAL(10, 2) COMMENT '日线爆量倍数',
        surge_count INT COMMENT '分时脉冲次数',
        max_surge_ret DECIMAL(10, 2) COMMENT '单分最大涨幅%',
        surge_times TEXT COMMENT '异动具体时间点',
        last_update DATETIME COMMENT '记录最后更新时间',
        PRIMARY KEY (symbol, trade_date)
    ) ENGINE=InnoDB;
    """
    with engine.begin() as conn:
        conn.execute(text(create_table_sql))
    print("✅ 数据库表结构校验完成")

# --- 3. 分时脉冲分析 ---
def analyze_intraday_surge(symbol, date_str):
    query = text("""
        SELECT trade_time, close, volume 
        FROM stk_min_kline 
        WHERE symbol=:s AND DATE(trade_time)=:d 
        ORDER BY trade_time ASC
    """)
    try:
        df_min = pd.read_sql(query, engine, params={"s": symbol, "d": date_str})
        if len(df_min) < 50: return None

        df_min["ret"] = df_min["close"].pct_change()
        df_min["vol_ma10"] = df_min["volume"].rolling(10).mean()
        df_min["vol_ratio"] = df_min["volume"] / (df_min["vol_ma10"].shift(1) + 1)
        
        surges = df_min[(df_min["ret"] > 0.008) & (df_min["vol_ratio"] > 5.0)]
        if surges.empty: return None

        return {
            "surge_count": len(surges),
            "max_surge_ret": round(surges["ret"].max() * 100, 2),
            "surge_times": ",".join(surges["trade_time"].dt.strftime("%H:%M").tolist())
        }
    except:
        return None

# --- 4. 覆盖保存结果到 MySQL ---
def save_to_mysql_replace(results_list, trade_date):
    if not results_list: return
    df = pd.DataFrame(results_list)
    df["trade_date"] = trade_date
    df["last_update"] = datetime.datetime.now()
    
    df_to_save = df.rename(columns={
        "代码": "symbol", "名称": "name", "所属板块": "sector_name",
        "爆量倍数": "vol_ratio", "分时脉冲次数": "surge_count",
        "单分最大涨幅%": "max_surge_ret", "异动时间点": "surge_times"
    })

    db_cols = ["symbol", "name", "sector_name", "trade_date", "vol_ratio", "surge_count", "max_surge_ret", "surge_times", "last_update"]
    df_to_save = df_to_save[db_cols]

    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM stk_capital_abnormal WHERE trade_date = :d"), {"d": trade_date})
            df_to_save.to_sql("stk_capital_abnormal", con=conn, if_exists="append", index=False, chunksize=1000)
        print(f"✅ 今日 {trade_date} 异动名单已全量更新 (共 {len(df_to_save)} 条记录)")
    except Exception as e:
        print(f"❌ 数据库写入失败: {e}")

# --- 5. 主程序 ---
def run_capital_monitor():
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 启动资金异动扫描 (双板块显示版)...")

    latest_date_res = pd.read_sql("SELECT MAX(trade_date) AS trade_date FROM stk_factors", engine)
    latest_date = latest_date_res.iloc[0]["trade_date"]
    if latest_date is None: return

    blacklist_sql = " AND ".join([f"r.sector_name NOT LIKE '%%{item}%%'" for item in config.SECTOR_BLACKLIST])

    # 核心 SQL
    initial_query = text(f"""
        SELECT
            f.symbol, f.f_vol_ratio, s.name,
            GROUP_CONCAT(DISTINCT r.sector_name SEPARATOR ',') as sectors
        FROM stk_factors f
        JOIN stocks s ON f.symbol = s.symbol
        JOIN stock_sector_relation r ON f.symbol = r.symbol
        WHERE f.trade_date = :trade_date
          AND f.f_vol_ratio > 2.2
          AND s.name NOT LIKE '%%ST%%'
          AND (r.sector_name LIKE '行业-%%' OR r.sector_name LIKE '概念-%%')
          AND ({blacklist_sql})
        GROUP BY f.symbol;
    """)

    with engine.connect() as conn:
        candidates = pd.read_sql(initial_query, conn, params={"trade_date": latest_date})
    
    print(f"初筛候选个股: {len(candidates)} 只，开始深度穿透...")

    results = []
    for _, row in candidates.iterrows():
        surge_info = analyze_intraday_surge(row["symbol"], latest_date)
        if surge_info:
            # --- 核心修改：提取并清洗前两个板块 ---
            raw_list = row['sectors'].split(',')
            
            # 清洗函数：去掉前缀
            def clean(s): return s.replace('行业-','').replace('概念-','')
            
            # 提取前两个
            cleaned_list = [clean(s) for s in raw_list]
            
            if len(cleaned_list) >= 2:
                # 拼接两个板块，中间用 / 隔开
                combined_sector = f"{cleaned_list[0]} / {cleaned_list[1]}"
            else:
                combined_sector = cleaned_list[0]

            results.append({
                "代码": row["symbol"],
                "名称": row["name"],
                "所属板块": combined_sector, # 这里现在存储两个板块
                "爆量倍数": round(row["f_vol_ratio"], 2),
                "分时脉冲次数": surge_info["surge_count"],
                "单分最大涨幅%": surge_info["max_surge_ret"],
                "异动时间点": surge_info["surge_times"]
            })

    if results:
        df_res = pd.DataFrame(results).sort_values("分时脉冲次数", ascending=False)
        print("\n" + "🚨" * 5 + f" 今日异动 (Top 15) " + "🚨" * 5)
        # 调整控制台显示宽度
        pd.set_option('display.max_colwidth', 50)
        print(df_res[['代码', '名称', '所属板块', '分时脉冲次数', '单分最大涨幅%']].head(15).to_string(index=False))
        
        save_to_mysql_replace(results, latest_date)
    else:
        print("\n今日暂未扫描到符合条件的显著异动。")

if __name__ == "__main__":
    init_db()
    run_capital_monitor()