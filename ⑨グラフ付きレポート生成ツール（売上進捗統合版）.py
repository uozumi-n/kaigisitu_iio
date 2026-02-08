# @title ⑨グラフ付きのレポート生成ツール（前月データ補完強化版・⑧売上進捗統合）
# ==========================================
# 1. システム準備
# ==========================================
print("--- [1/5] システム準備中 ---")
!pip install plotly -q

import pandas as pd
import json
import io
import unicodedata
import datetime
from google.colab import files
import sys
import re
import os

# 数値クレンジング（円、カンマ除去）
def clean_num(series):
    return pd.to_numeric(series.astype(str).str.replace('円', '').str.replace(',', ''), errors='coerce').fillna(0)

# 【重要】文字コードの揺れ（濁点問題）を完全に解消する関数
def fix_text(text):
    if pd.isna(text): return ""
    # NFC形式に統一、スペース除去、全角を半角へ
    t = unicodedata.normalize('NFC', str(text)).strip().replace(" ", "").replace("　", "")
    return t

# 媒体名を確実に判定する関数
def get_media_name(m):
    m = fix_text(m)
    if "いいアプリ" in m or "会議室" in m: return "いいアプリ"
    if "インスタ" in m: return "インスタベース"
    if "スペイシー" in m: return "スペイシー"
    if "スペースマーケット" in m or "スペマ" in m: return "スペースマーケット"
    return m

# ==========================================
# 2. ファイルアップロード
# ==========================================
print("\n" + "="*60)
print(" 【重要】以下の4つのCSVファイルをアップロードしてください。")
print(" 1. 会議室シート_改 - 全体元データ.csv")
print(" 2. 会議室シート_改 - グラフの順番.csv")
print(" 3. 会議室シート_改 - タグ設置リスト.csv")
print(" 4. いいアプリ売上データ置き場 - 連携.csv")
print("="*60 + "\n")

uploaded = files.upload()

def find_file(keyword):
    # ファイル名自体も正規化して検索
    for name in uploaded.keys():
        if keyword in unicodedata.normalize('NFC', name): return name
    return None

data_f = find_file("全体元データ")
order_f = find_file("グラフの順番")
tags_f = find_file("タグ設置リスト")
store_f = find_file("連携")

if not all([data_f, order_f, tags_f, store_f]):
    print(f"\n【エラー】ファイルが見つかりません。 (検出状況: 元データ={data_f}, 順番={order_f}, タグ={tags_f}, 連携={store_f})")
    sys.exit()

# ==========================================
# 3. データの集計・同期・補完
# ==========================================
def load_csv(name):
    for enc in ['utf-8-sig', 'shift-jis', 'cp932', 'utf-8']:
        try: return pd.read_csv(io.BytesIO(uploaded[name]), encoding=enc)
        except: continue
    return pd.read_csv(io.BytesIO(uploaded[name]))

df_main = load_csv(data_f)
df_order = load_csv(order_f)
df_tags = load_csv(tags_f)
df_store = load_csv(store_f)

# --- 全データの超強力正規化 ---
print("-> データの文字コードを最適化中...")
df_main['統一店名'] = df_main['統一店名'].apply(fix_text)
df_main['統一設備名'] = df_main['統一設備名'].apply(fix_text)
df_main['媒体名'] = df_main['媒体名'].apply(get_media_name)

df_order.iloc[:, 0] = df_order.iloc[:, 0].apply(fix_text)
df_tags['統一施設名'] = df_tags['統一施設名'].apply(fix_text)
df_tags['媒体側施設名'] = df_tags['媒体側施設名'].apply(fix_text)

df_store['店名'] = df_store['店名'].apply(fix_text)

# 日付処理
df_main['dt'] = pd.to_datetime(df_main['日付'].astype(str).str.replace('/', '-'), errors='coerce')
df_main['月次タグ'] = df_main['dt'].dt.strftime('%Y年%m月')
cutoff_dt = df_main['dt'].max()
current_month_label = cutoff_dt.strftime('%Y年%m月')

# 連携データの日付同期
df_store['dt'] = pd.to_datetime(df_store['日付'], errors='coerce')
df_store = df_store[df_store['dt'] <= cutoff_dt].copy()
df_store['月次'] = df_store['dt'].dt.strftime('%Y年%m月')

# 名寄せ（連携データの店名を統一施設名に変換）
mapping = dict(zip(df_tags['媒体側施設名'], df_tags['統一施設名']))
df_store['統一店名'] = df_store['店名'].map(mapping).fillna(df_store['店名'])

print(f"-> 基準日: {cutoff_dt.strftime('%Y/%m/%d')} (当月: {current_month_label})")

store_cols = ['月額契約', '分配金', 'その他', '従量課金', '設備予約', '外部媒体売上']
for c in store_cols: df_store[c] = clean_num(df_store[c])
df_store_monthly = df_store.groupby(['月次', '統一店名'])[store_cols].sum().reset_index()

# カレンダー作成 (13ヶ月分)
months_13_dt = [(cutoff_dt.replace(day=1) - pd.DateOffset(months=i)) for i in range(12, -1, -1)]
month_labels = [d.strftime('%Y年%m月') for d in months_13_dt]
# 前月ラベルの取得
previous_month_label = month_labels[-2] if len(month_labels) >= 2 else None

def calc_pct(curr, prev):
    if prev == 0: return "-" if curr == 0 else "新規"
    return f"{int((curr / prev) * 100)}%"

df_main['売上'] = clean_num(df_main['売上'])
df_main['件数'] = clean_num(df_main['件数'])
all_media = ['いいアプリ', 'インスタベース', 'スペイシー', 'スペースマーケット']
ordered_stores = df_order.iloc[:, 0].dropna().unique().tolist()

# ------------------------------------------
# データ抽出コアエンジン
# ------------------------------------------
def get_data(s_name=None, f_name=None, mode="linkage", is_global=False):
    res_list = []
    for label in month_labels:
        row = {"month": label}
        if mode == "original":
            # 元データ参照（会議室予約）
            if is_global:
                m_data = df_main[df_main['月次タグ']==label]
            elif f_name:
                m_data = df_main[(df_main['統一店名']==s_name) & (df_main['統一設備名']==f_name) & (df_main['月次タグ']==label)]
            else:
                m_data = df_main[(df_main['統一店名']==s_name) & (df_main['月次タグ']==label)]

            if f_name:
                row.update({"total_sales": int(m_data['売上'].sum()), "total_counts": int(m_data['件数'].sum())})
                for m in all_media:
                    row[f"s_{m}"] = int(m_data[m_data['媒体名']==m]['売上'].sum())
                    row[f"c_{m}"] = int(m_data[m_data['媒体名']==m]['件数'].sum())
            else:
                for m in all_media: row[m] = int(m_data[m_data['媒体名']==m]['売上'].sum())
                row["total_sales"] = sum([row[m] for m in all_media])
        else:
            # 連携データ参照（店舗全体実績：当月・前月自動補完ロジック）
            if is_global:
                g_df = df_store_monthly[df_store_monthly['月次']==label]
                base = g_df[store_cols].sum().to_dict()

                # 最新月の補完 (いいアプリ予約・外部予約を元データから常に反映)
                if label == current_month_label:
                    m_data = df_main[df_main['月次タグ']==label]
                    base['設備予約'] = int(m_data[m_data['媒体名']=='いいアプリ']['売上'].sum())
                    base['外部媒体売上'] = int(m_data[m_data['媒体名']!='いいアプリ']['売上'].sum())

                # 前月分の外部媒体売上が0の場合の補完
                elif label == previous_month_label and base.get('外部媒体売上', 0) == 0:
                    m_data = df_main[df_main['月次タグ']==label]
                    base['外部媒体売上'] = int(m_data[m_data['媒体名']!='いいアプリ']['売上'].sum())

                row.update({c: int(base.get(c, 0)) for c in store_cols})
            else:
                s_df = df_store_monthly[(df_store_monthly['統一店名']==s_name) & (df_store_monthly['月次']==label)]
                base = s_df.iloc[0].to_dict() if not s_df.empty else {c: 0 for c in store_cols}

                # 最新月の補完
                if label == current_month_label:
                    m_data = df_main[(df_main['統一店名']==s_name) & (df_main['月次タグ']==label)]
                    base['設備予約'] = int(m_data[m_data['媒体名']=='いいアプリ']['売上'].sum())
                    base['外部媒体売上'] = int(m_data[m_data['媒体名']!='いいアプリ']['売上'].sum())

                # 前月分の外部媒体売上が0の場合の補完
                elif label == previous_month_label and base.get('外部媒体売上', 0) == 0:
                    m_data = df_main[(df_main['統一店名']==s_name) & (df_main['月次タグ']==label)]
                    base['外部媒体売上'] = int(m_data[m_data['媒体名']!='いいアプリ']['売上'].sum())

                row.update({c: int(base[c]) for c in store_cols})
            row["total_sales"] = sum([row[c] for c in store_cols])
        res_list.append(row)

    # 前月比
    mom_cols = ['従量課金', '設備予約', '外部媒体売上'] if mode=="linkage" else all_media
    for i in range(len(res_list)):
        curr, prev = res_list[i], (res_list[i-1] if i > 0 else None)
        if mode == "original": curr["total_sales_mom"] = calc_pct(curr["total_sales"], prev["total_sales"] if prev else 0)
        for c in mom_cols:
            k = f"s_{c}" if f_name else c
            curr[f"{k}_mom"] = calc_pct(curr[k], prev[k] if prev else 0)
    return res_list

# ------------------------------------------
# 5日刻み売上進捗データの生成 (⑧統合)
# ------------------------------------------
print("-> 5日刻み売上進捗データを生成中...")

# いいアプリ(内部)と外部媒体を日別・店舗別に集計
_is_internal = df_main['媒体名'] == 'いいアプリ'
daily_room = df_main.groupby(['dt', '統一店名', _is_internal.rename('is_int')])['売上'].sum().unstack(fill_value=0).reset_index()
if True not in daily_room.columns: daily_room[True] = 0
if False not in daily_room.columns: daily_room[False] = 0
daily_room.rename(columns={True: '設備予約_内部', False: '外部媒体売上_部屋'}, inplace=True)

# 連携データからベース売上(月額契約・分配金・その他・従量課金)を日別集計
base_cols_prog = ['従量課金', '月額契約', '分配金', 'その他']
df_store_prog = df_store.copy()
for c in base_cols_prog:
    if c not in df_store_prog.columns: df_store_prog[c] = 0
df_store_prog['ベース売上'] = df_store_prog[base_cols_prog].sum(axis=1)
daily_base = df_store_prog.groupby(['dt', '統一店名'])['ベース売上'].sum().reset_index()

# 結合して当月データに限定
df_prog = pd.merge(daily_base, daily_room, on=['dt', '統一店名'], how='outer').fillna(0)
current_ym = cutoff_dt.strftime('%Y-%m')
df_prog_cur = df_prog[df_prog['dt'].dt.strftime('%Y-%m') == current_ym].copy()

# マイルストーン日付(5日, 10日, 15日, 20日, 25日, 月末 + 最新日)
month_start = cutoff_dt.replace(day=1)
full_dates = pd.date_range(month_start, cutoff_dt, freq='D')
milestone_dates = sorted(set(
    [d for d in full_dates if d.day in [5, 10, 15, 20, 25] or d.is_month_end]
    + [cutoff_dt]
))

# 全店合計の進捗
all_daily_p = df_prog_cur.groupby('dt').agg(
    {'ベース売上':'sum', '設備予約_内部':'sum', '外部媒体売上_部屋':'sum'}
).reindex(full_dates, fill_value=0)
all_cum_st = (all_daily_p['ベース売上'] + all_daily_p['設備予約_内部']).cumsum()
all_cum_ex = all_daily_p['外部媒体売上_部屋'].cumsum()
progress_all_store = [int(all_cum_st.loc[d]) for d in milestone_dates]
progress_all_ext = [int(all_cum_ex.loc[d]) for d in milestone_dates]

# 店舗別の進捗
progress_stores = []
for sname in ordered_stores:
    s_data = df_prog_cur[df_prog_cur['統一店名'] == sname]
    s_agg = s_data.groupby('dt').agg(
        {'ベース売上':'sum', '設備予約_内部':'sum', '外部媒体売上_部屋':'sum'}
    ).reindex(full_dates, fill_value=0)
    s_cum_st = (s_agg['ベース売上'] + s_agg['設備予約_内部']).cumsum()
    s_cum_ex = s_agg['外部媒体売上_部屋'].cumsum()
    progress_stores.append({
        'name': sname,
        'store_total': [int(s_cum_st.loc[d]) for d in milestone_dates],
        'external': [int(s_cum_ex.loc[d]) for d in milestone_dates]
    })
progress_milestones = [d.strftime('%m/%d') for d in milestone_dates]

# ==========================================
# 4. JSON構築
# ==========================================
print("\n--- [4/5] ダッシュボードを構築中 ---")
js_master = {
    "g_all": get_data(is_global=True, mode="linkage"),
    "g_room": get_data(is_global=True, mode="original"),
    "stores": []
}
for idx, sname in enumerate(ordered_stores):
    s_main_rows = df_main[df_main['統一店名'] == sname]
    sorted_facs = s_main_rows.groupby('統一設備名')['売上'].sum().sort_values(ascending=False).index.tolist()
    fac_data = [{"name": f, "data": get_data(sname, f, mode="original")} for f in sorted_facs]
    js_master["stores"].append({
        "id": f"st_{idx}", "name": sname,
        "summary": get_data(sname, mode="linkage"),
        "original_summary": get_data(sname, mode="original"),
        "facilities": fac_data
    })
js_master["progress"] = {
    "month": current_month_label,
    "milestones": progress_milestones,
    "all": {"store_total": progress_all_store, "external": progress_all_ext},
    "stores": progress_stores
}

# ==========================================
# 5. HTML生成
# ==========================================
json_data_str = json.dumps(js_master, ensure_ascii=False)
media_list_str = json.dumps(all_media, ensure_ascii=False)

html_template = """
<!DOCTYPE html><html><head><meta charset="UTF-8"><title>稼働実績ダッシュボード</title>
<script src="https://cdn.plot.ly/plotly-2.24.1.min.js"></script>
<style>
:root { --primary: #22923f; --primary-light: #e8f8ed; --bg: #f8f9fa; --up: #e74c3c; --down: #2980b9; }
body { font-family: sans-serif; margin: 0; display: flex; background: var(--bg); }
nav { width: 260px; background: #2f3640; height: 100vh; position: sticky; top: 0; overflow-y: auto; color: white; z-index: 1000; flex-shrink: 0; }
nav h3 { padding: 20px; background: #222f3e; margin: 0; font-size: 1rem; }
nav ul { list-style: none; padding: 0; }
nav li a { display: block; padding: 12px 20px; color: #ced6e0; text-decoration: none; border-bottom: 1px solid #34495e; font-size: 0.82rem; }
nav li a:hover { background: var(--primary); color: white; }
.title-bar { position: sticky; top: 0; background: white; padding: 10px 25px; border-bottom: 3px solid var(--primary); box-shadow: 0 2px 5px rgba(0,0,0,0.08); z-index: 900; display: flex; justify-content: space-between; align-items: center; }
.title-bar h2 { margin: 0; font-size: 1.2rem; color: var(--primary); }
main { flex: 1; min-width: 0; }
.section { padding: 10px 25px 80px; scroll-margin-top: 10px; }
.card { background: white; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.05); margin-bottom: 35px; border: 1px solid #dfe6e9; overflow: hidden; }
.card-h { background: #f8f9fa; padding: 12px 20px; font-weight: bold; border-bottom: 1px solid #dfe6e9; }
.card-b { padding: 20px; }
.table-c { overflow-x: auto; border-radius: 8px; border: 1px solid #dfe6e9; margin-top: 15px; }
table { width: 100%; border-collapse: collapse; font-size: 0.74rem; background: white; }
th, td { border: 1px solid #eee; padding: 6px; text-align: right; }
th { background: #f1f2f6; text-align: center; white-space: nowrap; }
td:first-child { text-align: center; font-weight: bold; background: #fafafa; position: sticky; left: 0; }
.mom { font-weight: bold; font-size: 0.7rem; }
.mom.up { color: var(--up); }
.mom.down { color: var(--down); }
.tot { background: var(--primary-light); font-weight: bold; }
</style></head><body>
<nav><h3>実績メニュー</h3><ul id="nav-ul"></ul></nav>
<main id="main-c"></main>
<script>
const data = """ + json_data_str + """;
const media = """ + media_list_str + """;
const content = document.getElementById('main-c');
const nav = document.getElementById('nav-ul');

let nHtml = '<li><a href="#g-all">全店舗合計 (総合)</a></li><li><a href="#g-room">全店舗合計 (会議室のみ)</a></li><li><a href="#progress" style="color:#f1c40f;font-weight:bold">5日刻み売上進捗</a></li>';
data.stores.forEach(s => { nHtml += `<li><a href="#${s.id}">${s.name}</a></li>`; });
nav.innerHTML = nHtml;

function getMomClass(m) { if(!m || m==="-" || m==="新規") return ""; const v = parseInt(m.replace('%','')); return v > 100 ? "up" : (v < 100 ? "down" : ""); }

function getStoreTbl(rows) {
    const cats = ['月額契約', '分配金', 'その他', '従量課金', '設備予約', '外部媒体売上'];
    const momCats = ['従量課金', '設備予約', '外部媒体売上'];
    let h = '<div class="table-c"><table><thead><tr><th>月次</th><th style="background:#e8f8ed">合計売上</th>';
    cats.forEach(c => { h += (momCats.includes(c)) ? `<th colspan="2">${c}</th>` : `<th>${c}</th>`; });
    h += '</tr></thead><tbody>';
    rows.forEach(r => {
        h += `<tr><td>${r.month}</td><td class="tot">${r.total_sales.toLocaleString()}</td>`;
        cats.forEach(c => {
            h += `<td>${(r[c]||0).toLocaleString()}</td>`;
            if(momCats.includes(c)) { let m = r[c+"_mom"] || "-"; h += `<td class="mom ${getMomClass(m)}">${m}</td>`; }
        });
        h += '</tr>';
    });
    return h + '</tbody></table></div>';
}

function getOriginalTbl(rows, cats) {
    let h = '<div class="table-c"><table><thead><tr><th>月次</th><th style="background:#e8f8ed">合計売上</th><th style="background:#e8f8ed">前月比</th>';
    cats.forEach(c => h += `<th>${c}</th>`);
    h += '</tr></thead><tbody>';
    rows.forEach(r => {
        h += `<tr><td>${r.month}</td><td class="tot">${r.total_sales.toLocaleString()}</td><td class="mom ${getMomClass(r.total_sales_mom)}">${r.total_sales_mom}</td>`;
        cats.forEach(c => h += `<td>${(r[c]||0).toLocaleString()}</td>`);
        h += '</tr>';
    });
    return h + '</tbody></table></div>';
}

function getFacTbl(rows) {
    let h = '<div class="table-c"><table><thead><tr><th rowspan="2">月次</th><th colspan="3" style="background:#e8f8ed">施設全体</th>';
    media.forEach(m => h += `<th colspan="3">${m}</th>`);
    h += '</tr><tr><th style="background:#f1f2f6">売上</th><th style="background:#f1f2f6">前月比</th><th style="background:#f1f2f6">件数</th>';
    media.forEach(m => h += `<th>売上</th><th>前月比</th><th>件数</th>`);
    h += '</tr></thead><tbody>';
    rows.forEach(r => {
        h += `<tr><td>${r.month}</td><td class="tot">${r.total_sales.toLocaleString()}</td><td class="mom ${getMomClass(r.total_sales_mom)}">${r.total_sales_mom}</td><td>${r.total_counts.toLocaleString()}</td>`;
        media.forEach(m => {
            let m_mom = r['s_'+m+'_mom'] || "-";
            h += `<td>${(r['s_'+m]||0).toLocaleString()}</td><td class="mom ${getMomClass(m_mom)}">${m_mom}</td><td>${(r['c_'+m]||0).toLocaleString()}</td>`;
        });
        h += '</tr>';
    });
    return h + '</tbody></table></div>';
}

let sHtml = `<section id="g-all" class="section"><div class="title-bar"><h2>📊 全店舗合計 (総合)</h2><span>基準日: """ + cutoff_dt.strftime('%Y/%m/%d') + """</span></div><div class="card"><div class="card-h">全社実績 (連携データベース)</div><div class="card-b"><div id="c-gall" style="height:400px;"></div><div id="t-gall"></div></div></div></section>
<section id="g-room" class="section"><div class="title-bar"><h2>💼 全店舗合計 (会議室のみ)</h2></div><div class="card"><div class="card-h">全社 会議室予約内訳 (全体元データベース)</div><div class="card-b"><div id="c-groom" style="height:400px;"></div><div id="t-groom"></div></div></div></section>
<section id="progress" class="section"><div class="title-bar"><h2>📈 ${data.progress.month} 5日刻み売上進捗</h2><span>基準日: """ + cutoff_dt.strftime('%Y/%m/%d') + """</span></div>
<div class="card"><div class="card-h">全店舗 売上進捗推移（累計）</div><div class="card-b"><div id="c-prog-line" style="height:450px;"></div></div></div>
<div class="card"><div class="card-h">店舗別 最新売上内訳</div><div class="card-b"><div id="c-prog-bar" style="height:400px;"></div></div></div>
<div class="card"><div class="card-h">5日刻み売上進捗テーブル</div><div class="card-b"><div id="t-prog"></div></div></div>
</section>`;

data.stores.forEach(s => {
    sHtml += `<section id="${s.id}" class="section"><div class="title-bar"><h2>🏠 ${s.name}</h2><span>基準日: """ + cutoff_dt.strftime('%Y/%m/%d') + """</span></div>
        <div class="card"><div class="card-h">① 店舗全体実績 (連携データベース)</div><div class="card-b"><div id="c-s-${s.id}" style="height:400px;"></div><div id="t-s-${s.id}"></div></div></div>
        <div class="card"><div class="card-h">② 会議室予約合計 (全体元データベース)</div><div class="card-b"><div id="c-sori-${s.id}" style="height:400px;"></div><div id="t-sori-${s.id}"></div></div></div>`;
    s.facilities.forEach((f, i) => {
        sHtml += `<div class="card"><div class="card-h">【設備】${f.name} (全体元データ参照)</div><div class="card-b"><div style="display:flex; flex-wrap:wrap; gap:15px;"><div id="c-fs-${s.id}-${i}" style="flex:1; min-width:320px; height:380px;"></div><div id="c-fc-${s.id}-${i}" style="flex:1; min-width:320px; height:380px;"></div></div><div id="t-f-${s.id}-${i}"></div></div></div>`;
    });
    sHtml += '</section>';
});
content.innerHTML = sHtml;

const rendered = new Set();
function render(id) {
    if(rendered.has(id)) return;
    const cfg = { responsive:true, displayModeBar:false };
    const lay = { barmode:'stack', margin:{t:40,b:40,l:60,r:15}, font:{size:11}, legend:{orientation:'h',y:-0.15}, colorway:['#22923f','#3498db','#9b59b6','#f1c40f','#e67e22','#e74c3c'] };
    const s_cats = ['月額契約', '分配金', 'その他', '従量課金', '設備予約', '外部媒体売上'];

    if(id==='g-all'){
        Plotly.newPlot('c-gall', s_cats.map(c=>({x:data.g_all.map(r=>r.month),y:data.g_all.map(r=>r[c]),name:c,type:'bar'})).filter(t=>t.y.some(v=>v>0)), lay, cfg);
        document.getElementById('t-gall').innerHTML = getStoreTbl(data.g_all);
    } else if(id==='g-room'){
        Plotly.newPlot('c-groom', media.map(c=>({x:data.g_room.map(r=>r.month),y:data.g_room.map(r=>r[c]),name:c,type:'bar'})).filter(t=>t.y.some(v=>v>0)), lay, cfg);
        document.getElementById('t-groom').innerHTML = getOriginalTbl(data.g_room, media);
    } else if(id==='progress'){
        const pg = data.progress;
        const ms = pg.milestones;
        const pLay = { margin:{t:50,b:60,l:80,r:15}, font:{size:11}, legend:{orientation:'h',y:-0.2}, colorway:['#2c3e50','#22923f','#3498db','#9b59b6','#f1c40f','#e67e22','#e74c3c','#1abc9c','#e91e63','#607d8b','#795548','#00bcd4','#ff5722','#4caf50','#673ab7','#ff9800','#009688','#f44336','#3f51b5','#cddc39'] };
        // 折れ線グラフ：店舗別の月間売上累計推移
        const lineTraces = [{x:ms,y:pg.all.store_total.map((v,i)=>v+pg.all.external[i]),name:'全店合計',type:'scatter',mode:'lines+markers',line:{width:3,dash:'dash',color:'#000'}}];
        pg.stores.forEach(s=>{lineTraces.push({x:ms,y:s.store_total.map((v,i)=>v+s.external[i]),name:s.name,type:'scatter',mode:'lines+markers'});});
        Plotly.newPlot('c-prog-line',lineTraces,{...pLay,title:pg.month+' 月間売上累計推移（5日刻み）',xaxis:{title:'日付'},yaxis:{title:'累計売上（円）'}},cfg);
        // 積み上げ棒グラフ：最新時点の店舗別内訳
        const li = ms.length-1;
        const sNames = pg.stores.map(s=>s.name);
        Plotly.newPlot('c-prog-bar',[
            {x:sNames,y:pg.stores.map(s=>s.store_total[li]),name:'店舗全体売上',type:'bar',marker:{color:'#22923f'}},
            {x:sNames,y:pg.stores.map(s=>s.external[li]),name:'外部媒体売上',type:'bar',marker:{color:'#3498db'}}
        ],{...pLay,barmode:'stack',title:'最新時点 ('+ms[li]+') 店舗別売上内訳'},cfg);
        // テーブル
        let tbl='<div class="table-c"><table><thead><tr><th rowspan="2" style="min-width:120px;position:sticky;left:0;background:#f1f2f6;z-index:1">店舗名</th>';
        ms.forEach(m=>{tbl+=`<th colspan="3" style="background:#e8f8ed">~${m}</th>`;});
        tbl+='</tr><tr>';
        ms.forEach(()=>{tbl+='<th>店舗全体</th><th>外部媒体</th><th style="background:#ffeaa7">合計</th>';});
        tbl+='</tr></thead><tbody>';
        tbl+='<tr style="background:#e8f8ed;font-weight:bold"><td style="position:sticky;left:0;background:#e8f8ed;z-index:1">全店合計</td>';
        ms.forEach((_,i)=>{const st=pg.all.store_total[i],ex=pg.all.external[i];tbl+=`<td>${st.toLocaleString()}</td><td>${ex.toLocaleString()}</td><td style="background:#ffeaa7;font-weight:bold">${(st+ex).toLocaleString()}</td>`;});
        tbl+='</tr>';
        pg.stores.forEach(s=>{
            tbl+=`<tr><td style="position:sticky;left:0;background:#fafafa;z-index:1">${s.name}</td>`;
            ms.forEach((_,i)=>{const st=s.store_total[i],ex=s.external[i];tbl+=`<td>${st.toLocaleString()}</td><td>${ex.toLocaleString()}</td><td style="background:#ffeaa7">${(st+ex).toLocaleString()}</td>`;});
            tbl+='</tr>';
        });
        tbl+='</tbody></table></div>';
        document.getElementById('t-prog').innerHTML=tbl;
    } else {
        const s = data.stores.find(x => x.id === id); if(!s) return;
        Plotly.newPlot('c-s-'+id, s_cats.map(c=>({x:s.summary.map(r=>r.month),y:s.summary.map(r=>r[c]),name:c,type:'bar'})).filter(t=>t.y.some(v=>v>0)), lay, cfg);
        document.getElementById('t-s-'+id).innerHTML = getStoreTbl(s.summary);
        Plotly.newPlot('c-sori-'+id, media.map(c=>({x:s.original_summary.map(r=>r.month),y:s.original_summary.map(r=>r[c]),name:c,type:'bar'})).filter(t=>t.y.some(v=>v>0)), lay, cfg);
        document.getElementById('t-sori-'+id).innerHTML = getOriginalTbl(s.original_summary, media);
        s.facilities.forEach((f,i)=>{
            Plotly.newPlot(`c-fs-${id}-${i}`, media.map(m=>({x:f.data.map(r=>r.month),y:f.data.map(r=>r["s_"+m]),name:m,type:'bar'})).filter(t=>t.y.some(v=>v>0)), {...lay, title:'売上推移'}, cfg);
            Plotly.newPlot(`c-fc-${id}-${i}`, media.map(m=>({x:f.data.map(r=>r.month),y:f.data.map(r=>r["c_"+m]),name:m,type:'bar'})).filter(t=>t.y.some(v=>v>0)), {...lay, title:'件数推移'}, cfg);
            document.getElementById(`t-f-${id}-${i}`).innerHTML = getFacTbl(f.data);
        });
    }
    rendered.add(id);
}
const obs = new IntersectionObserver((es)=>{ es.forEach(e=>{ if(e.isIntersecting) render(e.target.id); }); }, {threshold:0.05});
document.querySelectorAll('.section').forEach(s=>obs.observe(s));
</script></body></html>
"""

filename = f"稼働実績ダッシュボード_{cutoff_dt.strftime('%Y%m%d')}.html"
with open(filename, "w", encoding="utf-8") as f: f.write(html_template)
print(f"\n--- [5/5] 完了！ ---")
files.download(filename)
