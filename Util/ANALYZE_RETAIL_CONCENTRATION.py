import pandas as pd
from xtquant import xtdata
from sqlalchemy import create_engine
import datetime

# --- 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def analyze_retail_concentration(symbol):
    print(f"正在分析 {symbol} 的筹码分布特征...")

    # --- 维度 A: 股东人数变化 (从财务库取) ---
    # 获取股东人数数据
    res = xtdata.get_financial_data([symbol], table_list=['HOLDER_COUNT'])
    try:
        df_holders = res[symbol]['HOLDER_COUNT']
        # 比较本期与上期股东人数
        current_holders = df_holders['holder_count'].iloc[-1]
        prev_holders = df_holders['holder_count'].iloc[-2]
        holder_change = (current_holders - prev_holders) / prev_holders
    except:
        holder_change = 0 # 缺数据
        current_holders = 0

    # --- 维度 B: 筹码稳定性 (从日线库取最近20天) ---
    query = f"SELECT close, volume, turnover_rate FROM stk_daily_kline WHERE symbol='{symbol}' ORDER BY trade_date DESC LIMIT 20"
    df_daily = pd.read_sql(query, engine)
    
    # 1. 换手率稳定性：散户多的股，换手率忽大忽小，极不稳定
    turnover_std = df_daily['turnover_rate'].std()
    
    # 2. 价格波动率：散户多的股，日内振幅和日间波幅通常较大
    price_volatility = df_daily['close'].pct_change().std()

    # --- 维度 C: 盘口异动 (利用你之前的分时异动逻辑) ---
    # 如果该股近期频繁出现“分时脉冲”，通常是主力在“赶散户”或者“对倒洗盘”
    abnormal_query = f"SELECT COUNT(*) FROM stk_capital_abnormal WHERE symbol='{symbol}' AND trade_date > DATE_SUB(CURDATE(), INTERVAL 30 DAY)"
    abnormal_count = pd.read_sql(abnormal_query, engine).iloc[0,0]

    # --- 综合评分逻辑 (得分越高，散户越多) ---
    retail_score = 0
    
    # 股东人数增加，大减分（散户化严重）
    if holder_change > 0.05: retail_score += 40
    elif holder_change < -0.05: retail_score -= 20 # 筹码趋向集中
    
    # 价格波动极高
    if price_volatility > 0.03: retail_score += 30
    
    # 换手极其不稳定
    if turnover_std > 5: retail_score += 20

    # 结果打印
    print(f"------------------------------------")
    print(f"股东人数: {current_holders} (变动: {holder_change*100:+.2f}%)")
    print(f"价格波动率: {price_volatility:.4f}")
    print(f"近30日主力异动次数: {abnormal_count}")
    
    if retail_score >= 60:
        print("🚩 结论：散户扎堆，筹码极度分散，拉升阻力大。")
    elif retail_score <= 20:
        print("💎 结论：筹码高度集中，疑似主力高度控盘，容易出大行情。")
    else:
        print("⚖️  结论：筹码分布中性。")

if __name__ == "__main__":
    # 测试一只股票，比如“贵州茅台”或你筛选出来的“起爆股”
    analyze_retail_concentration('600519.SH')