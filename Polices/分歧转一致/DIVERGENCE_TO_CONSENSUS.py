import pandas as pd
import pandas_ta as ta
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def screen_hot_sector_reversal():
    print(f"[{datetime.datetime.now()}] 正在扫描【吸金王板块】中的‘分歧转一致’信号...")

    # 1. 核心 SQL：联表查询行情、所属板块及今日资金流向
    # 逻辑：
    # - 锁定今日主力净流入 > 1.5 亿的板块
    # - 提取这些板块内主板个股最近 40 天的数据
    query = """
    SELECT k.*, s.name, flow.sector_name as hot_sector, flow.net_inflow_amount
    FROM stk_daily_kline k
    JOIN stocks s ON k.symbol = s.symbol
    JOIN stock_sector_relation r ON k.symbol = r.symbol
    JOIN stk_sector_fund_flow flow ON (
        r.sector_name = CONCAT('行业-', flow.sector_name) 
        OR r.sector_name = CONCAT('概念-', flow.sector_name)
    )
    WHERE k.trade_date >= DATE_SUB(CURDATE(), INTERVAL 40 DAY)
      AND flow.trade_date = (SELECT MAX(trade_date) FROM stk_sector_fund_flow)
      AND flow.net_inflow_amount > 1.5                    -- 门槛：板块流入大于 1.5 亿
      AND (k.symbol LIKE '60%%' OR k.symbol LIKE '00%%') -- 只要主板
      AND s.name NOT LIKE '%%ST%%'
    ORDER BY k.symbol, k.trade_date ASC
    """
    
    try:
        df_all = pd.read_sql(query, engine)
    except Exception as e:
        print(f"❌ 数据库查询失败: {e}")
        return

    if df_all.empty:
        print("今日热门板块中未发现符合条件的原始数据。")
        return

    potential_list = [] # 预警：今日出长针
    confirm_list = []   # 确认：昨日出针，今日转一致

    # 2. 按股票分组计算
    print(f"正在对 {df_all['symbol'].nunique()} 只热门题材股进行形态穿透...")
    
    for symbol, df in df_all.groupby('symbol'):
        if len(df) < 10: continue
        
        # 指标计算
        df['ma20'] = ta.sma(df['close'], length=20)
        
        t2 = df.iloc[-1] # 今天
        t1 = df.iloc[-2] # 昨天
        
        def check_needle(row):
            """判定长下影线形态"""
            total_range = row['high'] - row['low']
            if total_range == 0: return False
            body = abs(row['close'] - row['open'])
            lower_shadow = min(row['open'], row['close']) - row['low']
            upper_shadow = row['high'] - max(row['open'], row['close'])
            # 下影线 > 实体 2 倍 且 占振幅 60% 以上
            return (lower_shadow > body * 2) and (lower_shadow / total_range > 0.6) and (upper_shadow / total_range < 0.2)

        # --- 逻辑 A：今日刚出针（资金在热门板块内疯狂洗盘） ---
        if check_needle(t2):
            if t2['close'] < t2['ma20'] * 1.1: # 排除高位见顶针
                potential_list.append({
                    '代码': symbol, '名称': t2['name'], '所属题材': t2['hot_sector'],
                    '现价': t2['close'], '题材流入(亿)': t2['net_inflow_amount'],
                    '状态': '今日洗盘中'
                })

        # --- 逻辑 B：昨日出针，今日转一致（热门板块反攻买点） ---
        if check_needle(t1):
            yest_high_body = max(t1['open'], t1['close'])
            # 确认逻辑：今日收盘反包昨日实体 且 不破昨日底
            if (t2['low'] >= t1['low']) and (t2['close'] > yest_high_body):
                confirm_list.append({
                    '代码': symbol, '名称': t2['name'], '所属题材': t2['hot_sector'],
                    '买入价': t2['close'], '今日涨幅%': round((t2['close']-t1['close'])/t1['close']*100, 2),
                    '板块流入(亿)': t2['net_inflow_amount']
                })

    # 3. 输出结果
    print("\n" + "💰" * 8 + " 阶段一：吸金板块 + 今日分歧点 (待观察) " + "💰" * 8)
    if potential_list:
        print(pd.DataFrame(potential_list).to_string(index=False))
    else:
        print("暂无")

    print("\n" + "🚀" * 8 + " 阶段二：吸金板块 + 确认转一致 (强势买入) " + "🚀" * 8)
    if confirm_list:
        df_confirm = pd.DataFrame(confirm_list).sort_values('板块流入(亿)', ascending=False)
        print(df_confirm.to_string(index=False))
        print("\n" + "-"*85)
        print("💡 操盘研判：")
        print("1. 为什么买：这些个股所属板块正在大量吸金，且个股完成了‘探底-确认’的洗盘动作。")
        print("2. 止损设定：昨日长下影线的最低点。")
        print("3. 优先级：优先选‘板块流入(亿)’排名靠前的个股，板块效应越强，反弹越稳。")
    else:
        print("今日吸金板块中暂未发现‘反包’信号。")

if __name__ == "__main__":
    screen_hot_sector_reversal()