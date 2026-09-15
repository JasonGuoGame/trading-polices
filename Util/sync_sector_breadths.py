import pandas as pd
from sqlalchemy import create_engine, text, bindparam
import datetime
import sys
import numpy as np


# ============================================================
# 1. 引入全局配置
# ============================================================

sys.path.append(r"C:\ws\trading-polices\config")

try:
    import config

except ImportError:

    class DummyConfig:
        SECTOR_BLACKLIST = []

    config = DummyConfig()


# ============================================================
# 2. 数据库配置
# ============================================================

engine_quant = create_engine(
    'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
)

engine_review = create_engine(
    'mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review'
)


# ============================================================
# 3. 指数黑名单
# ============================================================

INDEX_LIST = [
    '000001.SH',
    '399001.SZ',
    '399006.SZ',
    '000300.SH',
    '000852.SH'
]


# ============================================================
# 4. 获取最近两个交易日
# ============================================================

def get_latest_dates():

    with engine_quant.connect() as conn:

        query = text("""
            SELECT DISTINCT trade_date
            FROM stk_daily_kline
            ORDER BY trade_date DESC
            LIMIT 2
        """)

        res = conn.execute(query).fetchall()

        return [
            row[0]
            for row in res
        ]


# ============================================================
# 5. 获取过去 N 个交易日进入前15名的次数
#
# 注意：
# 现在 stk_sector_breadths 已经存在多个盘中快照。
#
# 所以这里必须：
#
# COUNT(DISTINCT trade_date)
#
# 不能再 COUNT(*)。
#
# 否则一个板块一天有10个快照，
# 会被错误算成进入前15名10次。
# ============================================================

def get_past_15_counts(today, days=6):

    with engine_review.connect() as conn:

        date_query = text("""
            SELECT DISTINCT trade_date
            FROM stk_sector_breadths
            WHERE trade_date < :t
            ORDER BY trade_date DESC
            LIMIT :d
        """)

        past_dates = [
            r[0]
            for r in conn.execute(
                date_query,
                {
                    "t": today,
                    "d": days
                }
            ).fetchall()
        ]

        if not past_dates:
            return {}

        # SQLAlchemy expanding 参数
        count_query = text("""
            SELECT
                sector_name,
                COUNT(DISTINCT trade_date) AS cnt
            FROM stk_sector_breadths
            WHERE trade_date IN :dates
              AND rank_pos <= 15
              AND sector_type = 'industry'
            GROUP BY sector_name
        """).bindparams(
            bindparam(
                "dates",
                expanding=True
            )
        )

        res = conn.execute(
            count_query,
            {
                "dates": past_dates
            }
        ).fetchall()

        return {
            r[0]: int(r[1])
            for r in res
        }


# ============================================================
# 6. 主同步函数
# ============================================================

def sync_sector_data():

    # --------------------------------------------------------
    # 获取最近两个交易日
    # --------------------------------------------------------

    dates = get_latest_dates()

    if len(dates) < 2:

        print("数据不足。")

        return

    today, yesterday = dates[0], dates[1]

    print(
        f"正在分析日期: {today} "
        f"(对比基准: {yesterday})"
    )


    # ========================================================
    # ⭐ 核心：
    # 整个本次运行只生成一个 snapshot_time
    #
    # 所有板块：
    #
    # snapshot_time = 完全相同
    #
    # 精确到秒。
    # ========================================================

    snapshot_time = datetime.datetime.now().replace(
        microsecond=0
    )

    print(
        f"当前盘中快照时间: {snapshot_time}"
    )


    # ========================================================
    # 1. 获取个股价格数据
    # ========================================================

    idx_str = "','".join(INDEX_LIST)

    sql_kline = f"""

        SELECT
            symbol,
            trade_date,
            close

        FROM stk_daily_kline

        WHERE trade_date IN (
            '{today}',
            '{yesterday}'
        )

        AND symbol NOT IN ('{idx_str}')

    """

    df_all = pd.read_sql(
        sql_kline,
        engine_quant
    )

    if df_all.empty:

        print("没有获取到个股行情数据。")

        return


    df_all = df_all.drop_duplicates(
        subset=[
            'symbol',
            'trade_date'
        ],
        keep='last'
    )


    # ========================================================
    # 2. 计算涨跌
    # ========================================================

    try:

        df_pivot = (
            df_all
            .pivot(
                index='symbol',
                columns='trade_date',
                values='close'
            )
            .dropna()
        )

    except Exception as e:

        print(
            f"数据透视失败: {e}"
        )

        return


    pct_change = (
        (
            df_pivot[today]
            - df_pivot[yesterday]
        )
        / df_pivot[yesterday]
        * 100
    )


    is_up_map = (
        pct_change > 0
    ).astype(int)


    # ========================================================
    # 3. 计算个股新高
    # ========================================================

    print(
        "正在计算个股新高状态..."
    )


    start_history = (
        pd.to_datetime(today)
        - pd.Timedelta(days=400)
    ).strftime('%Y-%m-%d')


    sql_history = f"""

        SELECT
            symbol,
            trade_date,
            high,
            close

        FROM stk_daily_kline

        WHERE trade_date >= '{start_history}'
          AND trade_date <= '{today}'

          AND symbol NOT IN ('{idx_str}')

    """


    df_hist = pd.read_sql(
        sql_history,
        engine_quant
    )


    if df_hist.empty:

        print("没有获取到历史行情数据。")

        return


    df_hist = df_hist.sort_values(
        [
            'symbol',
            'trade_date'
        ]
    )


    # --------------------------------------------------------
    # 计算滚动最高价
    # 不包含当日
    # --------------------------------------------------------

    df_hist['max20'] = (
        df_hist
        .groupby('symbol')['high']
        .transform(
            lambda x:
                x.shift(1)
                .rolling(20)
                .max()
        )
    )


    df_hist['max60'] = (
        df_hist
        .groupby('symbol')['high']
        .transform(
            lambda x:
                x.shift(1)
                .rolling(60)
                .max()
        )
    )


    df_hist['max250'] = (
        df_hist
        .groupby('symbol')['high']
        .transform(
            lambda x:
                x.shift(1)
                .rolling(250)
                .max()
        )
    )


    # --------------------------------------------------------
    # 判定今天是否创新高
    # --------------------------------------------------------

    df_high_today = df_hist[
        df_hist['trade_date'] == today
    ].copy()


    df_high_today['h20'] = (
        df_high_today['close']
        > df_high_today['max20']
    ).astype(int)


    df_high_today['h60'] = (
        df_high_today['close']
        > df_high_today['max60']
    ).astype(int)


    df_high_today['h250'] = (
        df_high_today['close']
        > df_high_today['max250']
    ).astype(int)


    h20_map = (
        df_high_today
        .set_index('symbol')['h20']
    )


    h60_map = (
        df_high_today
        .set_index('symbol')['h60']
    )


    h250_map = (
        df_high_today
        .set_index('symbol')['h250']
    )


    # ========================================================
    # 4. 获取板块映射
    # ========================================================

    query_sectors = """

        SELECT
            symbol,
            sector_name

        FROM stock_sector_relation

        WHERE sector_name LIKE '行业-%%'

    """


    df_rel = pd.read_sql(
        query_sectors,
        engine_quant
    )


    # --------------------------------------------------------
    # 板块黑名单
    # --------------------------------------------------------

    if (
        hasattr(config, 'SECTOR_BLACKLIST')
        and config.SECTOR_BLACKLIST
    ):

        mask = (
            df_rel['sector_name']
            .str.replace(
                '行业-',
                '',
                regex=False
            )
            .isin(
                config.SECTOR_BLACKLIST
            )
        )

        df_rel = df_rel[~mask]


    # ========================================================
    # 5. 获取历史前15数据
    # ========================================================

    history_counts = get_past_15_counts(
        today,
        6
    )


    final_records = []


    # ========================================================
    # ⭐ 不再单独生成 now_time
    #
    # created_at 和 snapshot_time 都使用
    # 同一个 snapshot_time。
    # ========================================================

    now_time = snapshot_time


    # ========================================================
    # 6. 获取今天上一份盘中快照
    #
    # 用于计算：
    #
    # rank_change =
    # previous_rank - current_rank
    #
    # 正数 = 排名上升
    # 负数 = 排名下降
    # ========================================================

    previous_rank_map = {}


    with engine_review.connect() as conn:

        previous_snapshot_sql = text("""
            SELECT
                sector_name,
                rank_pos

            FROM stk_sector_breadths

            WHERE trade_date = :trade_date

              AND sector_type = 'industry'

              AND snapshot_time IS NOT NULL

              AND snapshot_time = (

                  SELECT MAX(snapshot_time)

                  FROM stk_sector_breadths

                  WHERE trade_date = :trade_date

                    AND sector_type = 'industry'

                    AND snapshot_time IS NOT NULL
              )
        """)


        previous_rows = conn.execute(
            previous_snapshot_sql,
            {
                "trade_date": today
            }
        ).fetchall()


        previous_rank_map = {
            row[0]: int(row[1])
            for row in previous_rows
        }


    # ========================================================
    # A. 宽基计算
    # ========================================================

    broad_groups = {

        '沪指主板': '60',

        '深指主板': '00',

        '创业板': '30',

        '科创板': '68'
    }


    for b_name, prefix in broad_groups.items():

        subset_symbols = (
            pct_change
            .index[
                pct_change.index.str.startswith(
                    prefix
                )
            ]
        )


        if not subset_symbols.empty:

            adv = (
                is_up_map
                .reindex(subset_symbols)
                .sum()
            )


            total = len(
                subset_symbols
            )


            # ------------------------------------------------
            # 新高统计
            # ------------------------------------------------

            h20_sum = (
                h20_map
                .reindex(subset_symbols)
                .sum()
            )


            h60_sum = (
                h60_map
                .reindex(subset_symbols)
                .sum()
            )


            h250_sum = (
                h250_map
                .reindex(subset_symbols)
                .sum()
            )


            final_records.append({

                'trade_date':
                    today,

                'sector_name':
                    b_name,

                'sector_type':
                    'broad',

                'red_rate':
                    round(
                        adv / total * 100,
                        2
                    ),

                'advancers':
                    int(adv),

                'total_stocks':
                    int(total),

                'rank_pos':
                    0,

                # ⭐ 和 snapshot_time 完全一致
                'created_at':
                    snapshot_time,

                'persistence_7d':
                    0,

                'is_leader':
                    0,

                'high_20d_count':
                    int(h20_sum),

                'high_60d_count':
                    int(h60_sum),

                'high_250d_count':
                    int(h250_sum),

                # ⭐ 本次运行统一快照时间
                'snapshot_time':
                    snapshot_time,

                # 宽基没有行业排名
                'rank_change':
                    0
            })


    # ========================================================
    # B. 行业细分计算
    # ========================================================

    df_rel['is_up'] = (
        df_rel['symbol']
        .map(is_up_map)
    )


    df_rel['h20'] = (
        df_rel['symbol']
        .map(h20_map)
    )


    df_rel['h60'] = (
        df_rel['symbol']
        .map(h60_map)
    )


    df_rel['h250'] = (
        df_rel['symbol']
        .map(h250_map)
    )


    df_rel = df_rel.dropna(
        subset=['is_up']
    )


    # ========================================================
    # 聚合统计
    # ========================================================

    ind_stats = (
        df_rel
        .groupby('sector_name')
        .agg({
            'is_up': ['sum', 'count'],
            'h20': 'sum',
            'h60': 'sum',
            'h250': 'sum'
        })
        .reset_index()
    )


    ind_stats.columns = [

        'sector_name',

        'advancers',

        'total_stocks',

        'high_20d_count',

        'high_60d_count',

        'high_250d_count'
    ]


    # --------------------------------------------------------
    # 至少13只成分股
    # --------------------------------------------------------

    ind_stats = ind_stats[
        ind_stats['total_stocks'] >= 13
    ].copy()


    # ========================================================
    # 红盘率
    # ========================================================

    ind_stats['red_rate'] = (
        ind_stats['advancers']
        / ind_stats['total_stocks']
        * 100
    ).round(2)


    # ========================================================
    # 按红盘率排名
    # ========================================================

    ind_stats = (
        ind_stats
        .sort_values(
            'red_rate',
            ascending=False
        )
        .reset_index(drop=True)
    )


    ind_stats['rank_pos'] = (
        ind_stats.index + 1
    )


    # ========================================================
    # 生成行业记录
    # ========================================================

    for _, row in ind_stats.iterrows():

        clean_name = (
            row['sector_name']
            .replace(
                '行业-',
                ''
            )
        )


        # ----------------------------------------------------
        # 历史进入前15次数
        # ----------------------------------------------------

        p_count = history_counts.get(
            clean_name,
            0
        )


        # ----------------------------------------------------
        # 今天进入前15
        # ----------------------------------------------------

        if row['rank_pos'] <= 15:

            p_count += 1


        # ----------------------------------------------------
        # 是否领头羊
        # ----------------------------------------------------

        is_leader = (
            1
            if p_count >= 3
            else 0
        )


        # ====================================================
        # rank_change
        #
        # 上一次快照排名
        # -
        # 本次排名
        #
        # 正数 = 排名上升
        # ====================================================

        current_rank = int(
            row['rank_pos']
        )


        previous_rank = (
            previous_rank_map
            .get(clean_name)
        )


        if previous_rank is None:

            # 今天第一次运行
            rank_change = 0

        else:

            rank_change = (
                int(previous_rank)
                - current_rank
            )


        # ====================================================
        # 保存记录
        # ====================================================

        final_records.append({

            'trade_date':
                today,

            'sector_name':
                clean_name,

            'sector_type':
                'industry',

            'red_rate':
                row['red_rate'],

            'advancers':
                int(row['advancers']),

            'total_stocks':
                int(row['total_stocks']),

            'rank_pos':
                current_rank,

            # ⭐ 和 snapshot_time 完全一致
            'created_at':
                snapshot_time,

            'persistence_7d':
                int(p_count),

            'is_leader':
                int(is_leader),

            'high_20d_count':
                int(row['high_20d_count']),

            'high_60d_count':
                int(row['high_60d_count']),

            'high_250d_count':
                int(row['high_250d_count']),

            # ⭐ 本次运行统一快照时间
            'snapshot_time':
                snapshot_time,

            'rank_change':
                int(rank_change)
        })


    # ========================================================
    # 7. 没有数据
    # ========================================================

    if not final_records:

        print("没有生成任何板块数据。")

        return


    df_save = pd.DataFrame(
        final_records
    )


    # ========================================================
    # 8. 数据写入
    #
    # 注意：
    #
    # 唯一键现在应该是：
    #
    # trade_date
    # sector_name
    # snapshot_time
    #
    # 因此不同快照不会互相覆盖。
    # ========================================================

    upsert_sql = text("""

        INSERT INTO stk_sector_breadths (

            trade_date,
            sector_name,
            sector_type,
            red_rate,

            advancers,
            total_stocks,
            rank_pos,
            created_at,

            persistence_7d,
            is_leader,

            high_20d_count,
            high_60d_count,
            high_250d_count,

            snapshot_time,
            rank_change

        )

        VALUES (

            :trade_date,
            :sector_name,
            :sector_type,
            :red_rate,

            :advancers,
            :total_stocks,
            :rank_pos,
            :created_at,

            :persistence_7d,
            :is_leader,

            :high_20d_count,
            :high_60d_count,
            :high_250d_count,

            :snapshot_time,
            :rank_change

        )

        ON DUPLICATE KEY UPDATE

            red_rate =
                VALUES(red_rate),

            advancers =
                VALUES(advancers),

            total_stocks =
                VALUES(total_stocks),

            rank_pos =
                VALUES(rank_pos),

            persistence_7d =
                VALUES(persistence_7d),

            is_leader =
                VALUES(is_leader),

            high_20d_count =
                VALUES(high_20d_count),

            high_60d_count =
                VALUES(high_60d_count),

            created_at =
                VALUES(created_at),

            rank_change =
                VALUES(rank_change)

    """)


    # ========================================================
    # 9. 批量写入
    # ========================================================

    with engine_review.begin() as conn:

        conn.execute(
            upsert_sql,
            df_save.to_dict(
                orient='records'
            )
        )


    # ========================================================
    # 10. 输出统计
    # ========================================================

    leader_count = len(
        df_save[
            df_save['is_leader'] == 1
        ]
    )


    industry_count = len(
        df_save[
            df_save['sector_type'] == 'industry'
        ]
    )


    broad_count = len(
        df_save[
            df_save['sector_type'] == 'broad'
        ]
    )


    print(
        f"\n✅ 同步完成！"
    )

    print(
        f"快照时间: {snapshot_time}"
    )

    print(
        f"行业板块: {industry_count}"
    )

    print(
        f"宽基板块: {broad_count}"
    )

    print(
        f"领头羊板块: {leader_count}"
    )


    # ========================================================
    # 11. 输出当前排名上升最快的行业
    # ========================================================

    fastest_rising = (

        df_save[
            (df_save['sector_type'] == 'industry')
            &
            (df_save['rank_change'] > 0)
        ]

        .sort_values(
            'rank_change',
            ascending=False
        )

        .head(10)
    )


    if not fastest_rising.empty:

        print(
            "\n🚀 当前排名上升最快的板块："
        )


        for _, row in fastest_rising.iterrows():

            print(

                f"{row['sector_name']:<20}"

                f" 当前排名: "
                f"{int(row['rank_pos']):>3}"

                f" 上升: "
                f"+{int(row['rank_change']):>3}"
            )

    else:

        print(
            "\n当前没有排名上升的行业板块。"
        )


# ============================================================
# 12. 程序入口
# ============================================================

if __name__ == "__main__":

    sync_sector_data()