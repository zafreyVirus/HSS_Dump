import streamlit as st
import duckdb
import os
import time
import tempfile
from datetime import datetime
import plotly.graph_objects as go

# ============================================================
# CONFIG
# ============================================================
CSV_FILE = "combined_all_columns.csv"
DB_FILE  = "combined.duckdb"
DB_NAME  = "combined"           # DuckDB database name (file basename without .duckdb)
APP_TITLE = "HSS Dump"

USE_DB = os.path.exists(DB_FILE)

# Column names (adjust if your CSV uses different names)
COL_APNTPLID = "APNTPLID"
COL_APN_TYPE = "APN_TYPE"
COL_EPSODB   = "EPSODBPOS"
COL_TCSITPL  = "TCSITPL"
COL_MSISDN   = "MSISDN"


def reader():
    """Return the SQL source: DuckDB table (fast) or CSV (fallback)."""
    if USE_DB:
        return "hss"
    return (
        f"read_csv_auto('{CSV_FILE}', "
        "ignore_errors=true, "
        "null_padding=true, "
        "all_varchar=true, "
        "sample_size=100000, "
        "parallel=true)"
    )


def recon_reader():
    """Return the SQL source for the reconciliation view."""
    if USE_DB:
        return "imsi_recon"
    return None


st.set_page_config(
    page_title=APP_TITLE,
    page_icon="",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ============================================================
# CACHED HELPERS
# ============================================================
@st.cache_resource
def get_connection():
    """Open a DuckDB connection directly to combined.duckdb."""
    if USE_DB:
        con = duckdb.connect(database=DB_FILE, read_only=True)
    else:
        con = duckdb.connect(database=':memory:', read_only=False)

    try:
        con.execute("SET threads TO 4")
        con.execute("SET memory_limit = '6GB'")
        con.execute("SET enable_progress_bar = false")
    except Exception:
        pass

    return con


# Pre-warm connection
if USE_DB:
    try:
        _warm = get_connection()
        _warm.execute("SELECT 1").fetchone()
    except Exception:
        pass


@st.cache_data(show_spinner=False)
def get_columns(csv_path, mtime):
    con = get_connection()
    result = con.execute(f"SELECT * FROM {reader()} LIMIT 0")
    return [d[0] for d in result.description]


@st.cache_data(show_spinner=False)
def get_total_rows(csv_path, mtime):
    con = get_connection()
    result = con.execute(f"SELECT COUNT(*) FROM {reader()}").fetchone()
    return int(result[0]) if result and result[0] is not None else 0


@st.cache_data(show_spinner=False)
def get_preview(csv_path, mtime, n=100):
    con = get_connection()
    return con.execute(f"SELECT * FROM {reader()} LIMIT {n}").df()


def build_where_clause(filters):
    """Build SQL WHERE clause from list of filters."""
    clauses = []
    params = []
    for f in filters:
        col = f["column"]
        val = f["value"]
        mode = f["mode"]

        if mode == "equals":
            clauses.append(f'CAST("{col}" AS VARCHAR) = ?')
            params.append(val)
        elif mode == "not_equals":
            clauses.append(
                f'(CAST("{col}" AS VARCHAR) IS NULL '
                f'OR CAST("{col}" AS VARCHAR) != ?)'
            )
            params.append(val)
        elif mode == "contains":
            clauses.append(f'CAST("{col}" AS VARCHAR) LIKE ?')
            params.append(f"%{val}%")
        elif mode == "not_contains":
            clauses.append(
                f'(CAST("{col}" AS VARCHAR) IS NULL '
                f'OR CAST("{col}" AS VARCHAR) NOT LIKE ?)'
            )
            params.append(f"%{val}%")
        elif mode == "token":
            clauses.append(
                f'(CAST("{col}" AS VARCHAR) = ? '
                f'OR CAST("{col}" AS VARCHAR) LIKE ? '
                f'OR CAST("{col}" AS VARCHAR) LIKE ? '
                f'OR CAST("{col}" AS VARCHAR) LIKE ?)'
            )
            params.extend([val, f"{val}&%", f"%&{val}", f"%&{val}&%"])
        elif mode == "not_token":
            clauses.append(
                f'(CAST("{col}" AS VARCHAR) IS NULL '
                f'OR (CAST("{col}" AS VARCHAR) != ? '
                f'AND CAST("{col}" AS VARCHAR) NOT LIKE ? '
                f'AND CAST("{col}" AS VARCHAR) NOT LIKE ? '
                f'AND CAST("{col}" AS VARCHAR) NOT LIKE ?))'
            )
            params.extend([val, f"{val}&%", f"%&{val}", f"%&{val}&%"])

    if not clauses:
        return "", []
    return " WHERE " + " AND ".join(clauses), params


@st.cache_data(show_spinner=False)
def count_filtered(csv_path, mtime, filters_tuple):
    filters = [dict(f) for f in filters_tuple]
    where, params = build_where_clause(filters)
    con = get_connection()
    sql = f"SELECT COUNT(*) FROM {reader()}{where}"
    result = con.execute(sql, params).fetchone()
    return int(result[0]) if result and result[0] is not None else 0


def run_filter_query(csv_path, filters, limit=None):
    where, params = build_where_clause(filters)
    sql = f"SELECT * FROM {reader()}{where}"
    if limit:
        sql += f" LIMIT {limit}"
    con = get_connection()
    return con.execute(sql, params).df()


def export_filtered_csv(filters):
    """Export filtered rows to CSV using DuckDB's native COPY."""
    where, params = build_where_clause(filters)
    con = get_connection()

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv", prefix="hss_export_")
    tmp_path = tmp.name
    tmp.close()

    try:
        sql = (
            f"COPY (SELECT * FROM {reader()}{where}) "
            f"TO '{tmp_path}' (HEADER, DELIMITER ',')"
        )
        con.execute(sql, params)

        with open(tmp_path, "rb") as f:
            data = f.read()
        return data, len(data)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


# ============================================================
# IMSI RECON HELPERS
# ============================================================
@st.cache_data(show_spinner=False)
def get_recon_summary(db_mtime):
    """Return recon counts: total, match, mismatch, missing."""
    con = get_connection()
    src = recon_reader()
    if src is None:
        return None
    df = con.execute(f"""
        SELECT status, COUNT(*) AS n
        FROM {src}
        GROUP BY status
    """).df()
    counts = dict(zip(df["status"], df["n"]))
    return {
        "total":    int(sum(counts.values())),
        "match":    int(counts.get("Match", 0)),
        "mismatch": int(counts.get("IMSI mismatch", 0)),
        "missing":  int(counts.get("Missing in HSS", 0)),
    }


@st.cache_data(show_spinner=False)
def get_recon_mismatch_df(db_mtime, limit=None):
    """Return mismatch rows as a dataframe."""
    con = get_connection()
    src = recon_reader()
    if src is None:
        return None
    sql = f"SELECT * FROM {src} WHERE status = 'IMSI mismatch'"
    if limit:
        sql += f" LIMIT {limit}"
    return con.execute(sql).df()


def export_recon_csv(db_mtime, status_filter=None):
    """Export recon rows to CSV via DuckDB COPY."""
    con = get_connection()
    src = recon_reader()
    if src is None:
        return None, 0

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv", prefix="imsi_recon_")
    tmp_path = tmp.name
    tmp.close()

    try:
        if status_filter:
            sql = (
                f"COPY (SELECT * FROM {src} WHERE status = ?) "
                f"TO '{tmp_path}' (HEADER, DELIMITER ',')"
            )
            con.execute(sql, [status_filter])
        else:
            sql = f"COPY (SELECT * FROM {src}) TO '{tmp_path}' (HEADER, DELIMITER ',')"
            con.execute(sql)

        with open(tmp_path, "rb") as f:
            data = f.read()
        return data, len(data)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


# ============================================================
# VOICE/APN STATS (Home Tab)
# ============================================================
@st.cache_data(show_spinner=False)
def get_recon_stats(csv_path, mtime):
    con = get_connection()
    src = reader()
    stats = {}

    stats["total_subs"] = con.execute(
        f"SELECT COUNT(*) FROM {src}"
    ).fetchone()[0]

    stats["ps_apn_subs"] = con.execute(
        f"""SELECT COUNT(*) FROM {src}
            WHERE CAST("{COL_APN_TYPE}" AS VARCHAR) LIKE '%PS_APN%'"""
    ).fetchone()[0]

    stats["no_eps"] = con.execute(
        f"""SELECT COUNT(*) FROM {src}
            WHERE TRIM(CAST("{COL_EPSODB}" AS VARCHAR)) = '0'"""
    ).fetchone()[0]

    stats["no_apn"] = con.execute(
        f"""SELECT COUNT(*) FROM {src}
            WHERE TRIM(CAST("{COL_APNTPLID}" AS VARCHAR)) = '-1'"""
    ).fetchone()[0]

    def token_clause(col, val):
        return (
            f'(CAST("{col}" AS VARCHAR) = ? '
            f'OR CAST("{col}" AS VARCHAR) LIKE ? '
            f'OR CAST("{col}" AS VARCHAR) LIKE ? '
            f'OR CAST("{col}" AS VARCHAR) LIKE ?)'
        )

    stats["apn_99"] = con.execute(
        f"SELECT COUNT(*) FROM {src} WHERE {token_clause(COL_APNTPLID, '99')}",
        ["99", "99&%", "%&99", "%&99&%"],
    ).fetchone()[0]

    stats["apn_2"] = con.execute(
        f"SELECT COUNT(*) FROM {src} WHERE {token_clause(COL_APNTPLID, '2')}",
        ["2", "2&%", "%&2", "%&2&%"],
    ).fetchone()[0]

    stats["volte_subs"] = con.execute(
        f"SELECT COUNT(*) FROM {src} WHERE {token_clause(COL_APNTPLID, '26')}",
        ["26", "26&%", "%&26", "%&26&%"],
    ).fetchone()[0]

    stats["volte_tcsitpl_13"] = con.execute(
        f"""SELECT COUNT(*) FROM {src}
            WHERE {token_clause(COL_APNTPLID, '26')}
              AND {token_clause(COL_TCSITPL, '13')}""",
        [
            "26", "26&%", "%&26", "%&26&%",
            "13", "13&%", "%&13", "%&13&%",
        ],
    ).fetchone()[0]

    stats["volte_tcsitpl_not_13"] = (
        stats["volte_subs"] - stats["volte_tcsitpl_13"]
    )

    return stats


# ============================================================
# SIDEBAR
# ============================================================
st.sidebar.title(APP_TITLE)
st.sidebar.markdown("---")

if not os.path.exists(CSV_FILE) and not USE_DB:
    st.error(f"File not found: `{CSV_FILE}`")
    st.info("Place `dashboard.py` in the same folder as `combined_all_columns.csv`.")
    st.stop()

file_mtime = os.path.getmtime(DB_FILE if USE_DB else CSV_FILE)

if USE_DB:
    st.sidebar.success("HSS dump")
else:
    st.sidebar.warning(
        "CSV mode active — run `python3 convert_to_duckdb.py` for ~20× speed"
    )

with st.spinner("Reading file metadata..."):
    columns = get_columns(CSV_FILE, file_mtime)
    total_rows = get_total_rows(CSV_FILE, file_mtime)

st.sidebar.metric("Total columns", f"{len(columns):,}")

# ============================================================
# TABS
# ============================================================
tab_home, tab1, tab2, tab3, tab4 = st.tabs([
    "Home",
    "Quick Filters",
    "Custom Filter",
    "Search",
    "Preview",
])

# ------------------------------------------------------------
# TAB 0: HOME (RECON)
# ------------------------------------------------------------
with tab_home:
    st.header("Recon Dashboard")
    st.caption("Overview of subscriber statistics")

    colA, colB = st.columns([4, 1])
    with colB:
        if st.button("Refresh Stats", use_container_width=True):
            get_recon_stats.clear()
            get_recon_summary.clear()
            st.rerun()

    with st.spinner("Computing stats (first load may take ~10 seconds)..."):
        stats = get_recon_stats(CSV_FILE, file_mtime)
        recon = get_recon_summary(file_mtime)

    total = stats["total_subs"]

    def pct(n):
        return f"{(n / total * 100):.2f}% of total" if total else "0%"

    # ---- Row 1: Main KPIs ----
    r1c1, r1c2 = st.columns(2)
    r1c1.metric("VoLTE Subscribers", f"{stats['volte_subs']:,}",
                delta=pct(stats['volte_subs']), delta_color="off")
    r1c2.metric("PS APN Subscribers", f"{stats['ps_apn_subs']:,}",
                delta=pct(stats['ps_apn_subs']), delta_color="off")

    # ---- Row 2: Secondary KPIs ----
    r2c1, r2c2, r2c3, r2c4 = st.columns(4)
    r2c1.metric("No EPS", f"{stats['no_eps']:,}",
                delta=pct(stats['no_eps']), delta_color="off")
    r2c2.metric("No APN", f"{stats['no_apn']:,}",
                delta=pct(stats['no_apn']), delta_color="off")
    r2c3.metric("APN 99 (dummy)", f"{stats['apn_99']:,}",
                delta=pct(stats['apn_99']), delta_color="off")
    r2c4.metric("APN 2 (WAP)", f"{stats['apn_2']:,}",
                delta=pct(stats['apn_2']), delta_color="off")

    st.markdown("---")

    # ---- VoLTE donut chart ----
    st.subheader("VoLTE Subscribers by TCSITPL")
    st.caption(
        "VoLTE subscribers (APNTPLID contains 26) split by TCSITPL = 13 vs TCSITPL ≠ 13"
    )

    donut_col1, donut_col2 = st.columns([2, 1])

    with donut_col1:
        labels = ["TCSITPL = 13", "TCSITPL ≠ 13"]
        values = [stats["volte_tcsitpl_13"], stats["volte_tcsitpl_not_13"]]
        colors = ["#2ecc71", "#e74c3c"]

        fig = go.Figure(
            data=[
                go.Pie(
                    labels=labels,
                    values=values,
                    hole=0.55,
                    marker=dict(colors=colors),
                    textinfo="label+percent",
                    textposition="outside",
                    hovertemplate=(
                        "<b>%{label}</b><br>"
                        "Count: %{value:,}<br>"
                        "Share: %{percent}<extra></extra>"
                    ),
                )
            ]
        )
        fig.update_layout(
            showlegend=True,
            height=440,
            margin=dict(l=20, r=20, t=40, b=20),
            annotations=[
                dict(
                    text=f"<b>{stats['volte_subs']:,}</b><br>VoLTE Subs",
                    x=0.5, y=0.5,
                    font_size=16,
                    showarrow=False,
                )
            ],
        )
        st.plotly_chart(fig, use_container_width=True)

    with donut_col2:
        st.markdown("### Legend")
        v_total = stats["volte_subs"]

        def v_pct(n):
            return f"{(n / v_total * 100):.2f}%" if v_total else "0%"

        st.markdown(
            f"- **TCSITPL = 13**: `{stats['volte_tcsitpl_13']:,}` "
            f"({v_pct(stats['volte_tcsitpl_13'])})"
        )
        st.markdown(
            f"- **TCSITPL ≠ 13**: `{stats['volte_tcsitpl_not_13']:,}` "
            f"({v_pct(stats['volte_tcsitpl_not_13'])})"
        )
        st.markdown("---")
        st.markdown(f"**Total VoLTE**: `{v_total:,}`")

    # ============================================================
    # IMSI RECONCILIATION SECTION (Home tab)
    # ============================================================
    st.markdown("---")
    st.subheader("IMSI Reconciliation (IMS vs HSS)")
    st.caption(
        "Every MSISDN in the IMS dump is looked up in the HSS dump. "
        "A mismatch means the two dumps disagree on the IMSI for that MSISDN."
    )

    if recon is None:
        st.warning(
            "`imsi_recon` view not found in the database. "
            "Run `make_recon_view.py` to build it."
        )
    else:
        rc1, rc2, rc3, rc4 = st.columns(4)
        rc1.metric("IMS subs (total)",   f"{recon['total']:,}")
        rc2.metric("IMSI matched",       f"{recon['match']:,}",
                   delta=f"{(recon['match']/recon['total']*100):.2f}%"
                         if recon['total'] else None,
                   delta_color="off")
        rc3.metric("IMSI mismatch",      f"{recon['mismatch']:,}",
                   delta=f"{(recon['mismatch']/recon['total']*100):.2f}%"
                         if recon['total'] else None,
                   delta_color="inverse")
        rc4.metric("Missing in HSS",     f"{recon['missing']:,}",
                   delta=f"{(recon['missing']/recon['total']*100):.2f}%"
                         if recon['total'] else None,
                   delta_color="off")

        st.markdown("")

        pie_col1, pie_col2 = st.columns([2, 1])

        with pie_col1:
            r_labels = ["Matched", "IMSI mismatch", "Missing in HSS"]
            r_values = [recon["match"], recon["mismatch"], recon["missing"]]
            r_colors = ["#2ecc71", "#e74c3c", "#f39c12"]

            rfig = go.Figure(
                data=[
                    go.Pie(
                        labels=r_labels,
                        values=r_values,
                        hole=0.0,   # regular pie
                        marker=dict(colors=r_colors),
                        textinfo="label+percent",
                        textposition="outside",
                        hovertemplate=(
                            "<b>%{label}</b><br>"
                            "Count: %{value:,}<br>"
                            "Share: %{percent}<extra></extra>"
                        ),
                    )
                ]
            )
            rfig.update_layout(
                showlegend=True,
                height=440,
                margin=dict(l=20, r=20, t=40, b=20),
            )
            st.plotly_chart(rfig, use_container_width=True)

        with pie_col2:
            st.markdown("### Mismatch breakdown")
            if recon["total"]:
                st.markdown(
                    f"- **Matched**: `{recon['match']:,}` "
                    f"({recon['match']/recon['total']*100:.2f}%)"
                )
                st.markdown(
                    f"- **IMSI mismatch**: `{recon['mismatch']:,}` "
                    f"({recon['mismatch']/recon['total']*100:.2f}%)"
                )
                st.markdown(
                    f"- **Missing in HSS**: `{recon['missing']:,}` "
                    f"({recon['missing']/recon['total']*100:.2f}%)"
                )
            st.markdown("---")
            st.markdown(f"**Total IMS**: `{recon['total']:,}`")

        st.markdown("")
        st.markdown("### Preview: IMSI mismatch (first 100)")
        mismatch_df = get_recon_mismatch_df(file_mtime, limit=100)
        if mismatch_df is not None and not mismatch_df.empty:
            st.dataframe(mismatch_df, use_container_width=True, height=300)

            if st.button("Generate IMSI mismatch CSV", key="home_gen_mismatch"):
                with st.spinner("Preparing CSV..."):
                    data, size = export_recon_csv(file_mtime, status_filter="IMSI mismatch")
                    st.session_state["home_mismatch_csv"] = data
                    st.session_state["home_mismatch_size"] = size

            if "home_mismatch_csv" in st.session_state:
                size_mb = st.session_state["home_mismatch_size"] / (1024 * 1024)
                st.download_button(
                    label=f"⬇️ Download imsi_mismatch.csv ({size_mb:.2f} MB)",
                    data=st.session_state["home_mismatch_csv"],
                    file_name="imsi_mismatch.csv",
                    mime="text/csv",
                    key="home_dl_mismatch",
                )
        else:
            st.info("No IMSI mismatches found.")

# ------------------------------------------------------------
# TAB 1: QUICK FILTERS
# ------------------------------------------------------------
with tab1:
    st.header("Quick Filters")
    st.caption("One-click filters — click to get a count, then download the results")

    # ---- IMSI recon download (dropdown) ----
    st.subheader("IMSI Reconciliation Downloads (IMS vs HSS)")
    recon_q = get_recon_summary(file_mtime)
    if recon_q is None:
        st.warning("`imsi_recon` table not found. Run the recon builder first.")
    else:
        # What each dropdown option maps to:
        #   - status_filter = None → whole table
        #   - status_filter = "IMSI mismatch" / "Match" / "Missing in HSS" → filtered
        DOWNLOAD_OPTIONS = {
            "IMSI mismatch":          ("IMSI mismatch",  "imsi_mismatch.csv"),
            "Matched IMSI":           ("Match",          "imsi_matched.csv"),
            "Missing in HSS":         ("Missing in HSS", "imsi_missing_in_hss.csv"),
            "Whole IMS dump":         (None,             "ims_dump_full.csv"),
        }

        # Show counts so the operator knows what they're about to grab
        counts = {
            "IMSI mismatch":  recon_q["mismatch"],
            "Matched IMSI":   recon_q["match"],
            "Missing in HSS": recon_q["missing"],
            "Whole IMS dump": recon_q["total"],
        }

        dl_col1, dl_col2 = st.columns([3, 1])

        with dl_col1:
            choice = st.selectbox(
                "Choose what to download",
                list(DOWNLOAD_OPTIONS.keys()),
                format_func=lambda k: f"{k}  ({counts[k]:,} rows)",
                key="qf_recon_choice",
            )
            status_filter, default_filename = DOWNLOAD_OPTIONS[choice]

            st.caption(
                f"**Total IMS subscribers:** {recon_q['total']:,}  |  "
                f"Matched: {recon_q['match']:,}  |  "
                f"Mismatch: {recon_q['mismatch']:,}  |  "
                f"Missing in HSS: {recon_q['missing']:,}"
            )

        with dl_col2:
            st.write("")
            st.write("")
            if st.button("Prepare CSV", key="qf_prep_recon",
                         use_container_width=True):
                with st.spinner(f"Preparing {choice}..."):
                    data, size = export_recon_csv(file_mtime,
                                                  status_filter=status_filter)
                    st.session_state["qf_recon_csv"] = data
                    st.session_state["qf_recon_size"] = size
                    st.session_state["qf_recon_name"] = default_filename
                    st.session_state["qf_recon_label"] = choice

        if "qf_recon_csv" in st.session_state:
            size_mb = st.session_state["qf_recon_size"] / (1024 * 1024)
            st.download_button(
                label=(
                    f"⬇️ Download {st.session_state['qf_recon_label']} "
                    f"({size_mb:.2f} MB)"
                ),
                data=st.session_state["qf_recon_csv"],
                file_name=st.session_state["qf_recon_name"],
                mime="text/csv",
                key="qf_dl_recon",
            )

    st.markdown("---")

    quick_filters = {
        "PS_APN":        [{"column": "APN_TYPE", "mode": "contains", "value": "PS_APN"}],
        "MSISDN = -1":   [{"column": "MSISDN",   "mode": "equals",   "value": "-1"}],
        "APNTPLID = -1": [{"column": "APNTPLID", "mode": "equals",   "value": "-1"}],
        "APNTPLID 2":    [{"column": "APNTPLID", "mode": "token",    "value": "2"}],
        "APNTPLID 18":   [{"column": "APNTPLID", "mode": "token",    "value": "18"}],
        "APNTPLID 26":   [{"column": "APNTPLID", "mode": "token",    "value": "26"}],
        "APNTPLID 55":   [{"column": "APNTPLID", "mode": "token",    "value": "55"}],
        "APNTPLID 79":   [{"column": "APNTPLID", "mode": "token",    "value": "79"}],
        "APNTPLID 99":   [{"column": "APNTPLID", "mode": "token",    "value": "99"}],
    }

    cols = st.columns(4)
    for i, (name, flts) in enumerate(quick_filters.items()):
        with cols[i % 4]:
            if st.button(name, use_container_width=True, key=f"quick_{name}"):
                with st.spinner(f"Counting {name}..."):
                    try:
                        cnt = count_filtered(CSV_FILE, file_mtime, tuple(sorted(
                            (tuple(sorted(f.items())) for f in flts)
                        )))
                        st.session_state[f"quick_result_{name}"] = cnt
                    except Exception as e:
                        st.error(f"Error: {e}")

    st.markdown("---")

    for name, flts in quick_filters.items():
        count_key = f"quick_result_{name}"
        if count_key not in st.session_state:
            continue

        cnt = st.session_state[count_key]
        st.success(f"**{name}**: {cnt:,} rows matched")

        if cnt == 0:
            continue

        if st.button(f"Generate CSV for {name}", key=f"gen_{name}"):
            with st.spinner(f"Preparing {name}..."):
                try:
                    csv_bytes, size = export_filtered_csv(flts)
                    st.session_state[f"csv_bytes_{name}"] = csv_bytes
                    st.session_state[f"csv_size_{name}"] = size
                except Exception as e:
                    st.error(f"Error: {e}")

        if f"csv_bytes_{name}" in st.session_state:
            size_mb = st.session_state[f"csv_size_{name}"] / (1024 * 1024)
            st.download_button(
                label=f"Download {name}.csv ({size_mb:.1f} MB)",
                data=st.session_state[f"csv_bytes_{name}"],
                file_name=f"{name.replace(' ', '_').replace('=', '')}.csv",
                mime="text/csv",
                key=f"dl_{name}",
            )

# ------------------------------------------------------------
# TAB 2: CUSTOM FILTER
# ------------------------------------------------------------
with tab2:
    st.header("Custom Filter")
    st.caption("Combine multiple filters with AND logic")

    MODE_HELP = (
        "equals: exact match\n"
        "not_equals: exclude exact value (NULLs included)\n"
        "contains: substring anywhere\n"
        "not_contains: exclude substring (NULLs included)\n"
        "token: match a full '&'-separated element (for APNTPLID)\n"
        "not_token: exclude a full '&'-separated element"
    )

    if "custom_filters" not in st.session_state:
        st.session_state.custom_filters = [
            {"column": columns[0], "mode": "equals", "value": ""}
        ]

    for i, flt in enumerate(st.session_state.custom_filters):
        c1, c2, c3, c4 = st.columns([3, 2, 3, 1])
        with c1:
            flt["column"] = st.selectbox(
                "Column", columns,
                index=columns.index(flt["column"]) if flt["column"] in columns else 0,
                key=f"col_{i}",
            )
        with c2:
            mode_options = ["equals", "not_equals", "contains", "not_contains",
                            "token", "not_token"]
            flt["mode"] = st.selectbox(
                "Mode", mode_options,
                index=mode_options.index(flt["mode"]) if flt["mode"] in mode_options else 0,
                key=f"mode_{i}",
                help=MODE_HELP,
            )
        with c3:
            flt["value"] = st.text_input(
                "Value", value=flt["value"], key=f"val_{i}", placeholder="e.g., 79",
            )
        with c4:
            st.write("")
            st.write("")
            if st.button("🗑", key=f"del_{i}"):
                st.session_state.custom_filters.pop(i)
                st.rerun()

    col_a, col_b = st.columns([1, 4])
    with col_a:
        if st.button("➕ Add Filter"):
            st.session_state.custom_filters.append(
                {"column": columns[0], "mode": "equals", "value": ""}
            )
            st.rerun()
    with col_b:
        if st.button("🧹 Reset Filters"):
            st.session_state.custom_filters = [
                {"column": columns[0], "mode": "equals", "value": ""}
            ]
            st.rerun()

    st.markdown("---")

    if st.button("Apply Filters", type="primary"):
        valid_filters = [f for f in st.session_state.custom_filters if f["value"].strip()]
        if not valid_filters:
            st.warning("Please enter at least one value")
        else:
            with st.spinner("Filtering..."):
                try:
                    t0 = time.time()
                    cnt = count_filtered(CSV_FILE, file_mtime, tuple(sorted(
                        (tuple(sorted(f.items())) for f in valid_filters)
                    )))
                    elapsed = time.time() - t0
                    st.success(f"{cnt:,} rows matched ({elapsed:.3f}s)")
                    if cnt > 0:
                        st.session_state.custom_valid_filters = valid_filters
                except Exception as e:
                    st.error(f"Error: {e}")

    if "custom_valid_filters" in st.session_state:
        if st.button("Generate CSV", key="gen_custom"):
            with st.spinner("Preparing CSV..."):
                try:
                    csv_bytes, size = export_filtered_csv(st.session_state.custom_valid_filters)
                    st.session_state["custom_csv_bytes"] = csv_bytes
                    st.session_state["custom_csv_size"] = size
                except Exception as e:
                    st.error(f"Error: {e}")

        if "custom_csv_bytes" in st.session_state:
            size_mb = st.session_state["custom_csv_size"] / (1024 * 1024)
            st.download_button(
                label=f"Download filtered.csv ({size_mb:.1f} MB)",
                data=st.session_state["custom_csv_bytes"],
                file_name="filtered_custom.csv",
                mime="text/csv",
                key="dl_custom",
            )

# ------------------------------------------------------------
# TAB 3: SEARCH
# ------------------------------------------------------------
with tab3:
    st.header("Search")
    st.caption("Search for an exact value in a specific column")

    c1, c2 = st.columns([2, 3])
    with c1:
        search_col = st.selectbox("Column", columns, key="search_col")
    with c2:
        search_val = st.text_input("Value", key="search_val",
                                   placeholder="e.g., 26588807xxxx")

    if st.button("Search", type="primary"):
        if not search_val.strip():
            st.warning("Enter a value to search")
        else:
            filters = [{"column": search_col, "mode": "equals",
                        "value": search_val.strip()}]
            with st.spinner("Searching..."):
                t0 = time.time()
                cnt = count_filtered(CSV_FILE, file_mtime, tuple(sorted(
                    (tuple(sorted(f.items())) for f in filters)
                )))
                elapsed = time.time() - t0

                if cnt == 0:
                    st.info(f"No matches for `{search_val}` in `{search_col}` "
                            f"({elapsed:.3f}s)")
                else:
                    st.success(f"Found {cnt:,} matches ({elapsed:.3f}s)")
                    df = run_filter_query(CSV_FILE, filters, limit=1000)
                    st.dataframe(df, use_container_width=True)
                    if cnt > 1000:
                        st.caption(f"Showing first 1,000 of {cnt:,} rows")

                    st.session_state["search_filters"] = filters
                    st.session_state["search_col_val"] = search_col
                    st.session_state["search_val_val"] = search_val

    if "search_filters" in st.session_state:
        if st.button("Generate CSV", key="gen_search"):
            with st.spinner("Preparing full CSV..."):
                try:
                    csv_bytes, size = export_filtered_csv(st.session_state["search_filters"])
                    st.session_state["search_csv_bytes"] = csv_bytes
                    st.session_state["search_csv_size"] = size
                except Exception as e:
                    st.error(f"Error: {e}")

        if "search_csv_bytes" in st.session_state:
            size_mb = st.session_state["search_csv_size"] / (1024 * 1024)
            st.download_button(
                label=f"Download results ({size_mb:.1f} MB)",
                data=st.session_state["search_csv_bytes"],
                file_name=f"search_{st.session_state['search_col_val']}_"
                          f"{st.session_state['search_val_val']}.csv",
                mime="text/csv",
                key="dl_search",
            )

# ------------------------------------------------------------
# TAB 4: PREVIEW
# ------------------------------------------------------------
with tab4:
    st.header("Preview")
    st.caption("First 100 rows of the file")

    with st.spinner("Loading preview..."):
        df = get_preview(CSV_FILE, file_mtime, n=100)
    st.dataframe(df, use_container_width=True)
    st.caption(f"Showing 100 of {total_rows:,} rows")
