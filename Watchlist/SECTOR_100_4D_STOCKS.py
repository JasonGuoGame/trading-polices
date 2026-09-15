import pandas as pd
import numpy as np
import datetime
from sqlalchemy import create_engine, text


# ============================================================
# 数据库配置
# ============================================================

QUANT_DB_URL = (
    "mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db"
)

REVIEW_DB_URL = (
    "mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review"
)

quant_engine = create_engine(QUANT_DB_URL)
review_engine = create_engine(REVIEW_DB_URL)


# ============================================================
# 基础配置
# ============================================================

TODAY = datetime.date.today()

# 如果需要指定交易日期，可以直接修改这里
# TODAY = datetime.date(2026, 9, 11)

STATUS = "四维共振"

TOP_N = 5


# ============================================================
# 工具函数
# ============================================================

def safe_float(value, default=0.0):
    """
    安全转换 float
    """
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def clean_sector_name(sector_name):
    """
    清洗板块名称

    例如：
        行业-通信 -> 通信
        行业-计算机 -> 计算机
        概念-国资云概念 -> 国资云概念
    """
    if sector_name is None:
        return ""

    sector_name = str(sector_name).strip()

    if sector_name.startswith("行业-"):
        sector_name = sector_name[3:]

    return sector_name


# ============================================================
# 1. 获取某张表当天最新的公共 snapshot_time
# ============================================================

def get_latest_snapshot_time(table_name, trade_date):
    """
    获取某张表当天最新 snapshot_time。

    注意：
    不是每个板块自己 MAX(snapshot_time)，
    而是整个表当天统一取 MAX(snapshot_time)。

    这样可以保证 Top5 中的板块来自同一个盘中快照。
    """

    sql = text(f"""
        SELECT MAX(snapshot_time)
        FROM {table_name}
        WHERE trade_date = :trade_date
          AND snapshot_time IS NOT NULL
    """)

    with review_engine.connect() as conn:
        result = conn.execute(
            sql,
            {"trade_date": trade_date}
        ).scalar()

    return result


# ============================================================
# 2. 获取 stk_sector_scores Top5
# ============================================================

def get_top5_from_scores(trade_date):
    """
    从 stk_sector_scores 独立寻找 Top5。

    排序逻辑：

    1. rank_change DESC
    2. rank_pos ASC

    rank_change 越大，说明排名提升越明显。
    """

    table_name = "stk_sector_scores"

    latest_snapshot = get_latest_snapshot_time(
        table_name,
        trade_date
    )

    if latest_snapshot is None:
        print(
            f"[WARN] {table_name} "
            f"{trade_date} 没有 snapshot_time 数据"
        )
        return pd.DataFrame()

    print()
    print("=" * 80)
    print("【1】stk_sector_scores 独立寻找 Top5")
    print("=" * 80)

    print(f"最新 snapshot_time: {latest_snapshot}")

    sql = text("""
        SELECT
            trade_date,
            sector_name,
            total_score,
            rank_pos,
            rank_change,
            persistence_7d,
            is_leader,
            high_20d_count,
            high_60d_count,
            high_250d_count,
            snapshot_time
        FROM stk_sector_scores
        WHERE trade_date = :trade_date
          AND snapshot_time = :snapshot_time
        ORDER BY
            rank_change DESC,
            rank_pos ASC
        LIMIT 5
    """)

    df = pd.read_sql(
        sql,
        review_engine,
        params={
            "trade_date": trade_date,
            "snapshot_time": latest_snapshot
        }
    )

    if df.empty:
        print("没有找到 Top5")
        return df

    df["source_type"] = "强度"

    print()
    print(
        f"{'排名':<6}"
        f"{'板块':<25}"
        f"{'当前排名':<10}"
        f"{'排名变化':<10}"
        f"{'总分':<10}"
    )

    print("-" * 80)

    for idx, row in df.iterrows():

        print(
            f"{idx + 1:<6}"
            f"{str(row['sector_name']):<25}"
            f"{int(row['rank_pos']):<10}"
            f"{safe_float(row['rank_change']):<10.0f}"
            f"{safe_float(row['total_score']):<10.2f}"
        )

    return df


# ============================================================
# 3. 获取 stk_sector_breadths Top5
# ============================================================

def get_top5_from_breadths(trade_date):
    """
    从 stk_sector_breadths 独立寻找 Top5。

    只使用：

        sector_type = industry

    排序：

        rank_change DESC
        rank_pos ASC
    """

    table_name = "stk_sector_breadths"

    latest_snapshot = get_latest_snapshot_time(
        table_name,
        trade_date
    )

    if latest_snapshot is None:
        print(
            f"[WARN] {table_name} "
            f"{trade_date} 没有 snapshot_time 数据"
        )
        return pd.DataFrame()

    print()
    print("=" * 80)
    print("【2】stk_sector_breadths 独立寻找 Top5")
    print("=" * 80)

    print(f"最新 snapshot_time: {latest_snapshot}")

    sql = text("""
        SELECT
            trade_date,
            sector_name,
            sector_type,
            red_rate,
            advancers,
            total_stocks,
            rank_pos,
            rank_change,
            persistence_7d,
            is_leader,
            high_20d_count,
            high_60d_count,
            high_250d_count,
            snapshot_time
        FROM stk_sector_breadths
        WHERE trade_date = :trade_date
          AND snapshot_time = :snapshot_time
          AND sector_type = 'industry'
        ORDER BY
            rank_change DESC,
            rank_pos ASC
        LIMIT 5
    """)

    df = pd.read_sql(
        sql,
        review_engine,
        params={
            "trade_date": trade_date,
            "snapshot_time": latest_snapshot
        }
    )

    if df.empty:
        print("没有找到 Top5")
        return df

    df["source_type"] = "宽度"

    print()
    print(
        f"{'排名':<6}"
        f"{'板块':<25}"
        f"{'当前排名':<10}"
        f"{'排名变化':<10}"
        f"{'红盘率':<10}"
    )

    print("-" * 80)

    for idx, row in df.iterrows():

        print(
            f"{idx + 1:<6}"
            f"{str(row['sector_name']):<25}"
            f"{int(row['rank_pos']):<10}"
            f"{safe_float(row['rank_change']):<10.0f}"
            f"{safe_float(row['red_rate']):<10.2f}"
        )

    return df


# ============================================================
# 4. 合并两个 Top5 的板块
# ============================================================

def build_selected_sectors(df_score_top5, df_breadth_top5):
    """
    注意：

    这里不是把两个表的 rank_change 相加。

    两张表各自产生 Top5。

    如果同一个板块同时进入两个 Top5：
        -> 最终只保留一个板块
        -> 后续只寻找一个龙头

    保留规则：

        优先保留 rank_change 更大的来源
        如果相同，则保留 rank_pos 更靠前的来源
    """

    records = []

    if df_score_top5 is not None and not df_score_top5.empty:

        for _, row in df_score_top5.iterrows():

            sector = clean_sector_name(row["sector_name"])

            records.append({
                "sector_name": sector,
                "source_type": "强度",
                "rank_change": safe_float(row["rank_change"]),
                "rank_pos": int(row["rank_pos"]),
                "snapshot_time": row["snapshot_time"],
                "total_score": safe_float(
                    row.get("total_score", 0)
                ),
                "red_rate": None
            })

    if df_breadth_top5 is not None and not df_breadth_top5.empty:

        for _, row in df_breadth_top5.iterrows():

            sector = clean_sector_name(row["sector_name"])

            records.append({
                "sector_name": sector,
                "source_type": "宽度",
                "rank_change": safe_float(row["rank_change"]),
                "rank_pos": int(row["rank_pos"]),
                "snapshot_time": row["snapshot_time"],
                "total_score": None,
                "red_rate": safe_float(
                    row.get("red_rate", 0)
                )
            })

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)

    # --------------------------------------------------------
    # 同一个板块如果同时进入两个 Top5
    #
    # 不合并 rank_change
    #
    # 只选择一个来源记录
    # --------------------------------------------------------

    df = df.sort_values(
        [
            "sector_name",
            "rank_change",
            "rank_pos"
        ],
        ascending=[
            True,
            False,
            True
        ]
    )

    df = df.drop_duplicates(
        subset=["sector_name"],
        keep="first"
    )

    # 最终展示顺序
    df = df.sort_values(
        [
            "rank_change",
            "rank_pos"
        ],
        ascending=[
            False,
            True
        ]
    ).reset_index(drop=True)

    return df


# ============================================================
# 5. 获取指定板块的股票
# ============================================================

def get_sector_stocks(sectors):
    """
    从 stock_sector_relation 获取指定板块的股票。

    一个股票可能属于多个板块。
    这里保留所有关系。

    后面会按照每一个板块分别寻找自己的龙头。
    """

    if not sectors:
        return pd.DataFrame()

    # 去重
    sectors = list(dict.fromkeys(sectors))

    # 使用 OR / LIKE 匹配，保持和原来的逻辑兼容
    conditions = []
    params = {}

    for i, sector in enumerate(sectors):

        param_name = f"sector_{i}"

        # 这里使用：
        # sector_name = xxx
        # 或 sector_name LIKE %xxx%
        conditions.append(
            f"""
            (
                r.sector_name = :{param_name}
                OR r.sector_name LIKE :like_{param_name}
            )
            """
        )

        params[param_name] = sector
        params[f"like_{param_name}"] = f"%{sector}%"

    where_sql = " OR ".join(conditions)

    sql = text(f"""
        SELECT
            r.symbol,
            s.name AS stock_name,
            r.sector_name
        FROM stock_sector_relation r
        JOIN stocks s
          ON r.symbol = s.symbol
        WHERE
            ({where_sql})
            AND s.name NOT LIKE '%ST%'
            AND s.name NOT LIKE '%退%'
    """)

    df = pd.read_sql(
        sql,
        quant_engine,
        params=params
    )

    if df.empty:
        return df

    # --------------------------------------------------------
    # 建立股票 -> 所属目标板块
    # --------------------------------------------------------

    mapping = (
        df.groupby(
            ["symbol", "stock_name"]
        )["sector_name"]
        .apply(list)
        .reset_index()
    )

    mapping.rename(
        columns={
            "sector_name": "sector_names"
        },
        inplace=True
    )

    mapping["sector_names"] = mapping["sector_names"].apply(
        lambda x: [
            clean_sector_name(v)
            for v in x
        ]
    )

    return mapping


# ============================================================
# 6. 获取因子数据
# ============================================================

def get_stock_factors(symbols, trade_date):

    if not symbols:
        return pd.DataFrame()

    syms = ",".join(
        "'" + str(s).replace("'", "''") + "'"
        for s in symbols
    )

    sql = f"""
        SELECT
            symbol,
            f_mom_20,
            f_macd_dif,
            f_macd_dea,
            f_macd_hist,
            f_bb_m,
            f_quantity_ratio,
            f_dist_high
        FROM stk_factors
        WHERE trade_date = '{trade_date}'
          AND symbol IN ({syms})
    """

    return pd.read_sql(
        sql,
        quant_engine
    )


# ============================================================
# 7. 获取日线数据
# ============================================================

def get_stock_kline(symbols, trade_date):

    if not symbols:
        return pd.DataFrame()

    syms = ",".join(
        "'" + str(s).replace("'", "''") + "'"
        for s in symbols
    )

    sql = f"""
        SELECT
            symbol,
            open,
            close,
            turnover_rate
        FROM stk_daily_kline
        WHERE trade_date = '{trade_date}'
          AND symbol IN ({syms})
    """

    return pd.read_sql(
        sql,
        quant_engine
    )


# ============================================================
# 8. 获取资金数据
# ============================================================

def get_stock_fund_flow(symbols, trade_date):

    if not symbols:
        return pd.DataFrame()

    syms = ",".join(
        "'" + str(s).replace("'", "''") + "'"
        for s in symbols
    )

    sql = f"""
        SELECT
            symbol,
            main_net_inflow,
            main_net_ratio,
            inflow_3d,
            buy_power_ratio,
            attack_score,
            capital_score,
            volume_power_ratio
        FROM stk_stock_fund_flow
        WHERE trade_date = '{trade_date}'
          AND symbol IN ({syms})
    """

    return pd.read_sql(
        sql,
        quant_engine
    )


# ============================================================
# 9. 获取筹码数据
# ============================================================

def get_stock_chip(symbols, trade_date):

    if not symbols:
        return pd.DataFrame()

    syms = ",".join(
        "'" + str(s).replace("'", "''") + "'"
        for s in symbols
    )

    sql = f"""
        SELECT
            symbol,
            profit_ratio,
            chip_score
        FROM stk_chip_factor
        WHERE trade_date = '{trade_date}'
          AND symbol IN ({syms})
    """

    return pd.read_sql(
        sql,
        quant_engine
    )


# ============================================================
# 10. 四维共振评分
# ============================================================

def calc_4d_score(row):

    score = 0

    # --------------------------------------------------------
    # 1. 恒强板块权重分
    # 20分
    # --------------------------------------------------------

    score += min(
        safe_float(row.get("sector_max_inflow_rate", 0)) * 0.2,
        20
    )

    # --------------------------------------------------------
    # 2. 资金规模分
    # 25分
    # --------------------------------------------------------

    score += (
        safe_float(
            row.get("capital_score", 0)
        ) * 0.15
    )

    score += min(
        safe_float(
            row.get("main_net_ratio", 0)
        ),
        10
    )

    # --------------------------------------------------------
    # 3. 资金攻击分
    # 30分
    # --------------------------------------------------------

    score += min(
        safe_float(
            row.get("buy_power_ratio", 0)
        ) / 100 * 15,
        15
    )

    score += min(
        safe_float(
            row.get("attack_score", 0)
        ) / 100 * 15,
        15
    )

    # --------------------------------------------------------
    # 4. 趋势分
    # 15分
    # --------------------------------------------------------

    score += min(
        safe_float(
            row.get("f_mom_20", 0)
        ) * 100,
        10
    )

    score += max(
        0,
        5 - safe_float(
            row.get("f_dist_high", 10)
        )
    )

    # --------------------------------------------------------
    # 5. 筹码 / 量价
    # 10分
    # --------------------------------------------------------

    score += (
        safe_float(
            row.get("chip_score", 0)
        ) / 100 * 5
    )

    qr = safe_float(
        row.get("f_quantity_ratio", 1)
    )

    if 1.5 <= qr <= 5.5:
        score += 5

    return round(score, 2)


# ============================================================
# 11. 四维共振股票过滤
# ============================================================

def filter_4d_stocks(df_all):

    if df_all.empty:
        return df_all

    # ========================================================
    # 1. 趋势条件
    # ========================================================

    cond_trend = (
        (df_all["f_mom_20"] > 0)
        &
        (df_all["f_macd_dif"] > df_all["f_macd_dea"])
        &
        (df_all["f_macd_hist"] > 0)
        &
        (df_all["close"] > df_all["f_bb_m"])
    )

    # ========================================================
    # 2. 资金条件
    # ========================================================

    cond_fund = (
        (df_all["main_net_inflow"] > 0)
        &
        (df_all["inflow_3d"] > 0)
        &
        (df_all["capital_score"] >= 70)
        &
        (df_all["buy_power_ratio"] >= 55)
        &
        (df_all["volume_power_ratio"] >= 1.1)
    )

    # ========================================================
    # 3. 筹码条件
    # ========================================================

    cond_chip = (
        (df_all["profit_ratio"] > 60)
        &
        (df_all["chip_score"] > 60)
    )

    # ========================================================
    # 4. 量价条件
    # ========================================================

    cond_vol = (
        (df_all["close"] > df_all["open"])
        &
        (
            df_all["close"] /
            df_all["open"] > 1.015
        )
        &
        (
            df_all["f_quantity_ratio"].between(
                1.5,
                6.0
            )
        )
        &
        (
            df_all["turnover_rate"].between(
                0.015,
                0.20
            )
        )
    )

    # ========================================================
    # 5. 攻击条件
    # ========================================================

    cond_atk = (
        (df_all["attack_score"] >= 70)
        &
        (df_all["buy_power_ratio"] >= 60)
    )

    # ========================================================
    # 最终四维共振
    # ========================================================

    df_sel = df_all[
        cond_fund
        &
        cond_trend
        &
        cond_chip
        &
        cond_vol
        &
        cond_atk
    ].copy()

    return df_sel


# ============================================================
# 12. 每个板块选择一个龙头
# ============================================================

def select_sector_leaders(
    df_all,
    selected_sectors
):
    """
    核心逻辑：

    每一个 Top5 板块单独寻找自己的龙头。

    例如：

        国资云
        通信
        计算机

    分别过滤属于该板块的股票。

    然后：

        四维共振条件
            ↓
        calc_4d_score
            ↓
        最高分 = 龙头

    注意：

    同一只股票如果属于两个板块，
    它可以分别成为两个板块的龙头。

    因为当前规则是：

        每个板块一个龙头

    而不是：

        全部板块只能有一个股票。
    """

    if df_all.empty:
        return pd.DataFrame()

    leaders = []

    for _, sector_row in selected_sectors.iterrows():

        sector = sector_row["sector_name"]

        # ----------------------------------------------------
        # 找属于当前板块的股票
        # ----------------------------------------------------

        sector_df = df_all[
            df_all["sector_names"].apply(
                lambda x: sector in x
            )
        ].copy()

        if sector_df.empty:
            print(
                f"[WARN] 板块 {sector} 没有候选股票"
            )
            continue

        # ----------------------------------------------------
        # 四维共振过滤
        # ----------------------------------------------------

        sector_df = filter_4d_stocks(
            sector_df
        )

        if sector_df.empty:
            print(
                f"[WARN] 板块 {sector} "
                f"没有股票满足四维共振条件"
            )
            continue

        # ----------------------------------------------------
        # 计算四维评分
        # ----------------------------------------------------

        sector_df["sort_score"] = sector_df.apply(
            calc_4d_score,
            axis=1
        )

        # ----------------------------------------------------
        # 最高分作为当前板块龙头
        # ----------------------------------------------------

        sector_df = sector_df.sort_values(
            "sort_score",
            ascending=False
        )

        leader = sector_df.iloc[0].copy()

        leader["selected_sector"] = sector
        leader["source_type"] = sector_row[
            "source_type"
        ]
        leader["rank_change"] = safe_float(
            sector_row["rank_change"]
        )
        leader["sector_rank_pos"] = int(
            sector_row["rank_pos"]
        )
        leader["sector_snapshot_time"] = (
            sector_row["snapshot_time"]
        )

        leaders.append(leader)

        print()
        print(
            f"[龙头] "
            f"{sector:<20} "
            f"{leader['symbol']:<12} "
            f"{leader['stock_name']:<12} "
            f"四维评分={leader['sort_score']:.2f} "
            f"来源={leader['source_type']} "
            f"rank_change={leader['rank_change']:.0f}"
        )

    if not leaders:
        return pd.DataFrame()

    return pd.DataFrame(leaders)


# ============================================================
# 13. 保存到 stock_pools
# ============================================================

def save_to_stock_pool(
    leaders,
    trade_date
):
    """
    保存到：

        trading_review.stock_pools

    status：

        四维共振

    不再使用：

        combined_rank_change
        breadth_rank_change
        score_rank_change

    因为两个 Top5 是独立计算的。
    """

    if leaders.empty:
        print()
        print("[INFO] 没有符合条件的板块龙头，不写入 stock_pools")
        return

    print()
    print("=" * 80)
    print("【保存】写入 stock_pools")
    print("=" * 80)

    sql = text("""
        INSERT INTO stock_pools (
            symbol,
            trade_date,
            stock_name,
            pool_type,
            sector_name,
            score,
            status,
            tags,
            notes,
            created_at,
            updated_at,
            is_watch_focus,
            watch_level
        )
        VALUES (
            :symbol,
            :trade_date,
            :stock_name,
            :pool_type,
            :sector_name,
            :score,
            :status,
            :tags,
            :notes,
            NOW(),
            NOW(),
            :is_watch_focus,
            :watch_level
        )
        ON DUPLICATE KEY UPDATE

            stock_name = VALUES(stock_name),
            sector_name = VALUES(sector_name),
            score = VALUES(score),
            tags = VALUES(tags),
            notes = VALUES(notes),
            updated_at = NOW()
    """)

    rows = []

    for _, row in leaders.iterrows():

        symbol = str(row["symbol"])
        stock_name = str(row["stock_name"])
        sector = str(row["selected_sector"])

        score = safe_float(
            row.get("sort_score", 0)
        )

        source_type = str(
            row.get("source_type", "")
        )

        rank_change = safe_float(
            row.get("rank_change", 0)
        )

        rank_pos = int(
            row.get("sector_rank_pos", 0)
        )

        snapshot_time = row.get(
            "sector_snapshot_time"
        )

        capital_score = safe_float(
            row.get("capital_score", 0)
        )

        profit_ratio = safe_float(
            row.get("profit_ratio", 0)
        )

        attack_score = safe_float(
            row.get("attack_score", 0)
        )

        buy_power_ratio = safe_float(
            row.get("buy_power_ratio", 0)
        )

        chip_score = safe_float(
            row.get("chip_score", 0)
        )

        tags = (
            f"策略={STATUS},"
            f"来源={source_type},"
            f"板块排名变化={rank_change:.0f},"
            f"板块排名={rank_pos},"
            f"资金评分={capital_score:.2f},"
            f"筹码评分={chip_score:.2f},"
            f"攻击评分={attack_score:.2f}"
        )

        notes = (
            f"板块={sector};"
            f"板块来源={source_type};"
            f"rank_change={rank_change:.0f};"
            f"rank_pos={rank_pos};"
            f"snapshot_time={snapshot_time};"
            f"四维评分={score:.2f};"
            f"capital_score={capital_score:.2f};"
            f"profit_ratio={profit_ratio:.2f};"
            f"attack_score={attack_score:.2f};"
            f"buy_power_ratio={buy_power_ratio:.2f};"
            f"chip_score={chip_score:.2f}"
        )

        rows.append({
            "symbol": symbol,
            "trade_date": trade_date,
            "stock_name": stock_name,

            # 保持原来的 pool_type
            "pool_type": "short",

            "sector_name": sector,
            "score": score,
            "status": STATUS,

            "tags": tags,
            "notes": notes,

            # 龙头建议默认重点观察
            "is_watch_focus": 1,
            "watch_level": 1
        })

    if not rows:
        return

    with review_engine.begin() as conn:

        for row in rows:

            conn.execute(
                sql,
                row
            )

    print()
    print(
        f"[OK] 成功写入 / 更新 "
        f"{len(rows)} 个板块龙头"
    )


# ============================================================
# 14. 打印最终结果
# ============================================================

def print_final_result(
    selected_sectors,
    leaders
):
    print()
    print()
    print("=" * 100)
    print("最终结果")
    print("=" * 100)

    print()
    print("【Top5 + Top5 去重后的板块】")

    print(
        f"{'序号':<6}"
        f"{'板块':<22}"
        f"{'来源':<8}"
        f"{'Rank变化':<10}"
        f"{'Rank':<8}"
    )

    print("-" * 100)

    for idx, row in selected_sectors.iterrows():

        print(
            f"{idx + 1:<6}"
            f"{str(row['sector_name']):<22}"
            f"{str(row['source_type']):<8}"
            f"{safe_float(row['rank_change']):<10.0f}"
            f"{int(row['rank_pos']):<8}"
        )

    print()
    print("【每个板块一个龙头】")

    if leaders.empty:
        print("没有找到符合四维共振条件的龙头。")
        return

    print(
        f"{'序号':<6}"
        f"{'板块':<20}"
        f"{'股票':<12}"
        f"{'名称':<12}"
        f"{'四维评分':<10}"
        f"{'来源':<8}"
        f"{'Rank变化':<10}"
    )

    print("-" * 100)

    for idx, row in leaders.iterrows():

        print(
            f"{idx + 1:<6}"
            f"{str(row['selected_sector']):<20}"
            f"{str(row['symbol']):<12}"
            f"{str(row['stock_name']):<12}"
            f"{safe_float(row['sort_score']):<10.2f}"
            f"{str(row['source_type']):<8}"
            f"{safe_float(row['rank_change']):<10.0f}"
        )


# ============================================================
# 15. 主程序
# ============================================================

def main():

    print()
    print("=" * 100)
    print("四维共振 - Top5 板块龙头选股")
    print("=" * 100)

    print(
        f"交易日期: {TODAY}"
    )

    print()
    print(
        "规则："
        "stk_sector_scores 独立 Top5 + "
        "stk_sector_breadths 独立 Top5"
    )

    print(
        "规则：每个板块只寻找一个四维共振龙头"
    )

    print(
        "规则：不合并两个表的 rank_change"
    )

    # ========================================================
    # Step 1
    # scores Top5
    # ========================================================

    df_score_top5 = get_top5_from_scores(
        TODAY
    )

    # ========================================================
    # Step 2
    # breadths Top5
    # ========================================================

    df_breadth_top5 = get_top5_from_breadths(
        TODAY
    )

    # ========================================================
    # Step 3
    # 两组 Top5 去重板块
    # ========================================================

    selected_sectors = build_selected_sectors(
        df_score_top5,
        df_breadth_top5
    )

    if selected_sectors.empty:

        print()
        print(
            "[STOP] 今天没有找到任何 Top5 板块"
        )

        return

    print()
    print("=" * 80)
    print(
        f"最终需要寻找龙头的板块数量："
        f"{len(selected_sectors)}"
    )
    print("=" * 80)

    # ========================================================
    # Step 4
    # 获取所有目标板块股票
    # ========================================================

    sectors = selected_sectors[
        "sector_name"
    ].tolist()

    stock_mapping = get_sector_stocks(
        sectors
    )

    if stock_mapping.empty:

        print()
        print(
            "[STOP] Top5 板块没有找到股票"
        )

        return

    print()
    print(
        f"目标板块股票数量："
        f"{len(stock_mapping)}"
    )

    # ========================================================
    # Step 5
    # 获取股票数据
    # ========================================================

    symbols = stock_mapping[
        "symbol"
    ].dropna().unique().tolist()

    print()
    print(
        f"开始加载 {len(symbols)} 只股票的四维数据..."
    )

    df_fac = get_stock_factors(
        symbols,
        TODAY
    )

    df_k = get_stock_kline(
        symbols,
        TODAY
    )

    df_fund = get_stock_fund_flow(
        symbols,
        TODAY
    )

    df_chip = get_stock_chip(
        symbols,
        TODAY
    )

    # ========================================================
    # Step 6
    # 合并股票数据
    # ========================================================

    df_all = stock_mapping.copy()

    if not df_fac.empty:

        df_all = df_all.merge(
            df_fac,
            on="symbol",
            how="left"
        )

    if not df_k.empty:

        df_all = df_all.merge(
            df_k,
            on="symbol",
            how="left"
        )

    if not df_fund.empty:

        df_all = df_all.merge(
            df_fund,
            on="symbol",
            how="left"
        )

    if not df_chip.empty:

        df_all = df_all.merge(
            df_chip,
            on="symbol",
            how="left"
        )

    # ========================================================
    # Step 7
    # 数值字段处理
    # ========================================================

    numeric_columns = [
        "f_mom_20",
        "f_macd_dif",
        "f_macd_dea",
        "f_macd_hist",
        "f_bb_m",
        "f_quantity_ratio",
        "f_dist_high",

        "open",
        "close",
        "turnover_rate",

        "main_net_inflow",
        "main_net_ratio",
        "inflow_3d",
        "buy_power_ratio",
        "attack_score",
        "capital_score",
        "volume_power_ratio",

        "profit_ratio",
        "chip_score"
    ]

    for col in numeric_columns:

        if col not in df_all.columns:
            df_all[col] = 0.0

        df_all[col] = pd.to_numeric(
            df_all[col],
            errors="coerce"
        ).fillna(0.0)

    # ========================================================
    # 原逻辑需要 sector_max_inflow_rate
    #
    # 如果原查询没有该字段，默认 0
    #
    # 不改变原有 calc_4d_score 结构
    # ========================================================

    if "sector_max_inflow_rate" not in df_all.columns:

        df_all["sector_max_inflow_rate"] = 0.0

    # ========================================================
    # Step 8
    # 每个板块独立找龙头
    # ========================================================

    leaders = select_sector_leaders(
        df_all,
        selected_sectors
    )

    # ========================================================
    # Step 9
    # 保存
    # ========================================================

    save_to_stock_pool(
        leaders,
        TODAY
    )

    # ========================================================
    # Step 10
    # 打印
    # ========================================================

    print_final_result(
        selected_sectors,
        leaders
    )

    print()
    print("=" * 100)
    print("四维共振选股完成")
    print("=" * 100)


# ============================================================
# 程序入口
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        print()
        print("=" * 100)
        print("[ERROR] 程序执行失败")
        print("=" * 100)

        print(
            f"{type(e).__name__}: {e}"
        )

        raise