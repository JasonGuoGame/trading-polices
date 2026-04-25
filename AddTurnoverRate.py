import pandas as pd
from xtquant import xtdata
from sqlalchemy import create_engine, text

# --- 配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def backfill_turnover_rate():
    print("正在连接 MiniQMT 获取流通股数据...")
    xtdata.enable_hello = False
    
    # 1. 从数据库获取所有不重复的股票代码
    with engine.connect() as conn:
        query = text("SELECT DISTINCT symbol FROM stk_daily_kline WHERE turnover_rate IS NULL OR turnover_rate = 0")
        symbols = [row[0] for row in conn.execute(query).fetchall()]
    
    if not symbols:
        print("没有发现需要补全换手率的数据。")
        return

    print(f"共有 {len(symbols)} 只股票需要更新换手率...")

    # 2. 逐个股票处理（为了准确，我们获取每只股的流通股本）
    count = 0
    for symbol in symbols:
        # 获取股票详细信息
        tick = xtdata.get_full_tick([symbol])[symbol]
        detail = xtdata.get_instrument_detail(symbol)
        # TODO totalVolumn and volumn 都是0.0 可能需要VIP 用户才能得到
        print(f"{symbol}的换手率是什么{detail}")
        print(f"{symbol}的tick是什么{tick}")
        if not detail or 'FloatVolume' not in detail:
            continue
            
        # 流通股本 (注意：QMT返回的通常是“股”)
        float_shares = detail['FloatVolume']
        
        if float_shares <= 0:
            continue

        # 3. 执行 SQL 更新语句：直接在数据库层面计算并填充
        # 公式：(volume / float_shares) * 100
        update_sql = text(f"""
            UPDATE stk_daily_kline 
            SET turnover_rate = (volume / :fs) * 100 
            WHERE symbol = :sym AND (turnover_rate IS NULL OR turnover_rate = 0)
        """)
        
        with engine.begin() as conn:
            conn.execute(update_sql, {"fs": float_shares, "sym": symbol})
        
        count += 1
        if count % 100 == 0:
            print(f"已完成: {count} / {len(symbols)}")

    print(f"\n--- 补全完成！共更新了 {count} 只股票的历史换手率 ---")

if __name__ == "__main__":
    backfill_turnover_rate()