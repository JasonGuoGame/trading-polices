# -*- coding: utf-8 -*-

"""
Backbone V2 配置文件
板块中军识别系统
"""

# ==========================
# 数据库配置
# ==========================

QUANT_DB_URL = (
    "mysql+pymysql://root:root_secret_2026"
    "@localhost:3306/quant_db"
)


REVIEW_DB_URL = (
    "mysql+pymysql://root:root_secret_2026"
    "@localhost:3306/trading_review"
)


# ==========================
# Backbone评分权重
# ==========================

WEIGHTS = {

    # 股票自身 80分

    # 资金持续
    "capital": 20,

    # 成交额稳定
    "amount": 15,

    # 趋势
    "trend": 15,

    # 板块同步
    "sync": 15,

    # 攻击力度
    "attack": 10,

    # 板块贡献
    "contribution": 5,


    # 板块加成
    "sector_bonus": 20
}



# ==========================
# 参数
# ==========================

# 分析多少天历史
LOOKBACK_DAYS = 20


# 输出排名数量
TOP_N = 5



# ==========================
# stock_pool配置
# ==========================

POOL_TYPE = "short"

STATUS = "BACKBONE_V2"



# ==========================
# 角色定义
# ==========================

def get_role(score):

    if score >= 90:
        return "超级中军"

    elif score >= 85:
        return "核心中军"

    elif score >= 75:
        return "板块骨干"

    elif score >= 65:
        return "先锋"

    else:
        return "跟随"



def get_watch_level(score):

    if score >= 90:
        return 5

    elif score >= 85:
        return 4

    elif score >= 75:
        return 3

    else:
        return 2