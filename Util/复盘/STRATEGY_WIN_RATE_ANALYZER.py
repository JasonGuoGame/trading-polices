import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime
import warnings

warnings.filterwarnings('ignore')

# --- 1. 数据库配置 ---
engine_quant = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')
engine_review = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')

def get_strategy_performance():
    print(f"[{datetime.datetime.now()}] 启动多策略次日胜率统计系统...")

    # 1. 提取 stock_pools 中的所有选股信号
    with engine_review.connect() as conn:
        df_signals = pd.read_sql("SELECT symbol, trade_date, pool_type, status FROM stock_pools", conn)
    
    if df_signals.empty:
        print("❌ 错误：stock_pools 表中无数据。")
        return

    # 2. 策略归类逻辑封装 (核心修改点)
    def categorize_strategy(row):
        pt, st = row['pool_type'], str(row['status']) # 确保 status 是字符串
        
        # --- 新增：识别赢家模式信号 ---
        if st.startswith("赢家模式:"):
            return '6. 模式赢家跟随'
            
        # --- 原有逻辑 ---
        if pt == 'short' and st == '短线爆发黑马': return '1. 短线黑马股'
        if pt == 'long' and st == '长线牛': return '2. 价值长线股'
        if pt == 'short' and st == '资金共振金叉': return '3. 0轴金叉资金共振'
        if pt == 'long' and st == '趋势确立': return '4. MACD+BOLL趋势'
        if pt == 'short' and st in ['主升接力', '启动突破']: return '5. 换手率+量比动能'
        
        return '其他'

    df_signals['strategy_group'] = df_signals.apply(categorize_strategy, axis=1)
    df_signals = df_signals[df_signals['strategy_group'] != '其他']

    # 3. 准备价格数据 (获取日期序列)
    print("正在匹配历史行情并执行 T+1 结算...")
    query_dates = "SELECT DISTINCT trade_date FROM stk_daily_kline ORDER BY trade_date ASC"
    all_dates = pd.read_sql(query_dates, engine_quant)['trade_date'].tolist()
    # 建立日期到下一日的映射
    date_to_next = {all_dates[i]: all_dates[i+1] for i in range(len(all_dates)-1)}

    # 4. 批量获取涉及到的价格信息
    symbols = tuple(df_signals['symbol'].unique())
    # 只需要查询信号日期及它们的 T+1 日
    unique_dates = df_signals['trade_date'].unique()
    target_dates = []
    for d in unique_dates:
        target_dates.append(d)
        if d in date_to_next:
            target_dates.append(date_to_next[d])
    
    target_dates = tuple(set(target_dates))

    query_prices = text("SELECT symbol, trade_date, close FROM stk_daily_kline WHERE symbol IN :s AND trade_date IN :d")
    with engine_quant.connect() as conn:
        df_prices = pd.read_sql(query_prices, conn, params={"s": symbols, "d": target_dates})
    
    # 字典化加速：(symbol, date) -> close
    price_map = df_prices.set_index(['symbol', 'trade_date'])['close'].to_dict()

    # 5. 计算每笔信号的收益
    results = []
    for _, sig in df_signals.iterrows():
        sym, t_date = sig['symbol'], sig['trade_date']
        t_plus_1 = date_to_next.get(t_date)
        
        if t_plus_1:
            p_t = price_map.get((sym, t_date))
            p_next = price_map.get((sym, t_plus_1))
            
            if p_t and p_next:
                # 收益率 = (次日收盘 - 今日收盘) / 今日收盘
                # 如果你想算‘隔夜收益’，可以把 p_next 改为次日 open
                ret = (p_next - p_t) / p_t * 100
                results.append({
                    'strategy': sig['strategy_group'],
                    'return': ret
                })

    # 6. 汇总分析
    if not results:
        print("未发现符合回测时间窗的数据。")
        return

    df_res = pd.DataFrame(results)
    
    # 执行 GroupBy 聚合统计
    summary = df_res.groupby('strategy')['return'].agg([
        ('信号总数', 'count'),
        ('平均收益%', lambda x: round(x.mean(), 2)),
        ('胜率%', lambda x: round((x > 0).mean() * 100, 2)),
        ('单笔最猛%', lambda x: round(x.max(), 2)),
        ('单笔最惨%', lambda x: round(x.min(), 2))
    ]).reset_index()

    # 7. 美化输出
    print("\n" + "📊" * 10 + " 策略赚钱效应【次日结算】总榜 " + "📊" * 10)
    print("-" * 110)
    # 按照胜率排序，让你一眼看到谁最稳
    summary = summary.sort_values('胜率%', ascending=False)
    print(summary.to_string(index=False))
    print("-" * 110)

    # 8. 核心研判
    if not summary.empty:
        top = summary.iloc[0]
        print(f"💡 逻辑演绎：当前市场‘最强模式’是【{top['strategy']}】")
        print(f"   - 历史次日胜率：{top['胜率%']}%")
        print(f"   - 平均获利空间：{top['平均收益%']}%")
        
        # 针对赢家模式的特殊反馈
        if top['strategy'] == '6. 模式赢家跟随':
            print("🚀 强力反馈：当前市场具有极强的‘路径依赖’，昨日强者今日继续走强，建议积极跟随赢家模式选股。")
        
        if top['平均收益%'] < 0:
            print("⚠️ 风险提示：当前表现最好的策略平均收益也为负，全市场处于‘大面’期，建议空仓避险。")

if __name__ == "__main__":
    get_strategy_performance()