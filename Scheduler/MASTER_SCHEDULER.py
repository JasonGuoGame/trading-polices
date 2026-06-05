import datetime
import subprocess
import sys
import os
import time

# ================= 配置区 =================
# 脚本路径定义 (顺序执行全量工作流)
PIPELINE_QUEUE = [
    r"C:\ws\trading-polices\Database\日线数据\DAILY_UPDATE_MYSQL.py",
    r"C:\ws\trading-polices\Database\因子数据库\UPDATE_FACTORS_INCREMENTAL.py",
    r"C:\ws\trading-polices\Database\分时数据\SYNC_30D_MINUTES.py",
    r"C:\ws\trading-polices\Polices\资金异动\CAPITAL_ABNORMAL_SCAN_DATABASE.py",
    # AKShare always encountered Remote end closed connection without response
    r"c:\ws\trading-polices\AKShare\SYNC_THS_FLOW_TO_DB.py", 
    r"c:\ws\trading-polices\Util\复盘\SCREEN_NEW_20B_STOCKS.py",
    r"C:\ws\trading-polices\Polices\主线\FIND_THEME_LEADER_FINAL.py",
    r"C:\ws\trading-polices\Watchlist\SYNC_MOMENTUM_STAGES.py",
    r"C:\ws\trading-polices\Watchlist\FIND_MACD_X_MONEY_FLOW.py",
    # r"c:\ws\trading-polices\Polices\分歧转一致\DIVERGENCE_TO_CONSENSUS.py",
    r"c:\ws\trading-polices\Watchlist\STOCK_ALPHA_SCORING.py",
    r"c:\ws\trading-polices\Watchlist\STRATEGY_TREND_FOLLOWING.py",
    r"c:\ws\trading-polices\Watchlist\SYNC_MAIN_FORCE_TO_POOL.py",
    r"c:\ws\trading-polices\Watchlist\STRATEGY_RESEAL_REPAIR.py",
    r"c:\ws\trading-polices\赚钱效应\MARKET_REGIME_JUDGE.py",
    r"C:\ws\trading-polices\Database\DB_ROLLING_MAINTENANCE.py",
    r"c:\ws\trading-polices\Util\复盘\STRATEGY_WIN_RATE_ANALYZER.py",
    r"c:\ws\trading-polices\Util\复盘\ANALYZE_STRATEGY_SCORE_DISTRIBUTION.py",
    r"C:\ws\trading-polices\Util\MARKET_SENTIMENT.py"
]

# 1. 竞价同步脚本路径
PATH_AUCTION = r"C:\ws\trading-polices\Polices\平均竞价量比\SYNC_AUCTION_CORE_LOGIC.py"

PYTHON_PATH = sys.executable
# ==========================================

auction_synced_today = None 

def is_within_running_window():
    """判断当前时刻是否允许开始新的循环"""
    now = datetime.datetime.now()
    current_time = now.strftime("%H:%M")
    
    # 1. 过滤周末
    if now.weekday() > 4:
        return False, "周末休息"

    # 2. 定义运行窗口
    # 10:00开始, 11:30-13:30休息, 16:00以后停止
    is_morning = ("09:50" <= current_time < "11:30")
    is_afternoon = ("13:20" <= current_time < "15:18")
    
    if is_morning:
        return True, "早盘运行中"
    if is_afternoon:
        return True, "午盘运行中"
    
    return False, "非运行时间段（或中午休市）"

def run_one_cycle(cycle_count):
    """执行一轮完整的工作流"""
    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"\n" + "🌀" * 5 + f" 启动第 {cycle_count} 轮循环 | 开始时间: {now_str} " + "🌀" * 5)
    print("=" * 80)

    for i, script_path in enumerate(PIPELINE_QUEUE, 1):
        if not os.path.exists(script_path):
            print(f"❌ 找不到文件: {script_path}")
            continue

        script_name = os.path.basename(script_path)
        print(f"▶️  [{i}/{len(PIPELINE_QUEUE)}] 正在执行: {script_name}...")
        
        try:
            # 运行子进程并等待结束
            start_ts = time.time()
            result = subprocess.run([PYTHON_PATH, script_path], check=False)
            duration = time.time() - start_ts
            
            if result.returncode == 0:
                print(f"   ✅ 完成 (用时: {duration:.1f}s)")
            else:
                print(f"   ⚠️ 失败 (退出码: {result.returncode})，为了数据安全，中止本轮后续脚本。")
                return False # 本轮循环失败
        except Exception as e:
            print(f"   💥 崩溃: {e}")
            return False
            
    print("=" * 80)
    print(f"🏁 第 {cycle_count} 轮循环顺利结束。")
    return True

def main_loop():
    global auction_synced_today
    cycle_count = 1
    print("🚀 往复式流水线调度中心已启动...")
    print(f"📍 监控脚本总数: {len(PIPELINE_QUEUE)}")
    print(f"🕒 设定：10:00 准时爆发，11:30-13:30 午休，16:00 鸣金收兵。")
    print("-" * 60)

    while True:
        now = datetime.datetime.now()
        current_date = now.date()
        current_time_str = now.strftime("%H:%M")
        
        # --- 核心新增：09:26 竞价同步触发逻辑 ---
        if now.weekday() <= 4: # 周一到周五
            if current_time_str == "09:29" and auction_synced_today != current_date:
                print(f"\n" + "🔔" * 5 + " 触发每日 09:26 竞价同步任务 " + "🔔" * 5)
                subprocess.run([PYTHON_PATH, PATH_AUCTION], check=False)
                auction_synced_today = current_date # 标记今天已运行
                print("✅ 竞价任务执行完毕，继续等待 10:00 循环开启。")

        can_run, reason = is_within_running_window()
        
        if can_run:
            # 执行一轮
            success = run_one_cycle(cycle_count)
            if success:
                cycle_count += 1
            
            # 每一轮跑完后微调休息 5 秒，防止极端情况下 CPU 负载过高
            time.sleep(5)
        else:
            # 如果没到 10:00 或者处于午休
            now = datetime.datetime.now().strftime("%H:%M:%S")
            # 只有在整分钟时打印一次状态，避免刷屏
            if datetime.datetime.now().second == 0:
                print(f"😴 等待中... 当前时刻: {now} | 状态: {reason}")
            
            time.sleep(1) # 每秒检查一次时间

if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        print("\n👋 收到指令，正在安全关闭调度中心...")