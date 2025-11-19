import streamlit as st
import pandas as pd
import pdfplumber
import re
import io

# ---------- 工具函数：安全转 float ----------
def safe_float(x):
    if x is None:
        return None
    s = str(x).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None

# ---------- 工具函数：把 *蔬菜*芥兰苗 拆成 类别 + 商品 ----------
def split_category_item(name: str):
    if not isinstance(name, str):
        name = str(name) if name is not None else ""
    name = name.strip()
    m = re.match(r"\*(?P<cat>[^*]+)\*(?P<item>.+)", name)
    if m:
        return m.group("cat").strip(), m.group("item").strip()
    return "未分类", name

# ---------- 从第一页文字里提取发票抬头信息 ----------
def parse_header_text(text: str):
    info = {}
    if not text:
        return info

    # 发票号码
    m = re.search(r"发票号码[:：]\s*(\d+)", text)
    if m:
        info["发票号码"] = m.group(1)

    # 开票日期
    m = re.search(r"开票日期[:：]\s*([0-9]{4}年[0-9]{1,2}月[0-9]{1,2}日)", text)
    if m:
        info["开票日期"] = m.group(1)

    # 名称（买方 / 卖方）
    names = re.findall(r"名称[:：]\s*([^\s]+)", text)
    if len(names) >= 1:
        info["购买方名称"] = names[0]
    if len(names) >= 2:
        info["销售方名称"] = names[1]

    # 统一社会信用代码/纳税人识别号
    codes = re.findall(r"统一社会信用代码/纳税人识别号[:：]\s*([0-9A-Z]+)", text)
    if len(codes) >= 1:
        info["购买方税号"] = codes[0]
    if len(codes) >= 2:
        info["销售方税号"] = codes[1]

    return info

# ---------- 核心：按“每一行文本”解析发票 ----------
def parse_invoice_pdf(uploaded_file):
    """
    按行解析一张 PDF 发票。
    每一行的格式类似：
    *蔬菜*芥兰苗 斤 72 5 360.00 免税 ***
           ↑   ↑  ↑  ↑   ↑
         单位 数量 单价 金额 税率/税额

    返回 DataFrame：
    ['发票文件','发票号码','开票日期','购买方名称','销售方名称',
     '购买方税号','销售方税号','页码','类别','商品','单位','数量','单价','金额','原始项目名称']
    """
    file_bytes = uploaded_file.read()
    rows = []

    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        # 先从第一页拿发票抬头信息
        header_text = pdf.pages[0].extract_text() or ""
        header_info = parse_header_text(header_text)

        # 再逐页按行解析明细
        for page_idx, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if not line:
                    continue

                # 只解析包含 "*类别*商品" 的行
                # 例如：*蔬菜*芥兰苗 斤 72 5 360.00 免税 ***
                m = re.match(r"\*(?P<cat>[^*]+)\*(?P<item>\S+)", line)
                if not m:
                    continue

                tokens = line.split()
                # 期望结构： [*蔬菜*芥兰苗, 单位, 数量, 单价, 金额, 税率, 税额...]
                if len(tokens) < 5:
                    # 太短的行直接跳过
                    continue

                name_token = tokens[0]       # *蔬菜*芥兰苗
                unit       = tokens[1]       # 斤
                qty        = safe_float(tokens[2])  # 72
                price      = safe_float(tokens[3])  # 5
                amount     = safe_float(tokens[4])  # 360.00

                category, item = split_category_item(name_token)

                data = {
                    "发票文件": uploaded_file.name,
                    "页码": page_idx + 1,
                    "类别": category,
                    "商品": item,
                    "单位": unit,
                    "数量": qty,
                    "单价": price,
                    "金额": amount,
                    "原始项目名称": name_token,
                }
                # 把抬头信息也附在每一行上
                data.update(header_info)

                rows.append(data)

    if not rows:
        # 如果什么都没解析到，返回空表，但列名先建好
        cols = [
            "发票文件", "发票号码", "开票日期",
            "购买方名称", "购买方税号",
            "销售方名称", "销售方税号",
            "页码", "类别", "商品", "单位",
            "数量", "单价", "金额", "原始项目名称",
        ]
        return pd.DataFrame(columns=cols)

    df = pd.DataFrame(rows)
    df["金额"] = pd.to_numeric(df["金额"], errors="coerce")
    return df

# ---------- 关键词搜索 ----------
def search_items(df: pd.DataFrame, query: str) -> pd.DataFrame:
    if not query:
        return df
    q = query.strip()
    mask = df["类别"].astype(str).str.contains(q, na=False) | \
           df["商品"].astype(str).str.contains(q, na=False)
    return df[mask]

# ---------- 日期简化 ----------
def format_date_short(raw):
    if not raw:
        return ""
    s = str(raw).strip()
    m = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", s)
    if m:
        y, mo, d = m.groups()
        return f"{y}-{int(mo):02d}-{int(d):02d}"
    return s
    
# ==================== Streamlit 页面 ====================

st.subheader("电子发票自动分类汇总")

st.markdown(
    """
1. 上传一张或多张电子发票 PDF
2. 程序自动识别发票内容
3. 提取 `*类别*商品` 信息，并按类别汇总金额
4. 可以输入关键词筛选明细
"""
)

uploaded_files = st.file_uploader(
    "上传电子发票 （PDF）",
    type=["pdf"],
    accept_multiple_files=True,
)

# ====== 解析按钮 + 状态初始化 ======

# 初始化 session_state（只在第一次运行时生效）
if "df_all" not in st.session_state:
    st.session_state["df_all"] = None
    st.session_state["n_files"] = 0

# 点击按钮：只负责“读取 PDF + 解析 + 存到 session_state”
if st.button("开始解析并汇总"):
    if not uploaded_files:
        st.warning("请先上传至少一份发票 PDF。")
        st.session_state["df_all"] = None
        st.session_state["n_files"] = 0
    else:
        all_dfs = []
        seen_invoices = set()  # 记录已经解析过的发票“身份证”

        for f in uploaded_files:
            df_one = parse_invoice_pdf(f)
            if df_one.empty:
                st.warning(f"文件 {f.name} 没有解析到明细行，请确认格式。")
                continue

            # ====== 构造这张发票的“身份证” ======
            # 优先用：发票号码 + 开票日期 + 购买方名称 + 销售方名称
            invoice_no = None
            issue_date = None
            buyer_name = None
            seller_name = None

            if "发票号码" in df_one.columns and df_one["发票号码"].notna().any():
                invoice_no = df_one["发票号码"].dropna().iloc[0]
            if "开票日期" in df_one.columns and df_one["开票日期"].notna().any():
                issue_date = df_one["开票日期"].dropna().iloc[0]
            if "购买方名称" in df_one.columns and df_one["购买方名称"].notna().any():
                buyer_name = df_one["购买方名称"].dropna().iloc[0]
            if "销售方名称" in df_one.columns and df_one["销售方名称"].notna().any():
                seller_name = df_one["销售方名称"].dropna().iloc[0]

            # 如果抬头信息都没有，就退而求其次用文件名当 key
            if any([invoice_no, issue_date, buyer_name, seller_name]):
                key = (invoice_no, issue_date, buyer_name, seller_name)
            else:
                key = ("FILE_ONLY", f.name)

            # ====== 检查是否重复 ======
            if key in seen_invoices:
                # 已经解析过同一张发票，跳过这次
                if invoice_no or issue_date:
                    msg = (
                        "检测到重复发票：  \n"
                        f"发票号：{invoice_no or '未知'}  \n"
                        f"日期：{issue_date or '未知'}  \n"
                        f"文件：{f.name} 已被自动忽略。"
                    )
                    st.warning(msg)
                else:
                    st.warning(f"文件 {f.name} 与之前上传的文件重复（根据文件名判断），已自动忽略。")
                continue

            # 记录这张发票的 key，避免后面再被计入
            seen_invoices.add(key)
            all_dfs.append(df_one)

        if not all_dfs:
            st.error("所有文件都没有解析到有效的发票明细（或全部为重复发票），请检查格式。")
            st.session_state["df_all"] = None
            st.session_state["n_files"] = 0
        else:
            df_all = pd.concat(all_dfs, ignore_index=True)
            st.session_state["df_all"] = df_all
            st.session_state["n_files"] = len(all_dfs)
            st.success("解析完成，可以在下方查看汇总结果。")

# ====== 下面开始：只要 session 里有数据，就根据当前控件状态来展示 ======
st.markdown("---")
df_all = st.session_state.get("df_all")

if df_all is not None and not df_all.empty:
    n_files = st.session_state.get("n_files", 1)
    total_amount = df_all["金额"].sum()

    # ================= 情况 1：只上传 1 个文件 =================
    if n_files <= 1:
        # 按类别汇总金额
        summary = (
            df_all.groupby("类别", dropna=False)["金额"]
                  .sum()
                  .reset_index()
                  .sort_values("金额", ascending=False)
                  .reset_index(drop=True)
        )

        st.subheader("按类别汇总金额")

        # 这里插入“开票日期：...” —— 放在标题和表格之间
        if "开票日期" in df_all.columns and df_all["开票日期"].notna().any():
            first_date_raw = df_all["开票日期"].dropna().iloc[0]
            first_date_short = format_date_short(first_date_raw)
            date_label = first_date_short
            st.write(f"**开票日期：{first_date_short}**")
        else:
            date_label = "本次"
            st.write("**开票日期：未知**")

        # 不显示左侧 0,1,... 的索引
        st.dataframe(summary, use_container_width=True, hide_index=True)
        st.write(f"**{date_label} 发票合计金额：{total_amount:.2f} 元**")

    # ================= 情况 2：上传了多个文件 =================
    else:
        # 是否有开票日期列，有的话就按日期汇总，没有就按发票文件名汇总
        if "开票日期" in df_all.columns:
            df_all["开票日期_短"] = df_all["开票日期"].apply(
                lambda x: format_date_short(x) if pd.notna(x) else ""
            )
            index_col = "开票日期_短"
            index_name = "开票日期"
        else:
            index_col = "发票文件"
            index_name = "发票文件"

        # 先算好“全部发票”的透视表：行 = 日期/文件，列 = 类别，值 = 金额
        pivot = pd.pivot_table(
            df_all,
            values="金额",
            index=index_col,
            columns="类别",
            aggfunc="sum",
            fill_value=0,
        )

        # 每行合计
        pivot["合计"] = pivot.sum(axis=1)

        # 最后一行总合计
        total_row = pd.DataFrame(pivot.sum()).T
        total_row.index = ["合计"]
        pivot = pd.concat([pivot, total_row])

        # 行索引恢复成普通列，并改名为“开票日期”或“发票文件”
        pivot.index.name = index_col            # 确保索引有名字
        pivot = pivot.reset_index()
        # 有的情况下列名是 "index"，有的情况下是 index_col，这里两种都处理
        pivot = pivot.rename(columns={"index": index_name, index_col: index_name})

        # 标题放在最前面
        st.subheader("按日期 × 类别 汇总金额")

        # ---- 多文件场景下：选择某一天，替代透视表显示 ----
        selected_date = None
        has_date_short = "开票日期_短" in df_all.columns

        if has_date_short:
            # 所有可选日期
            date_options = sorted(
                d for d in df_all["开票日期_短"].dropna().unique() if d
            )
            if date_options:
                selected_date = st.selectbox(
                    "选择开票日期，查看该日按类别汇总（默认显示全部）",
                    options=["默认"] + date_options,
                )

        # ====== 根据选择，二选一展示 ======
        if (not has_date_short) or (not selected_date) or (selected_date == "默认"):
            # 情况 A：不选具体日期 → 显示全部发票的“按日期×类别”透视表
            st.dataframe(pivot, use_container_width=True, hide_index=True)
        else:
            # 情况 B：选中了某一天 → 只显示那一天的“按类别汇总”，不再显示透视表
            df_day = df_all[df_all["开票日期_短"] == selected_date]
            summary_day = (
                df_day.groupby("类别", dropna=False)["金额"]
                      .sum()
                      .reset_index()
                      .sort_values("金额", ascending=False)
                      .reset_index(drop=True)
            )
            # 一行小字提示
            st.caption(f"{selected_date} 按类别汇总金额")
            # 原本的内容：当日按类别汇总表 + 当日合计金额
            st.dataframe(
                summary_day,
                use_container_width=True,
                hide_index=True
            )
            st.write(
                f"**{selected_date} 发票合计金额：{df_day['金额'].sum():.2f} 元**"
            )

        # 无论显示哪一种视图，都在下面给出“本次上传发票合计金额”
        st.write(f"**本次上传发票合计金额：{total_amount:.2f} 元**")

    # ===== 在汇总金额和明细记录之间加一条分割线 =====
    st.markdown("---")

    # =========================================================
    #               下面是“明细记录（按发票分组显示）”
    # =========================================================
    st.subheader("明细记录")
    
    # ===== 在已解析的数据里搜索（不会重新解析）=====
    query_text = st.text_input("搜索关键词（任意）", "", key="search_keyword")
    # 根据当前搜索关键词过滤明细
    df_filtered = search_items(df_all, query_text)

    st.write(
        f"当前搜索关键词：`{query_text if query_text else '未输入'}`，"
        f"共 {len(df_filtered)} 条记录。"
    )

    # ---------- 搜索结果统计折叠块 ----------
    if query_text.strip():  # 只在输入了关键词时显示
        df_stats = df_filtered.copy()

        # 涉及发票数量
        if "发票文件" in df_stats.columns:
            n_invoices = df_stats["发票文件"].nunique()
        else:
            n_invoices = None

        # 采购记录条数（当前过滤结果的行数）
        n_records = len(df_stats)

        # 总数量
        if "数量" in df_stats.columns:
            total_qty = pd.to_numeric(df_stats["数量"], errors="coerce").sum()
        else:
            total_qty = None

        # 总金额
        if "金额" in df_stats.columns:
            total_amt = pd.to_numeric(df_stats["金额"], errors="coerce").sum()
        else:
            total_amt = None

        # 尝试识别单位（如果只有一个唯一单位就用它，否则不显示单位）
        if "单位" in df_stats.columns:
            units = df_stats["单位"].dropna().unique()
            unit_label = units[0] if len(units) == 1 else ""
        else:
            unit_label = ""
        unit_suffix = f"/{unit_label}" if unit_label else ""   # 单价用的 “/斤”
        qty_suffix  = f" {unit_label}" if unit_label else ""   # 数量用的 “ 斤”

        # 单价统计
        if "单价" in df_stats.columns:
            price_series = pd.to_numeric(df_stats["单价"], errors="coerce")
            avg_price = price_series.mean()
            max_price = price_series.max()
            min_price = price_series.min()
        else:
            avg_price = max_price = min_price = None

        # 平均每次采购数量
        if total_qty is not None and n_records > 0:
            avg_qty_per_record = total_qty / n_records
        else:
            avg_qty_per_record = None

        # 默认折叠
        with st.expander(f"「{query_text}」统计（本次上传发票）", expanded=False):
            st.write(f"- 涉及发票：{n_invoices if n_invoices is not None else '未知'} 张")
            st.write(f"- 采购记录：{n_records} 条")
            if total_qty is not None:
                st.write(f"- 总数量：{total_qty:.2f}{qty_suffix}")
                if avg_qty_per_record is not None:
                    st.write(f"- 单次平均采购数量：{avg_qty_per_record:.2f}{qty_suffix}")
            if total_amt is not None:
                st.write(f"- 总金额：{total_amt:.2f} 元")
            if avg_price is not None:
                st.write(f"- 平均单价：{avg_price:.2f} 元{unit_suffix}")
                st.write(f"- 最高单价：{max_price:.2f} 元{unit_suffix}")
                st.write(f"- 最低单价：{min_price:.2f} 元{unit_suffix}")
    
    # 用这些列来区分不同发票
    group_cols = ["发票文件", "发票号码", "开票日期", "购买方名称", "销售方名称"]
    group_cols = [c for c in group_cols if c in df_filtered.columns]

    if not group_cols:
        # 如果没有抬头信息，就退化为简单明细表
        st.warning("明细中没有发票抬头信息，只能直接显示商品明细。")
        detail_cols = ["类别", "商品", "单位", "数量", "单价", "金额", "页码"]
        detail_cols = [c for c in detail_cols if c in df_filtered.columns]
        st.dataframe(
            df_filtered[detail_cols].reset_index(drop=True),
            use_container_width=True
        )
    else:
        # 按发票分组，逐张显示
        for key, df_inv in df_filtered.groupby(group_cols, dropna=False):
            # key 可能是单值或元组，这里统一变成 dict
            if len(group_cols) == 1:
                key = (key,)
            inv_info = dict(zip(group_cols, key))

            # 取日期，用于折叠块标题
            raw_date = inv_info.get("开票日期", "")
            date_short = format_date_short(raw_date) if raw_date else ""
            if date_short:
                expander_title = f"开票日期：{date_short}"
            elif raw_date:
                expander_title = f"开票日期：{raw_date}"
            else:
                expander_title = "开票日期：未知"

            # 其他抬头字段
            file_name   = str(inv_info.get("发票文件", ""))
            invoice_no  = str(inv_info.get("发票号码", "")).strip()
            buyer_name  = inv_info.get("购买方名称", "")
            seller_name = inv_info.get("销售方名称", "")

            # 默认收起
            with st.expander(expander_title, expanded=False):
                # 顶部显示完整抬头信息
                if file_name:
                    st.markdown(f"**文件名：** {file_name}")
                if invoice_no:
                    st.markdown(f"**发票号码：** {invoice_no}")
                if raw_date:
                    st.markdown(f"**开票日期：** {raw_date}")
                if buyer_name:
                    st.markdown(f"**购买方：** {buyer_name}")
                if seller_name:
                    st.markdown(f"**销售方：** {seller_name}")

                st.markdown("---")

                # 这一张发票的商品明细
                detail_cols = ["类别", "商品", "单位", "数量", "单价", "金额", "页码"]
                detail_cols = [c for c in detail_cols if c in df_inv.columns]
                st.dataframe(
                    df_inv[detail_cols].reset_index(drop=True),
                    use_container_width=True
                )

                # ---------- 本发票内的「关键词」小结 ----------
                # 只有在输入了搜索关键词时才显示
                if query_text.strip():
                    inv_stats = df_inv.copy()

                    # 总数量
                    if "数量" in inv_stats.columns:
                        inv_total_qty = pd.to_numeric(inv_stats["数量"], errors="coerce").sum()
                    else:
                        inv_total_qty = None

                    # 总金额
                    if "金额" in inv_stats.columns:
                        inv_total_amt = pd.to_numeric(inv_stats["金额"], errors="coerce").sum()
                    else:
                        inv_total_amt = None

                    # 尝试识别单位（如果这一张发票里只有一个单位，就显示出来）
                    if "单位" in inv_stats.columns:
                        units_inv = inv_stats["单位"].dropna().unique()
                        inv_unit_label = units_inv[0] if len(units_inv) == 1 else ""
                    else:
                        inv_unit_label = ""
                    inv_qty_suffix = f" {inv_unit_label}" if inv_unit_label else ""

                    # 只有当有数量或金额时才显示小结
                    if (inv_total_qty is not None) or (inv_total_amt is not None):
                        
                        st.caption(f"本发票中「{query_text}」小结：")
                        if inv_total_qty is not None:
                            st.write(f"- 总数量：{inv_total_qty:.2f}{inv_qty_suffix}")
                        if inv_total_amt is not None:
                            st.write(f"- 总金额：{inv_total_amt:.2f} 元")
                            

else:
    st.info("请先上传发票并点击按钮进行解析。")

st.markdown("---")

st.caption(f"需要新增功能请联系小何")
