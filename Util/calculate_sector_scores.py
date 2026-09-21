import json
import pandas as pd
import pandas_ta as ta
from sqlalchemy import create_engine, text, bindparam
import datetime
import numpy as np


# =========================================================
# Database configuration
# =========================================================

engine_quant = create_engine(
    'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
)

engine_review = create_engine(
    'mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review'
)


# =========================================================
# Limit-up threshold
# =========================================================

def get_limit_threshold(symbol):

    if symbol.startswith(('30', '68')):
        return 19.8

    return 9.8


# =========================================================
# Main
# =========================================================

def calculate_sector_scores_v4():

    # ---------------------------------------------------------
    # 0. Snapshot time
    # ---------------------------------------------------------

    snapshot_time = datetime.datetime.now()

    # ---------------------------------------------------------
    # 1. Automatically get the latest three trading days
    # ---------------------------------------------------------

    with engine_quant.connect() as conn:

        res = conn.execute(
            text("""
                SELECT DISTINCT trade_date
                FROM stk_daily_kline
                ORDER BY trade_date DESC
                LIMIT 3
            """)
        ).fetchall()

        if len(res) < 3:

            print(
                "❌ Historical data is insufficient."
            )

            return

        today, yesterday = res[0][0], res[1][0]

    print(
        f"🚀 Starting sector scoring system v4 "
        f"(with new high details) | "
        f"Target date: {today} | "
        f"Snapshot: {snapshot_time}"
    )

    # =========================================================
    # Step A
    # Get official sector list and fund flow data
    # =========================================================

    flow_sql = f"""
        SELECT
            sector_name,
            net_inflow_amount,
            net_inflow_rate
        FROM stk_sector_fund_flow
        WHERE trade_date = '{today}'
    """

    df_flow = pd.read_sql(
        flow_sql,
        engine_quant
    ).set_index('sector_name')

    if df_flow.empty:

        print(
            f"❌ Logic stopped: "
            f"fund flow data for {today} has not been generated."
        )

        return

    official_sector_list = (
        df_flow.index.tolist()
    )

    # =========================================================
    # Step B
    # Get stock data
    # =========================================================

    kline_sql = text("""
        SELECT
            t.symbol,
            s.name AS stock_name,
            t.close,
            t.high,
            t.amount,
            y.close AS prev_close,
            r.sector_name AS raw_db_sector_name

        FROM stk_daily_kline t

        JOIN stocks s
            ON t.symbol = s.symbol

        JOIN stk_daily_kline y
            ON t.symbol = y.symbol
            AND y.trade_date = :y_date

        JOIN stock_sector_relation r
            ON t.symbol = r.symbol

        WHERE t.trade_date = :t_date
    """)

    with engine_quant.connect() as conn:

        df_k_raw = pd.read_sql(
            kline_sql,
            conn,
            params={
                "t_date": today,
                "y_date": yesterday
            }
        )

    # ---------------------------------------------------------
    # Map database sector name
    # to official sector name
    # ---------------------------------------------------------

    def map_to_official(db_name):
        # 统一清洗两边的名称（去除“行业-”、“概念-”、“概念”、“行业”等后缀/前缀）
        clean_name = (
            db_name.replace("行业-", "")
            .replace("概念-", "")
            .replace("Ⅱ", "")
            .replace("Ⅲ", "")
            .replace("概念", "")
            .replace("行业", "")
            .strip()
        )

        if not clean_name:
            return None

        # 优先精确/双向包含校验
        for off_name in official_sector_list:
            clean_off_name = off_name.replace("概念", "").replace("行业", "").strip()

            # 如果清洗后的名称一致，或者存在相互包含关系
            if (
                clean_name == clean_off_name
                or clean_name in off_name
                or off_name in clean_name
            ):
                return off_name  # 返回资金流表中对应的官方板块名称 (如 'MLCC概念')

        return None

    df_k_raw['official_name'] = (
        df_k_raw['raw_db_sector_name']
        .apply(map_to_official)
    )

    df_k = df_k_raw.dropna(
        subset=['official_name']
    ).copy()

    if df_k.empty:

        return

    # =========================================================
    # Step B.2
    # Calculate new high status
    # =========================================================

    print(
        "📊 Calculating market-wide stock new high status..."
    )

    high_start_date = (
        pd.to_datetime(today)
        - pd.Timedelta(days=400)
    ).strftime('%Y-%m-%d')

    sql_hist = text("""
        SELECT
            symbol,
            trade_date,
            high,
            close
        FROM stk_daily_kline
        WHERE trade_date >= :sd
          AND trade_date <= :td
    """)

    df_hist = pd.read_sql(
        sql_hist,
        engine_quant,
        params={
            "sd": high_start_date,
            "td": today
        }
    )

    df_hist = df_hist.sort_values(
        ['symbol', 'trade_date']
    )

    # ---------------------------------------------------------
    # 20D high
    # ---------------------------------------------------------

    df_hist['max_20'] = (
        df_hist
        .groupby('symbol')['high']
        .transform(
            lambda x:
                x.shift(1)
                .rolling(20)
                .max()
        )
    )

    # ---------------------------------------------------------
    # 60D high
    # ---------------------------------------------------------

    df_hist['max_60'] = (
        df_hist
        .groupby('symbol')['high']
        .transform(
            lambda x:
                x.shift(1)
                .rolling(60)
                .max()
        )
    )

    # ---------------------------------------------------------
    # 250D high
    # ---------------------------------------------------------

    df_hist['max_250'] = (
        df_hist
        .groupby('symbol')['high']
        .transform(
            lambda x:
                x.shift(1)
                .rolling(250)
                .max()
        )
    )

    df_high_today = df_hist[
        df_hist['trade_date'] == today
    ].copy()

    df_high_today['is_h20'] = (
        df_high_today['close']
        >
        df_high_today['max_20']
    ).astype(int)

    df_high_today['is_h60'] = (
        df_high_today['close']
        >
        df_high_today['max_60']
    ).astype(int)

    df_high_today['is_h250'] = (
        df_high_today['close']
        >
        df_high_today['max_250']
    ).astype(int)

    # ---------------------------------------------------------
    # Merge new-high information
    # ---------------------------------------------------------

    df_k = df_k.merge(
        df_high_today[
            [
                'symbol',
                'is_h20',
                'is_h60',
                'is_h250'
            ]
        ],
        on='symbol',
        how='left'
    ).fillna(0)

    # =========================================================
    # Calculate change percentage
    # =========================================================

    df_k['chg_pct'] = (
        df_k['close']
        /
        df_k['prev_close']
        - 1
    ) * 100

    # =========================================================
    # Calculate limit-up status
    # =========================================================

    df_k['is_limit'] = df_k.apply(
        lambda r:

            r['close']
            >=
            round(
                r['prev_close']
                *
                (
                    1
                    +
                    get_limit_threshold(
                        r['symbol']
                    ) / 100
                ),
                2
            ),

        axis=1
    )

    # =========================================================
    # Calculate hit-limit status
    # =========================================================

    df_k['hit_limit'] = df_k.apply(
        lambda r:

            r['high']
            >=
            round(
                r['prev_close']
                *
                (
                    1
                    +
                    get_limit_threshold(
                        r['symbol']
                    ) / 100
                ),
                2
            ),

        axis=1
    )

    # =========================================================
    # Step C
    # Prepare auxiliary data
    # =========================================================

    attack_symbols = pd.read_sql(
        f"""
        SELECT symbol
        FROM stk_market_attack_log
        WHERE trade_date = '{today}'
        """,
        engine_quant
    )['symbol'].unique()

    # ---------------------------------------------------------
    # IMPORTANT FIX
    #
    # Yesterday may now have multiple intraday snapshots.
    #
    # We MUST use the latest snapshot of yesterday.
    #
    # Otherwise sector_name becomes a duplicated pandas index,
    # and:
    #
    # df_prev_scores.loc[name, 'rank_pos']
    #
    # may return a Series instead of a scalar.
    # ---------------------------------------------------------

    previous_day_sql = text("""
        SELECT
            sector_name,
            rank_pos
        FROM stk_sector_scores
        WHERE trade_date = :yesterday
          AND snapshot_time = (
              SELECT MAX(snapshot_time)
              FROM stk_sector_scores
              WHERE trade_date = :yesterday
                AND snapshot_time IS NOT NULL
          )
    """)

    df_prev_scores = pd.read_sql(
        previous_day_sql,
        engine_review,
        params={
            "yesterday": yesterday
        }
    )

    if not df_prev_scores.empty:

        # Safety protection:
        # sector_name should be unique inside
        # the latest snapshot.

        df_prev_scores = (
            df_prev_scores
            .drop_duplicates(
                subset=['sector_name'],
                keep='last'
            )
            .set_index('sector_name')
        )

    else:

        df_prev_scores = (
            pd.DataFrame(
                columns=[
                    'sector_name',
                    'rank_pos'
                ]
            )
            .set_index('sector_name')
        )

    # =========================================================
    # Step D
    # Core scoring loop
    # =========================================================

    sector_results = []

    for name in official_sector_list:

        group = df_k[
            df_k['official_name'] == name
        ]

        if group.empty:

            continue

        # =====================================================
        # 1. Money score (30)
        # =====================================================

        f = df_flow.loc[name]

        m_score = (

            min(
                max(
                    float(
                        f['net_inflow_amount']
                    )
                    * 1.5,
                    0
                ),
                15
            )

            +

            min(
                max(
                    float(
                        f['net_inflow_rate']
                    )
                    * 2,
                    0
                ),
                10
            )

            +

            (
                5
                if
                float(
                    f['net_inflow_amount']
                ) > 0
                else 0
            )
        )

        # =====================================================
        # 2. Profit effect score (30)
        # =====================================================

        up_rate = (
            (group['chg_pct'] > 0).sum()
            /
            len(group)
        )

        limit_count = (
            group['is_limit'].sum()
        )

        hit_limit_count = (
            group['hit_limit'].sum()
        )

        broken_rate = (

            (
                hit_limit_count
                -
                limit_count
            )
            /
            hit_limit_count

            if hit_limit_count > 0

            else 0
        )

        profit_s = (

            (up_rate * 20)

            +

            min(
                limit_count * 2,
                10
            )

            +

            max(
                5 * (1 - broken_rate),
                0
            )
        )

        # =====================================================
        # 3. Leader strength (20)
        # =====================================================

        unique_group = (
            group
            .drop_duplicates(
                subset=['symbol']
            )
        )

        leaders = (
            unique_group
            .sort_values(
                'amount',
                ascending=False
            )
            .head(5)
        )

        leader_pct = (
            leaders['chg_pct'].mean()
        )

        if leader_pct >= 5:

            core_score = 5

        elif leader_pct >= 3:

            core_score = 5

        elif leader_pct >= 0:

            core_score = 3

        else:

            core_score = 0

        l_count = (
            unique_group['is_limit'].sum()
        )

        if l_count >= 20:

            limit_score = 10

        elif l_count >= 10:

            limit_score = 6

        else:

            limit_score = 0

        top_amount = (
            leaders['amount'].sum()
        )

        sector_amount = (
            unique_group['amount'].sum()
        )

        amount_ratio = (

            top_amount
            /
            sector_amount

            if sector_amount > 0

            else 0
        )

        if amount_ratio >= 0.3:

            amount_score = 5

        elif amount_ratio >= 0.15:

            amount_score = 3

        else:

            amount_score = 1

        leader_s = (
            core_score
            +
            limit_score
            +
            amount_score
        )

        # =====================================================
        # 4. Attack score (10)
        # =====================================================

        attack_s = min(
            unique_group['symbol']
            .isin(attack_symbols)
            .sum(),
            10
        )

        # =====================================================
        # 5. Continuity base score (10)
        # =====================================================

        cont_s = 5

        if name in df_prev_scores.index:

            p_rank = df_prev_scores.loc[
                name,
                'rank_pos'
            ]

            # -------------------------------------------------
            # Safety:
            # p_rank should be scalar because we already
            # selected the latest snapshot and removed duplicates.
            # -------------------------------------------------

            p_rank = int(p_rank)

            if p_rank == 1:

                cont_s = 10

            elif p_rank <= 3:

                cont_s = 8

            else:

                cont_s = 6

        # =====================================================
        # New high counts
        # =====================================================

        h20_c = int(
            unique_group['is_h20'].sum()
        )

        h60_c = int(
            unique_group['is_h60'].sum()
        )

        h250_c = int(
            unique_group['is_h250'].sum()
        )

        # =====================================================
        # Save sector result
        # =====================================================

        sector_results.append({

            'trade_date': today,

            'sector_name': name,

            'money_score': m_score,

            'profit_score': profit_s,

            'leader_score': leader_s,

            'attack_score': attack_s,

            'continuity_score': cont_s,

            'total_score':
                m_score
                +
                profit_s
                +
                leader_s
                +
                attack_s
                +
                cont_s,

            'high_20d_count': h20_c,

            'high_60d_count': h60_c,

            'high_250d_count': h250_c
        })

    # =========================================================
    # Step G
    # Save stock new high details
    # =========================================================

    print(
        "💾 Syncing stock new high details "
        "to stk_new_high_detail..."
    )

    df_high_detail = df_k[
        (df_k['is_h20'] == 1)
        |
        (df_k['is_h60'] == 1)
        |
        (df_k['is_h250'] == 1)
    ].copy()

    if not df_high_detail.empty:

        df_high_detail['trade_date'] = today

        df_save_detail = df_high_detail[
            [
                'trade_date',
                'symbol',
                'stock_name',
                'official_name',
                'is_h20',
                'is_h60',
                'is_h250',
                'close'
            ]
        ].rename(
            columns={
                'official_name': 'sector_name',
                'is_h20': 'high_20d',
                'is_h60': 'high_60d',
                'is_h250': 'high_250d'
            }
        )

        with engine_review.begin() as conn:

            df_save_detail.to_sql(
                'tmp_high_detail',
                conn,
                if_exists='replace',
                index=False
            )

            conn.execute(
                text("""
                    INSERT INTO stk_new_high_detail
                    (
                        trade_date,
                        symbol,
                        stock_name,
                        sector_name,
                        high_20d,
                        high_60d,
                        high_250d,
                        close,
                        created_at
                    )
                    SELECT
                        trade_date,
                        symbol,
                        stock_name,
                        sector_name,
                        high_20d,
                        high_60d,
                        high_250d,
                        close,
                        NOW()
                    FROM tmp_high_detail

                    ON DUPLICATE KEY UPDATE

                        high_20d =
                            VALUES(high_20d),

                        high_60d =
                            VALUES(high_60d),

                        high_250d =
                            VALUES(high_250d),

                        close =
                            VALUES(close)
                """)
            )

            conn.execute(
                text(
                    "DROP TABLE IF EXISTS tmp_high_detail"
                )
            )

    # =========================================================
    # Step E
    # Calculate leader sectors
    # =========================================================

    df_res = (
        pd.DataFrame(sector_results)
        .sort_values(
            'total_score',
            ascending=False
        )
    )

    df_res['rank_pos'] = range(
        1,
        len(df_res) + 1
    )

    # =========================================================
    # NEW
    # Get previous intraday snapshot for TODAY
    #
    # rank_change:
    #
    # previous_rank - current_rank
    #
    # Positive = ranking improved
    # Negative = ranking dropped
    # =========================================================

    previous_snapshot_sql = text("""
        SELECT
            sector_name,
            rank_pos
        FROM stk_sector_scores
        WHERE trade_date = :trade_date
          AND snapshot_time IS NOT NULL
          AND snapshot_time = (
              SELECT MAX(snapshot_time)
              FROM stk_sector_scores
              WHERE trade_date = :trade_date
                AND snapshot_time IS NOT NULL
          )
    """)

    with engine_review.connect() as conn:

        previous_rows = conn.execute(
            previous_snapshot_sql,
            {
                "trade_date": today
            }
        ).fetchall()

    previous_snapshot = {
        row[0]: int(row[1])
        for row in previous_rows
    }

    # =========================================================
    # Calculate rank change
    # =========================================================

    def calculate_rank_change(row):

        sector_name = (
            row['sector_name']
        )

        current_rank = int(
            row['rank_pos']
        )

        previous_rank = (
            previous_snapshot.get(
                sector_name
            )
        )

        # First snapshot of the day
        if previous_rank is None:

            return 0

        return int(
            previous_rank
            -
            current_rank
        )

    df_res['rank_change'] = (
        df_res
        .apply(
            calculate_rank_change,
            axis=1
        )
    )

    # =========================================================
    # Step E.1
    # Calculate persistence
    #
    # IMPORTANT:
    # Intraday snapshots must NOT inflate persistence.
    #
    # Only count distinct trade_date.
    # =========================================================

    with engine_review.connect() as conn:

        hist_dates_res = conn.execute(
            text("""
                SELECT DISTINCT trade_date
                FROM stk_sector_scores
                WHERE trade_date < :d
                ORDER BY trade_date DESC
                LIMIT 6
            """),
            {
                "d": today
            }
        ).fetchall()

        hist_dates = [
            r[0]
            for r in hist_dates_res
        ]

        if hist_dates:

            # -------------------------------------------------
            # SQLAlchemy expanding parameter
            # -------------------------------------------------

            hist_sql = text("""
                SELECT
                    sector_name,
                    COUNT(DISTINCT trade_date) AS cnt
                FROM stk_sector_scores
                WHERE trade_date IN :dates
                  AND rank_pos <= 15
                GROUP BY sector_name
            """).bindparams(
                bindparam(
                    "dates",
                    expanding=True
                )
            )

            hist_counts = (
                pd.read_sql(
                    hist_sql,
                    conn,
                    params={
                        "dates": hist_dates
                    }
                )
                .set_index(
                    'sector_name'
                )['cnt']
                .to_dict()
            )

        else:

            hist_counts = {}

    # =========================================================
    # Persistence
    # =========================================================

    def calc_persistence(row):

        past_cnt = (
            hist_counts.get(
                row['sector_name'],
                0
            )
        )

        current_cnt = (

            past_cnt

            +

            (
                1
                if row['rank_pos'] <= 15
                else 0
            )
        )

        return int(
            current_cnt
        )

    df_res['persistence_7d'] = (
        df_res
        .apply(
            calc_persistence,
            axis=1
        )
    )

    # =========================================================
    # Leader flag
    # =========================================================

    df_res['is_leader'] = (
        df_res['persistence_7d']
        .apply(
            lambda x:
                1
                if x >= 3
                else 0
        )
    )

    # =========================================================
    # Step F
    # Save sector scores
    # =========================================================

    save_sql = text("""
        INSERT INTO stk_sector_scores
        (
            trade_date,
            sector_name,
            money_score,
            profit_score,
            leader_score,
            attack_score,
            continuity_score,
            total_score,
            rank_pos,
            persistence_7d,
            is_leader,
            high_20d_count,
            high_60d_count,
            high_250d_count,
            snapshot_time,
            rank_change
        )

        VALUES
        (
            :trade_date,
            :sector_name,
            :money_score,
            :profit_score,
            :leader_score,
            :attack_score,
            :continuity_score,
            :total_score,
            :rank_pos,
            :persistence_7d,
            :is_leader,
            :high_20d_count,
            :high_60d_count,
            :high_250d_count,
            :snapshot_time,
            :rank_change
        )

        ON DUPLICATE KEY UPDATE

            total_score =
                VALUES(total_score),

            rank_pos =
                VALUES(rank_pos),

            persistence_7d =
                VALUES(persistence_7d),

            is_leader =
                VALUES(is_leader),

            money_score =
                VALUES(money_score),

            profit_score =
                VALUES(profit_score),

            leader_score =
                VALUES(leader_score),

            attack_score =
                VALUES(attack_score),

            continuity_score =
                VALUES(continuity_score),

            high_20d_count =
                VALUES(high_20d_count),

            high_60d_count =
                VALUES(high_60d_count),

            high_250d_count =
                VALUES(high_250d_count),

            snapshot_time =
                VALUES(snapshot_time),

            rank_change =
                VALUES(rank_change)
    """)

    with engine_review.begin() as conn:

        for _, row in df_res.iterrows():

            params = {
                **row.to_dict(),

                'snapshot_time':
                    snapshot_time
            }

            conn.execute(
                save_sql,
                params
            )

    # =========================================================
    # Print current leaders
    # =========================================================

    leaders = (
        df_res[
            df_res['is_leader'] == 1
        ]['sector_name']
        .tolist()
    )

    print(
        f"✅ Score, details and leaders synchronized! "
        f"Snapshot: {snapshot_time}"
    )

    print(
        f"Current leader sectors: "
        f"{', '.join(leaders) if leaders else 'None'}"
    )

    # =========================================================
    # NEW
    # Print fastest rising sectors
    # =========================================================

    fastest_rising = (
        df_res[
            df_res['rank_change'] > 0
        ]
        .sort_values(
            'rank_change',
            ascending=False
        )
        .head(10)
    )

    if not fastest_rising.empty:

        print(
            "\n🚀 Fastest Rising Sectors:"
        )

        for _, row in (
            fastest_rising.iterrows()
        ):

            print(
                f"{row['sector_name']:<20} "
                f"Rank: {int(row['rank_pos']):>3} "
                f"Change: +{int(row['rank_change']):>3}"
            )

    else:

        print(
            "\n🚀 Fastest Rising Sectors: "
            "No rising sectors in this snapshot."
        )


# =========================================================
# Main
# =========================================================

if __name__ == "__main__":

    calculate_sector_scores_v4()