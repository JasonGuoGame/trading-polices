import pandas as pd
from sqlalchemy import create_engine, text
import datetime
import warnings

warnings.filterwarnings('ignore')

# --- 数据库配置 ---
engine_quant = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')
engine_review = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')

def get_latest_trade_date():
    """获取最新交易日期"""
    with engine_quant.connect() as conn:
        res = conn.execute(text("SELECT MAX(trade_date) FROM stk_daily_kline")).scalar()
    return res

def calculate_and_save_new_highs():
    # 1. 获取目标日期
    today = get_latest_trade_date()
    if not today:
        print("❌ 数据库中无交易数据。")
        return
    
    print(f"🚀 开始计算新高个股明细 | 目标日期: {today}")

    # 2. 提取计算新高所需的历史数据 (至少250个交易日，取400天比较保险)
    start_date = (pd.to_datetime(today) - pd.Timedelta(days=400)).strftime('%Y-%m-%d')
    
    # 提取 K 线数据
    kline_sql = text("""
        SELECT symbol, trade_date, open, high, low, close 
        FROM stk_daily_kline 
        WHERE trade_date >= :sd AND trade_date <= :td
    """)
    print("   正在提取历史 K 线数据...")
    df_k = pd.read_sql(kline_sql, engine_quant, params={"sd": start_date, "td": today})
    
    # 提取股票基本信息和板块关系
    rel_sql = text("""
        SELECT r.symbol, s.name as stock_name, r.sector_name 
        FROM stock_sector_relation r
        JOIN stocks s ON r.symbol = s.symbol
    """)
    df_rel = pd.read_sql(rel_sql, engine_quant)

    # 3. 计算新高逻辑
    print("   正在计算个股新高状态...")
    df_k = df_k.sort_values(['symbol', 'trade_date'])
    
    # 计算过去 N 天的最高价 (注意：使用 shift(1) 排除掉当天，即看今天收盘价是否突破昨天的历史最高)
    df_k['max_20'] = df_k.groupby('symbol')['high'].transform(lambda x: x.shift(1).rolling(20).max())
    df_k['max_60'] = df_k.groupby('symbol')['high'].transform(lambda x: x.shift(1).rolling(60).max())
    df_k['max_250'] = df_k.groupby('symbol')['high'].transform(lambda x: x.shift(1).rolling(250).max())

    # 只保留当天的数据
    df_today = df_k[df_k['trade_date'] == today].copy()
    
    # 判定逻辑：收盘价 > 过去N天最高价
    df_today['high_20d'] = (df_today['close'] > df_today['max_20']).astype(int)
    df_today['high_60d'] = (df_today['close'] > df_today['max_60']).astype(int)
    df_today['high_250d'] = (df_today['close'] > df_today['max_250']).astype(int)

    # 过滤出至少满足其中一个新高条件的个股
    df_selected = df_today[
        (df_today['high_20d'] == 1) | 
        (df_today['high_60d'] == 1) | 
        (df_today['high_250d'] == 1)
    ].copy()

    if df_selected.empty:
        print("💡 今日无任何个股创下新高。")
        return

    # 4. 关联板块和名称
    # 一个股票可能属于多个板块，所以 merge 后行数会增加，这符合明细表设计
    df_final = df_selected.merge(df_rel, on='symbol', how='inner')

    # 5. 清理板块名称 (沿用你 v4 代码中的清洗逻辑，确保板块名干净)
    def clean_sector_name(name):
        return name.replace('行业-', '').replace('概念-', '').replace('Ⅱ', '').replace('Ⅲ', '').strip()

    df_final['sector_name'] = df_final['sector_name'].apply(clean_sector_name)

    # 6. 准备写入数据库的数据
    # 字段对应：trade_date, symbol, stock_name, sector_name, high_20d, high_60d, high_250d, close
    df_to_save = df_final[[
        'trade_date', 'symbol', 'stock_name', 'sector_name', 
        'high_20d', 'high_60d', 'high_250d', 'close'
    ]]

    # 7. 写入数据库 (使用 ON DUPLICATE KEY UPDATE 模式防止重复执行报错)
    print(f"   正在保存 {len(df_to_save)} 条新高明细记录到数据库...")
    
    save_count = 0
    with engine_review.begin() as conn:
        # 创建临时表快速写入
        df_to_save.to_sql('tmp_new_high', conn, if_exists='replace', index=False)
        
        # 使用 INSERT INTO ... SELECT 结合 ON DUPLICATE KEY UPDATE 保证数据唯一性
        upsert_sql = text("""
            INSERT INTO stk_new_high_detail 
            (trade_date, symbol, stock_name, sector_name, high_20d, high_60d, high_250d, close, created_at)
            SELECT trade_date, symbol, stock_name, sector_name, high_20d, high_60d, high_250d, close, NOW()
            FROM tmp_new_high
            ON DUPLICATE KEY UPDATE
                high_20d = VALUES(high_20d),
                high_60d = VALUES(high_60d),
                high_250d = VALUES(high_250d),
                close = VALUES(close);
        """)
        conn.execute(upsert_sql)
        conn.execute(text("DROP TABLE IF EXISTS tmp_new_high"))
        save_count = len(df_to_save)

    print(f"✅ 处理完成！已记录 {save_count} 条个股新高数据到 stk_new_high_detail。")

if __name__ == "__main__":
    calculate_and_save_new_highs()