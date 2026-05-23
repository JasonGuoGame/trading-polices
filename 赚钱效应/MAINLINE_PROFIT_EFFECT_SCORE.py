import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import datetime

# --- 数据库配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db')

def analyze_mainline_effect():
    print(f"[{datetime.datetime.now()}] 正在扫描全市场‘题材主线’赚钱效应...")

    with engine.connect() as conn:
        # 1. 检查日期
        date_res = conn.execute(text("SELECT DISTINCT trade_date FROM stk_sector_fund_flow ORDER BY trade_date DESC LIMIT 5")).fetchall()
        if len(date_res) < 2:
            print(f"❌ 退出：资金流向表数据不足（当前仅有 {len(date_res)} 天数据），至少需要 2 天进行对比。")
            return
        
        dates = [r[0] for r in date_res]
        today = dates[0]
        print(f"📊 正在分析日期: {today}")

        # 2. 统计板块持续吸金能力
        flow_sql = text("SELECT sector_name, trade_date, net_inflow_amount FROM stk_sector_fund_flow WHERE trade_date >= :start_date")
        df_flow = pd.read_sql(flow_sql, conn, params={"start_date": dates[-1]})
        
        if df_flow.empty:
            print("❌ 退出：无法从 stk_sector_fund_flow 提取到数据。")
            return

        continuity_results = []
        for sector, group in df_flow.groupby('sector_name'):
            group = group.sort_values('trade_date', ascending=False)
            days = 0
            total_money = 0
            for amt in group['net_inflow_amount']:
                if float(amt) > 0:
                    days += 1
                    total_money += float(amt)
                else:
                    break
            if days > 0: # 只记录有流入的板块
                continuity_results.append({'板块': sector, '连续流入天数': days, '近期总流入': total_money})
        
        if not continuity_results:
            print("❌ 退出：最近几日全市场没有任何板块处于‘持续净流入’状态。")
            return

        df_continuity = pd.DataFrame(continuity_results).sort_values('近期总流入', ascending=False)
        top_mainline = df_continuity.iloc[0]
        print(f"🔎 锁定当前最强潜伏主线: {top_mainline['板块']} (连续流入 {top_mainline['连续流入天数']} 天)")

        # 3. 核心关联：找龙头 (增加名称兼容性逻辑)
        # 解决 '行业-名称' 与 '名称' 的对齐问题
        target_sector = top_mainline['板块']
        leader_sql = text("""
            SELECT s.symbol, s.name, k.close, k.amount
            FROM stock_sector_relation r
            JOIN stocks s ON r.symbol = s.symbol
            JOIN stk_daily_kline k ON s.symbol = k.symbol AND k.trade_date = :today
            WHERE (r.sector_name = :s OR r.sector_name = CONCAT('行业-', :s) OR r.sector_name = CONCAT('概念-', :s))
            ORDER BY k.amount DESC LIMIT 1
        """)
        leader_res = pd.read_sql(leader_sql, conn, params={"today": today, "s": target_sector})
        
        if leader_res.empty:
            print(f"⚠️ 警告：在板块【{target_sector}】中找不到对应的个股行情，请检查 stock_sector_relation 表。")
            return

        leader_name = leader_res['name'].iloc[0]
        leader_sym = leader_res['symbol'].iloc[0]
        curr_price = float(leader_res['close'].iloc[0])

        # 4. 检查龙头是否创新高 (20日线)
        high_sql = text("SELECT MAX(high) FROM stk_daily_kline WHERE symbol = :sym AND trade_date < :today AND trade_date >= DATE_SUB(:today, INTERVAL 30 DAY)")
        prev_high = conn.execute(high_sql, {"sym": leader_sym, "today": today}).fetchone()[0] or 0
        is_new_high = curr_price > float(prev_high)

    # 5. 计算得分 (100分制)
    score = 0
    score += np.clip(top_mainline['连续流入天数'] * 15, 0, 45) # 持续性 45分
    score += np.clip(top_mainline['近期总流入'] / 10 * 30, 0, 30) # 资金量 30分
    if is_new_high: score += 25 # 龙头新高 25分

    # 6. 输出最终报告
    print("\n" + "💎" * 15)
    print(f"📊 A股主线赚钱效应报告 ({today})")
    print("-" * 40)
    print(f"🔥 核心题材: {target_sector}")
    print(f"💰 累计流入: {top_mainline['近期总流入']:.2f} 亿 ({top_mainline['连续流入天数']}天连入)")
    print(f"👑 领军龙头: {leader_name} ({leader_sym})")
    print(f"📈 龙头走势: {'🚀 创近期新高' if is_new_high else '横盘整理中'}")
    print("-" * 40)
    print(f"🌡️ 主线模式得分: {score:.1f}")

    if score >= 70:
        res = "🟢 强：主线主升浪，闭眼入！"
    elif score >= 40:
        res = "🟡 中：主线分歧，适合低吸。"
    else:
        res = "🔴 弱：主线退潮，注意避险。"
    print(f"💡 结论: {res}")
    print("💎" * 15 + "\n")

if __name__ == "__main__":
    analyze_mainline_effect()