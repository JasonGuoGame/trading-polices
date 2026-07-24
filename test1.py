import json
import pandas as pd
import pandas_ta as ta
from sqlalchemy import create_engine, text
import datetime
import warnings

warnings.filterwarnings('ignore')

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)
engine_review = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')

def get_latest_date():
    with engine.connect() as conn:
        res = conn.execute(text("SELECT MAX(DATE(trade_time)) FROM stk_min_kline")).scalar()
    return res

# ---------------------------------------------------------
# 1. 数据转换函数：1分钟转5分钟
# ---------------------------------------------------------
def convert_1m_to_5m(df_1m):
    """
    将1分钟DataFrame聚合为5分钟 (兼容 Pandas 2.x)
    """
    if df_1m.empty: return pd.DataFrame()
    
    # 设置时间索引
    df_1m = df_1m.set_index('trade_time')
    
    # 定义聚合规则
    ohlc_dict = {
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }
    
    # 聚合为5分钟
    df_5m = df_1m.resample('5Min', closed='right', label='right').agg(ohlc_dict)
    
    # --- 修复 append 报错部分 ---
    # 分别获取上午和下午的交易时段
    am_session = df_5m.between_time('09:31', '11:30')
    pm_session = df_5m.between_time('13:01', '15:00')
    
    # 使用 pd.concat 进行合并
    df_5m = pd.concat([am_session, pm_session])
    # ---------------------------
    
    return df_5m.dropna().reset_index()

# ---------------------------------------------------------
# 2. 核心金叉逻辑
# ---------------------------------------------------------
def find_first_buy_signal(symbol, target_date):
    # 1. 提取数据（取当天及前一天，保证EMA准确）
    # 往前多取一天的数据做预热
    start_dt = (target_date - datetime.timedelta(days=3)).strftime('%Y-%m-%d')
    query = f"""
    SELECT trade_time, open, high, low, close, volume 
    FROM stk_min_kline 
    WHERE symbol = '{symbol}' 
      AND trade_time >= '{start_dt} 14:00:00'
      AND trade_time <= '{target_date.strftime('%Y-%m-%d')} 15:05:00'
    ORDER BY trade_time ASC
    """
    df_1m = pd.read_sql(text(query), engine)
    
    if df_1m.empty: return None

    # 2. 转换为 5分钟线
    df_5m = convert_1m_to_5m(df_1m)
    if len(df_5m) < 25: return None

    # 3. 计算指标
    df_5m['ema5'] = ta.ema(df_5m['close'], length=5)
    df_5m['ema20'] = ta.ema(df_5m['close'], length=20)
    df_5m['vol_ma'] = df_5m['volume'].rolling(5).mean()

    # 4. 判断金叉
    df_5m['prev_ema5'] = df_5m['ema5'].shift(1)
    df_5m['prev_ema20'] = df_5m['ema20'].shift(1)
    df_5m['gold_cross'] = (df_5m['prev_ema5'] < df_5m['prev_ema20']) & (df_5m['ema5'] > df_5m['ema20'])
    
    # 5. 筛选当天信号
    # 只看 target_date 当天
    df_today = df_5m[df_5m['trade_time'].dt.date == target_date]
    
    # 过滤条件
    # - 9:45之后
    # - 阳线
    # - 放量 (比前5根5分钟线均量大20%)
    cond_time = df_today['trade_time'].dt.time >= datetime.time(9, 45)
    cond_red = df_today['close'] > df_today['open']
    cond_vol = df_today['volume'] > (df_today['vol_ma'].shift(1) * 1.2)
    
    signals = df_today[df_today['gold_cross'] & cond_time & cond_red & cond_vol]

    if not signals.empty:
        buy_point = signals.iloc[0]
        return {
            "symbol": symbol,
            "time": buy_point['trade_time'].strftime('%H:%M'),
            "price": round(buy_point['close'], 2),
            "vol_ratio": round(buy_point['volume'] / buy_point['vol_ma'], 2)
        }
    return None

# ---------------------------------------------------------
# 3. 执行扫描
# ---------------------------------------------------------
def run_intraday_scan():
    today_dt = get_latest_date()
    if not today_dt: return
    
    print(f"⏰ 开始扫描【1分钟转5分钟】第一买点，日期: {today_dt}")

    # 获取股票池
    pool_sql = f"SELECT symbol, stock_name, sector_name FROM stock_pools WHERE trade_date = '{today_dt}' AND status = '四维共振'"
    df_pool = pd.read_sql(text(pool_sql), engine_review)
    
    if df_pool.empty:
        print("💡 请先运行日线选股策略填充股票池。")
        return

    results = []
    for _, row in df_pool.iterrows():
        signal = find_first_buy_signal(row['symbol'], today_dt)
        if signal:
            signal['stock_name'] = row['stock_name']
            signal['sector'] = row['sector_name']
            results.append(signal)
            print(f"✅ 信号: {signal['time']} | {signal['stock_name']} ({signal['symbol']}) | 价格: {signal['price']}")

    if results:
        print("\n" + "="*80)
        print(pd.DataFrame(results)[['time', 'symbol', 'stock_name', 'price', 'vol_ratio', 'sector']])
        print("="*80)
    else:
        print("今日暂无金叉信号。")

if __name__ == "__main__":
    run_intraday_scan()