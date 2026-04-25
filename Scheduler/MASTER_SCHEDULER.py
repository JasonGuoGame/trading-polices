from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
import datetime
import subprocess
import sys
import os

# ================= 配置区 =================
# 是否在启动脚本时立即运行一次全流程？
# True: 立即运行 | False: 等待定时时间点
RUN_NOW_SWITCH = True 

# 脚本路径定义
PATH_DAILY = r"C:\ws\data\Database\日线数据\DAILY_UPDATE_MYSQL.py"
PATH_FACTORS = r"C:\ws\data\Database\因子数据库\UPDATE_FACTORS_INCREMENTAL.py"
PATH_MINUTES = r"C:\ws\data\Database\分时数据\SYNC_30D_MINUTES.py"
PATH_ABNORMAL = r"C:\ws\data\Polices\资金异动\CAPITAL_ABNORMAL_SCAN_DATABASE.py"
PATH_MACD = r"C:\ws\data\Polices\MACD\FIND_MACD_GOLD_CROSS.py"

PYTHON_PATH = sys.executable
# ==========================================

def run_pipeline(force_run=False):
    now = datetime.datetime.now()
    current_time = now.strftime("%H:%M")
    
    print(f"\n" + "="*70)
    print(f"🔔 任务触发 | 当前系统时间: {now.strftime('%Y-%m-%d %H:%M:%S')}")

    # --- 1. 逻辑判断：是否需要跳过时间检查 ---
    if not force_run:
        # A. 过滤周末
        if now.weekday() > 4:
            print(f"[{current_time}] 休息日，跳过任务。")
            return

        # B. 定义交易时间窗口
        is_morning_session = ("09:55" <= current_time <= "11:40")
        is_afternoon_session = ("13:25" <= current_time <= "15:10")
        
        if not (is_morning_session or is_afternoon_session):
            print(f"[{current_time}] 非交易时段，脚本不执行。")
            return
    else:
        print(f"🚀 [强制运行模式]：跳过交易时间检查...")

    # --- 2. 模式判定：全量 vs 分时 ---
    full_sync_hours = ["10:05", "11:05", "14:05", "15:05"]
    
    # 检查是否在全量更新时间点附近 (±5分钟)
    is_full_sync_time = False
    for target_time in full_sync_hours:
        target_dt = datetime.datetime.strptime(target_time, "%H:%M")
        current_dt = datetime.datetime.strptime(current_time, "%H:%M")
        if abs((current_dt - target_dt).total_seconds()) <= 300:
            is_full_sync_time = True
            break

    # 如果是强制运行，或者处于全量时间点
    if force_run or is_full_sync_time:
        print(f"🌟 [模式：全量更新] (日线+因子+分时+异动)")
        pipeline_queue = [PATH_DAILY, PATH_FACTORS, PATH_MINUTES, PATH_ABNORMAL, PATH_MACD]
    else:
        print(f"⚡ [模式：分时扫描] (仅分时+异动)")
        pipeline_queue = [PATH_MINUTES, PATH_ABNORMAL,PATH_MACD]

    print(f"⏰ 实际启动时刻：{current_time}")

    # --- 3. 顺序执行脚本 ---
    for i, script_path in enumerate(pipeline_queue, 1):
        script_name = os.path.basename(script_path)
        print(f"   >>> 步骤 [{i}/{len(pipeline_queue)}]: 正在执行 {script_name} ...")
        
        try:
            result = subprocess.run([PYTHON_PATH, script_path], check=False)
            if result.returncode == 0:
                print(f"       ✅ 成功。")
            else:
                print(f"       ❌ 失败 (退出码: {result.returncode})。中止后续步骤。")
                break
        except Exception as e:
            print(f"       💥 异常: {e}")
            break

    print(f"🏁 本轮流水线处理结束。")
    print("="*70)

if __name__ == "__main__":
    # --- A. 立即运行开关判断 ---
    if RUN_NOW_SWITCH:
        print("💡 检测到立即运行开关已打开，正在启动首次全量任务...")
        run_pipeline(force_run=True)

    # --- B. 配置定时调度器 ---
    scheduler = BlockingScheduler()

    # 这里的 minute='5,35' 是为了配合你的 10:05, 11:05 等全量点
    trigger = CronTrigger(
        day_of_week='mon-fri', 
        hour='10,11,13,14,15', 
        minute='5,35',
        second=0
    )

    print(f"\n📅 自动化调度中心已就绪")
    print(f"🚀 定时计划：每小时的 05分 和 35分 自动触发")
    print("-" * 70)

    scheduler.add_job(
        run_pipeline,
        trigger=trigger,
        id='quant_smart_pipeline',
        misfire_grace_time=60 
    )

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("\n👋 调度器已手动关闭。")