import polars as pl
import polars.selectors as cs
import altair as alt
from IPython.display import display

def pivot_txs(df, group_by_cols): 
    sort_cols = ['txid', 'ln', 'mint', 'stability_pool', 'wallet']

    return (
        df
        .group_by(group_by_cols)
        .agg(
            pl.col('amount_msat').sum(),
            pl.col('txid').count().alias('n')
        )
        .sort(by=group_by_cols)
        .pivot(index='txid', on='kind', values='amount_msat')
        .select(pl.col(sort_cols))
    )


def check_for_orphaned_txids(tx_type, left_df, right_df):
    if tx_type == 'input':
        suffix = '_out'
    elif tx_type == 'output':
        suffix = '_in'
    else:
        print("Argument needs to be either 'input' or 'output'")
        return

    left_cols = ['ln', 'mint', 'stability_pool', 'wallet']
    right_cols = [f'{col}{suffix}' for col in left_cols]

    n_unique_txids = left_df.shape[0]
    n_non_orphaned_txids = left_df.join(right_df, on='txid', suffix=suffix, how='inner').shape[0]

    orphaned_txids = (
        left_df.join(right_df, on='txid', suffix=suffix, how='left')
        .filter(
            pl.all_horizontal(pl.col(right_cols).is_null())
        )
        .select(['txid'] + left_cols)
    )

    print(f"# unique {tx_type} txid's                       : {n_unique_txids:>8,}")
    print(f"# unique {tx_type} txid's w/ associated outputs : {n_non_orphaned_txids:>8,}")
    print(f"# orphaned {tx_type} txid's                     : {n_unique_txids - n_non_orphaned_txids:>8,}")
    print(f"\n# of orphaned {tx_type} txid's:")
    display(orphaned_txids.count()) 
    print(f"\n# of msats in orphaned {tx_type} txid's:")
    display(orphaned_txids.sum())
    
    return orphaned_txids.sort(by='txid')


def make_transitions(p_combined):
    melted = (
        p_combined
        .unpivot(on=cs.numeric(), index='txid', variable_name='kind', value_name='amount_msat')
        .drop_nulls()
        .sort(by='txid')
    )

    return (
        melted
        .with_columns(
            pl.when(~pl.col('kind').str.contains('_out'))
            .then(pl.col('kind').alias('in')),
            pl.when(~pl.col('kind').str.contains('_out'))
            .then(pl.col('amount_msat').alias('in_msat')),
            pl.when(pl.col('kind').str.contains('_out'))
            .then(pl.col('kind').alias('out')),
            pl.when(pl.col('kind').str.contains('_out'))
            .then(pl.col('amount_msat').alias('out_msat')),
        )
        .with_columns(
            pl.col('out').str.replace('_out', '')
        )
        .group_by('txid').agg(
            pl.col('in').drop_nulls(),
            pl.col('out').drop_nulls(),
            pl.col('in_msat').sum(),
            pl.col('out_msat').sum(),
        )
        .group_by(['in', 'out']).agg(
            pl.col('txid').count().alias('n'),
            pl.col('in_msat').sum(),
            pl.col('out_msat').sum()
        )
        .with_columns(
            pl.col('in').list.join(' + '),
            pl.col('out').list.join(' + '),
            (pl.col('in_msat') - pl.col('out_msat')).alias('diff')
        )
        .sort(by=['in', 'out'])
    )


def make_tx_stats_chart(tx_type, df, txs, session_times, omit_outlier=False):

    if tx_type == 'input':
        title_label = 'Input'
    else:
        title_label = 'Output'

    tx_session_times = (
        txs
        .join(session_times, on='session_index')
        .with_columns(
            pl.from_epoch(pl.col('estimated_session_timestamp'), time_unit='s').alias('timestamp')
        )
        .select(['txid', 'session_index', 'timestamp'])
        .sort(by='timestamp')
    )
    
    tx_kinds = (
        df
        .unpivot(on=cs.numeric(), index='txid', variable_name='kind', value_name='msats')
        .join(tx_session_times, on='txid')
        .drop_nulls()
    )
    
    cols = ['date', 'kind']
    daily_kinds = (
        tx_kinds.with_columns(pl.col('timestamp').cast(pl.Date).alias('date'))
        .group_by(cols)
        .agg([
            pl.sum('msats'),
            pl.n_unique('session_index').alias('n_sessions'),
            pl.count('txid').alias('n_txs')
        ])
        .sort(cols)
    )
    
    if omit_outlier:
        daily_kinds = daily_kinds.filter(pl.col('date') != pl.date(2024, 6, 24))
    
    bars = alt.Chart(daily_kinds).mark_bar().encode(
        x='date:T',
        y='msats:Q',
        color='kind:N',
        tooltip=['date', 'kind', 'msats']
    )
    
    daily_counts = (
        daily_kinds
        .group_by('date')
        .sum()
        .select(['date', 'n_sessions', 'n_txs'])
        .unpivot(
            on=['n_sessions', 'n_txs'],
            index='date',
            variable_name='type',
            value_name='n'
        )
    )
    
    lines = alt.Chart(daily_counts).mark_line().encode(
        x='date:T',
        y='n:Q',
        color='type:N'
    )
    
    return (bars + lines).resolve_scale(y='independent').properties(width=600, height=300, title=f'{title_label} Transaction Stats')

