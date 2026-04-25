import pandas as pd
import pandas_ta as ta
from sqlalchemy import create_engine
import datetime

# --- 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

# 粘合度阈值 (1.5% 以内认为粘合)
COHESION_THRESHOLD = 0.015 

def screen_stocks():
    print(f"[{datetime.datetime.now()}] 启动【主板非ST】精选筛选...")
    
    # 1. 获取数据库中最新的日期
    try:
        latest_date_query = "SELECT MAX(trade_date) FROM stk_daily_kline"
        latest_date = pd.read_sql(latest_date_query, engine).iloc[0, 0]
        print(f"当前分析日期: {latest_date}")
    except:
        print("数据库无数据。")
        return

    # 2. 核心 SQL：关联 stocks 表，并执行初步过滤（主板 + 非ST/退）
    # 过滤条件：代码 60或00开头，名称不含 ST、*ST、退
    query = f"""
    SELECT k.*, s.name 
    FROM stk_daily_kline k
    JOIN stocks s ON k.symbol = s.symbol
    WHERE k.trade_date >= (
        SELECT MIN(t.trade_date) FROM (
            SELECT DISTINCT trade_date FROM stk_daily_kline 
            ORDER BY trade_date DESC LIMIT 60
        ) AS t
    )
    AND (k.symbol LIKE '60%%' OR k.symbol LIKE '00%%')
    AND s.name NOT LIKE '%%ST%%'
    AND s.name NOT LIKE '%%退%%'
    ORDER BY k.symbol, k.trade_date ASC
    """
    df_all = pd.read_sql(query, engine)
    
    if df_all.empty:
        print("未找到符合基础条件（主板且非ST）的股票。")
        return

    selected_stocks = []
    
    # 3. 按股票分组计算技术指标
    print(f"正在对 {len(df_all['symbol'].unique())} 只主板个股进行技术形态扫描...")
    
    for symbol, df in df_all.groupby('symbol'):
        # 确保数据按时间排序
        df = df.sort_values('trade_date')
        
        # --- A. 计算技术指标 ---
        # 均线
        df['MA5'] = ta.sma(df['close'], length=5)
        df['MA10'] = ta.sma(df['close'], length=10)
        df['MA20'] = ta.sma(df['close'], length=20)
        # 成交量均线 (过去10日)
        df['V_MA10'] = ta.sma(df['volume'], length=10)
        
        # --- B. 获取最新一行数据进行判断 ---
        current = df.iloc[-1]
        
        # 容错处理：如果均线没算出来，跳过
        if pd.isna(current['MA20']):
            continue

        # --- C. 策略条件判断 ---
        
        # 1. 成交量翻倍 (当前量 > 2倍过去10天平均量)
        cond_vol = current['volume'] > 2 * current['V_MA10']
        
        # 2. 均线粘合度 (MA5, MA10, MA20 的最大差距在 1.5% 以内)
        mas = [current['MA5'], current['MA10'], current['MA20']]
        max_ma = max(mas)
        min_ma = min(mas)
        cohesion = (max_ma - min_ma) / current['MA20']
        cond_cohesion = cohesion < COHESION_THRESHOLD
        
        # 3. 多头形态 (收盘价在所有均线之上)
        cond_price = current['close'] > max_ma
        
        # --- D. 综合筛选 ---
        if cond_vol and cond_cohesion and cond_price:
            selected_stocks.append({
                '代码': symbol,
                '名称': df['name'].iloc[0],
                '最新价': round(current['close'], 2),
                '成交量倍数': round(current['volume'] / current['V_MA10'], 2),
                '均线粘合': f"{round(cohesion * 100, 2)}%",
                '日期': current['trade_date']
            })

    # 4. 输出结果
    if selected_stocks:
        result_df = pd.DataFrame(selected_stocks)
        # 按照成交量倍数降序排列，优先看放量最猛的
        result_df = result_df.sort_values('成交量倍数', ascending=False)
        
        print("\n" + "🚀" * 15)
        print(f"筛选完成！今日共找到 {len(result_df)} 只【主板起爆】潜力股：")
        print("-" * 85)
        print(result_df[['代码', '名称', '最新价', '成交量倍数', '均线粘合', '日期']].to_string(index=False))
        print("-" * 85)
        print("💡 研判：名单内个股均已剔除ST、双创板块。重点关注放量明显且均线极度粘合的标的。")
    else:
        print("\n今日全市场未发现符合“均线粘合+放量突破”的主板个股。")

if __name__ == "__main__":
    screen_stocks()