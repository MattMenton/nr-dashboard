"""
NetRevenue Weekly Sales Dashboard Generator
============================================
Generates a password-protected HTML dashboard per director,
hosted as static files (GitHub Pages, Netlify, etc.).

Zero Slack required. Directors bookmark their URL and check it Monday morning.

Setup:
    pip install requests

Run manually:
    python generate_dashboard.py

Auto-runs via GitHub Actions every Monday 7am SAST — see .github/workflows/weekly.yml
"""

import requests
import os
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─── CONFIG — only edit this section ──────────────────────────────────────────

SALESVUE_API_KEY  = "sv_04229c6b0aa919badbea7a2d75ef9d60"
SALESVUE_BASE_URL = "REPLACE_WITH_SALESVUE_BASE_URL"
MATT_EMAIL        = "matthew@netrevenue.io"

DIRECTORS = {
    "Ben Prevost":     {"sv_id": "qKEzVMFbsc",           "password": "apollo-ben",    "filename": "ben.html"},
    "Jake Solomon":    {"sv_id": "yHloCubOwrUY_w2SwXUd", "password": "apollo-jake",   "filename": "jake.html"},
    "Jon Estes":       {"sv_id": "RF_vb9UBieKXW1Z6Ed6G", "password": "apollo-jon",    "filename": "jon.html"},
    "Travis Chesinski":{"sv_id": "vHVyxIMEld",           "password": "apollo-travis", "filename": "travis.html"},
}
DIRECTOR_BY_SV_ID = {v["sv_id"]: k for k, v in DIRECTORS.items()}

RED_REVENUE_DROP    = -0.30
YELLOW_REVENUE_DROP = -0.10
RED_SHOW_RATE       = 0.25
YELLOW_SHOW_RATE    = 0.35
RED_CLOSE_RATE      = 0.15
YELLOW_CLOSE_RATE   = 0.20

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "docs")

def get_previous_week():
    today = date.today()
    days_since_sunday = (today.weekday() + 1) % 7
    last_sunday = today - timedelta(days=days_since_sunday if days_since_sunday else 7)
    last_monday = last_sunday - timedelta(days=6)
    return last_monday.strftime("%Y-%m-%d"), last_sunday.strftime("%Y-%m-%d")

class SalesVueClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {SALESVUE_API_KEY}", "Content-Type": "application/json"})

    def _get(self, endpoint, params=None):
        r = self.session.get(f"{SALESVUE_BASE_URL.rstrip('/')}/{endpoint.lstrip('/')}", params=params, timeout=15)
        r.raise_for_status()
        return r.json()

    def get_placements(self):
        return self._get("/placements").get("data", {}).get("placements", [])

    def get_revenue_summary(self, account_id, start, end):
        return self._get("/revenue/summary", {"account_id": account_id, "start_date": start, "end_date": end}).get("data", {})

    def get_appointment_summary(self, account_id, start, end):
        return self._get("/appointments/summary", {"account_id": account_id, "start_date": start, "end_date": end}).get("data", {})

def pct_change(current, previous):
    try:
        c, p = float(current), float(previous)
        return (c - p) / p if p != 0 else 0.0
    except: return 0.0

def fmt_currency(v):
    try:
        v = float(v)
        if v >= 1_000_000: return f"${v/1_000_000:.1f}M"
        if v >= 1_000: return f"${v/1_000:.0f}K"
        return f"${v:.0f}"
    except: return "$0"

def fmt_pct(v):
    try: return f"{float(v)*100:.1f}%"
    except: return "0.0%"

def fmt_change(pct):
    sign = "↑" if pct > 0 else ("↓" if pct < 0 else "—")
    cls  = "up" if pct > 0 else ("down" if pct < 0 else "flat")
    return {"text": f"{sign} {abs(pct)*100:.1f}% WoW", "cls": cls}

def traffic_light(rev_chg, show_rate, close_rate, refunds):
    if rev_chg <= RED_REVENUE_DROP or show_rate < RED_SHOW_RATE or close_rate < RED_CLOSE_RATE or refunds > 0: return "red"
    if rev_chg <= YELLOW_REVENUE_DROP or show_rate < YELLOW_SHOW_RATE or close_rate < YELLOW_CLOSE_RATE: return "yellow"
    return "green"

def flag_message(status, rev_chg, show_rate, close_rate, refunds, bookings_chg):
    if status == "red":
        parts = []
        if refunds > 0: parts.append(f"Refunds: {fmt_currency(refunds)}")
        if rev_chg <= RED_REVENUE_DROP: parts.append(f"Revenue down {abs(rev_chg)*100:.0f}%")
        if close_rate < RED_CLOSE_RATE: parts.append(f"Close rate {fmt_pct(close_rate)}")
        if show_rate < RED_SHOW_RATE: parts.append(f"Show rate {fmt_pct(show_rate)}")
        return "🔴 " + " · ".join(parts)
    if status == "yellow":
        parts = []
        if rev_chg <= YELLOW_REVENUE_DROP: parts.append(f"Revenue down {abs(rev_chg)*100:.0f}%")
        if bookings_chg <= -0.20: parts.append(f"Bookings down {abs(bookings_chg)*100:.0f}%")
        if show_rate < YELLOW_SHOW_RATE: parts.append(f"Show rate {fmt_pct(show_rate)}")
        return "⚠ " + " · ".join(parts) if parts else "⚠ Watch this account"
    return "✓ Clean week"

def build_account_data(placement, rev, appt):
    cash = float(rev.get("total_cash", 0))
    prev_cash = float(rev.get("previous_period", {}).get("total_cash", 0))
    deals = int(rev.get("deals_closed", 0))
    prev_deals = int(rev.get("previous_period", {}).get("deals_closed", 0))
    refunds = float(rev.get("refunds", 0))
    prev_refunds = float(rev.get("previous_period", {}).get("refunds", 0))
    pay_plans = float(rev.get("payment_plans", 0))
    booked = int(appt.get("total_booked", 0))
    prev_booked = int(appt.get("previous_period", {}).get("total_booked", 0))
    took = int(appt.get("total_taken", 0))
    show_rate = float(appt.get("show_rate", 0))
    close_rate = (deals / took) if took > 0 else 0.0
    avg_deal = (cash / deals) if deals > 0 else 0.0
    prev_avg = (prev_cash / prev_deals) if prev_deals > 0 else 0.0
    rev_chg = pct_change(cash, prev_cash)
    deals_chg = pct_change(deals, prev_deals)
    bookings_chg = pct_change(booked, prev_booked)
    avg_chg = pct_change(avg_deal, prev_avg)
    status = traffic_light(rev_chg, show_rate, close_rate, refunds)
    return {"account_id": placement["account_id"], "account_name": placement["account_name"], "cash": cash, "prev_cash": prev_cash, "deals": deals, "prev_deals": prev_deals, "refunds": refunds, "prev_refunds": prev_refunds, "pay_plans": pay_plans, "booked": booked, "prev_booked": prev_booked, "took": took, "show_rate": show_rate, "close_rate": close_rate, "avg_deal": avg_deal, "rev_chg": rev_chg, "deals_chg": deals_chg, "bookings_chg": bookings_chg, "avg_chg": avg_chg, "status": status, "flag": flag_message(status, rev_chg, show_rate, close_rate, refunds, bookings_chg)}

BADGE_LABEL = {"red": "Needs Attention", "yellow": "Watch", "green": "Strong"}

def account_card_html(a):
    s = a["status"]
    r_chg = fmt_change(a["rev_chg"]); d_chg = fmt_change(a["deals_chg"]); b_chg = fmt_change(a["bookings_chg"]); av_chg = fmt_change(a["avg_chg"])
    show_cls = "rate-green" if a["show_rate"] >= 0.40 else ("rate-yellow" if a["show_rate"] >= 0.30 else "rate-red")
    close_cls = "rate-green" if a["close_rate"] >= 0.25 else ("rate-yellow" if a["close_rate"] >= 0.15 else "rate-red")
    ref_cls = "rate-red" if a["refunds"] > 0 else "rate-green"
    flag_cls = "" if s == "red" else ("warn" if s == "yellow" else "ok")
    return f'<div class="account-card {s}"><div class="card-header"><div class="card-name">{a["account_name"]}</div><div class="card-badge badge-{s}">{BADGE_LABEL[s]}</div></div><div class="card-metrics"><div class="metric"><div class="metric-label">Total Cash</div><div class="metric-value">{fmt_currency(a["cash"])}</div><div class="metric-sub {r_chg["cls"]}">{r_chg["text"]}</div></div><div class="metric"><div class="metric-label">Deals Closed</div><div class="metric-value">{a["deals"]}</div><div class="metric-sub {d_chg["cls"]}">{d_chg["text"]}</div></div><div class="metric"><div class="metric-label">Avg Deal</div><div class="metric-value">{fmt_currency(a["avg_deal"])}</div><div class="metric-sub {av_chg["cls"]}">{av_chg["text"]}</div></div><div class="metric"><div class="metric-label">Bookings</div><div class="metric-value">{a["booked"]}</div><div class="metric-sub {b_chg["cls"]}">{b_chg["text"]}</div></div></div><div class="card-rates"><div class="rate-item"><div class="rate-label">Show Rate</div><div class="rate-value {show_cls}">{fmt_pct(a["show_rate"])}</div></div><div class="rate-item"><div class="rate-label">Close Rate</div><div class="rate-value {close_cls}">{fmt_pct(a["close_rate"])}</div></div><div class="rate-item"><div class="rate-label">Refunds</div><div class="rate-value {ref_cls}">{fmt_currency(a["refunds"])}</div></div></div><div class="card-flag {flag_cls}">{a["flag"]}</div></div>'

def action_items_html(accounts):
    critical, warnings, opps = [], [], []
    for a in sorted(accounts, key=lambda x: {"red": 0, "yellow": 1, "green": 2}[x["status"]]):
        n = a["account_name"]; rd = abs(a["rev_chg"])*100; bd = abs(a["bookings_chg"])*100; br = a["bookings_chg"]*100
        prev_bk = int(a["booked"]/(1+a["bookings_chg"])) if (1+a["bookings_chg"]) != 0 else 0
        if a["deals"] == 0 and a["booked"] > 0: critical.append((n, f"Zero deals closed despite {a['booked']} bookings. Pipeline stalled — pull all showed calls and find where leads are dropping off.", "Director"))
        if a["refunds"] > 0: critical.append((n, f"{fmt_currency(a['refunds'])} in refunds. Identify which closer(s) triggered chargebacks, review the calls, patch the gap.", "Director"))
        if a["rev_chg"] <= RED_REVENUE_DROP: critical.append((n, f"Revenue down {rd:.0f}% WoW ({fmt_currency(a['cash'])} vs {fmt_currency(a['prev_cash'])}). Emergency closer coaching session needed before Wednesday.", "Director + Closers"))
        if a["close_rate"] < RED_CLOSE_RATE and a["took"] > 2: critical.append((n, f"Close rate at {fmt_pct(a['close_rate'])} — below the 15% floor. Identify reps pulling it down, pull recordings, run individual reviews this week.", "Director"))
        if a["show_rate"] < RED_SHOW_RATE and a["booked"] > 5: critical.append((n, f"Show rate critically low at {fmt_pct(a['show_rate'])}. Audit confirmation sequences in GHL — are reminder texts firing? Is the calendar link live?", "Ops"))
        if YELLOW_REVENUE_DROP < a["rev_chg"] <= -0.10: warnings.append((n, f"Revenue down {rd:.0f}% WoW. Check if this is a volume issue (fewer calls took) or conversion (close rate slipping).", "Director"))
        if a["bookings_chg"] <= -0.25: warnings.append((n, f"Bookings down {bd:.0f}% WoW ({a['booked']} vs {prev_bk} prior). Audit setter dial volume and lead follow-up — leads may be going cold in New Leads stage.", "Setter Team"))
        if YELLOW_SHOW_RATE <= a["show_rate"] < YELLOW_SHOW_RATE+0.05 and a["booked"] > 5: warnings.append((n, f"Show rate at {fmt_pct(a['show_rate'])} — in the watch zone. Review 48hr and 24hr reminder workflows.", "Ops"))
        if YELLOW_CLOSE_RATE <= a["close_rate"] < RED_CLOSE_RATE+0.05 and a["took"] > 2: warnings.append((n, f"Close rate at {fmt_pct(a['close_rate'])} — trending toward the floor. Group close-rate review before end of week.", "Director"))
        if a["deals_chg"] <= -0.30 and a["rev_chg"] > -0.10: warnings.append((n, f"Deal count down {abs(a['deals_chg'])*100:.0f}% but revenue holding — avg deal size jumped. Confirm this is intentional.", "Director"))
        if a["pay_plans"] > 10000: warnings.append((n, f"Payment plan portfolio at {fmt_currency(a['pay_plans'])}. Monitor Stripe for upcoming failed charges.", "Ops"))
        if a["rev_chg"] >= 0.25: opps.append((n, f"Revenue up {a['rev_chg']*100:.0f}% WoW — strong week. Document what drove it as a repeatable playbook.", "Director"))
        if a["show_rate"] >= 0.50 and a["booked"] > 5: opps.append((n, f"Show rate at {fmt_pct(a['show_rate'])} — above 50%. Roll this confirmation sequence out to accounts below 35%.", "Ops"))
        if a["close_rate"] >= 0.35 and a["took"] > 2: opps.append((n, f"Close rate at {fmt_pct(a['close_rate'])} — top-tier. Pull top closer's last 3 calls and build a training doc.", "Director"))
        if a["bookings_chg"] >= 0.20 and a["booked"] > 10: opps.append((n, f"Bookings up {br:.0f}% WoW. Identify what changed in setter workflow and lock it in as SOP.", "Setter Team"))
        if a["avg_chg"] >= 0.20 and a["deals"] > 0: opps.append((n, f"Avg deal size up {a['avg_chg']*100:.0f}% to {fmt_currency(a['avg_deal'])}. Coach the team on what's shifting in the pitch.", "Director"))
        if a["refunds"] == 0 and a["deals"] >= 3 and a.get("prev_refunds", 0) > 0: opps.append((n, f"Zero refunds after {fmt_currency(a.get('prev_refunds',0))} last week. Make whatever changed standard SOP immediately.", "Director"))
    all_items = critical + warnings + opps
    if not all_items: all_items.append(("Portfolio", "Clean week — no flags. Focus on protecting show rates and maintaining zero refunds.", "Team"))
    rows = ""
    for i, (acct, text, owner) in enumerate(all_items[:10], 1):
        rows += f'<div class="action-item"><div class="action-num">{i}</div><div class="action-text"><span class="action-account">{acct}</span> — {text}<span class="action-owner">{owner}</span></div></div>'
    return rows

CSS = """:root{--bg:#0f1117;--surface:#1a1d27;--surface2:#22263a;--border:#2e3348;--text:#e8eaf0;--muted:#7b82a0;--green:#22c55e;--green-bg:rgba(34,197,94,.1);--yellow:#f59e0b;--yellow-bg:rgba(245,158,11,.1);--red:#ef4444;--red-bg:rgba(239,68,68,.1);--blue:#6366f1;--blue-bg:rgba(99,102,241,.12);--accent:#6366f1}*{box-sizing:border-box;margin:0;padding:0}body{background:var(--bg);color:var(--text);font-family:'Inter',-apple-system,sans-serif;font-size:14px;min-height:100vh}.topbar{background:var(--surface);border-bottom:1px solid var(--border);padding:0 32px;display:flex;align-items:center;justify-content:space-between;height:60px;position:sticky;top:0;z-index:100}.logo{font-size:16px;font-weight:700;letter-spacing:-.3px}.logo span{color:var(--accent)}.week-badge{background:var(--blue-bg);color:var(--accent);border:1px solid rgba(99,102,241,.3);border-radius:20px;padding:4px 12px;font-size:12px;font-weight:500}.director-tag{background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:5px 14px;font-size:12px;color:var(--muted);font-weight:500}.main{padding:28px 32px;max-width:1300px;margin:0 auto}.summary-strip{display:grid;grid-template-columns:repeat(5,1fr);gap:16px;margin-bottom:28px}.stat-card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:18px 20px}.stat-label{color:var(--muted);font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.6px;margin-bottom:8px}.stat-value{font-size:22px;font-weight:700;letter-spacing:-.5px}.stat-change{margin-top:6px;font-size:12px;font-weight:500}.up{color:var(--green)}.down{color:var(--red)}.flat{color:var(--muted)}.section-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;margin-top:8px}.section-title{font-size:15px;font-weight:600}.section-meta{font-size:12px;color:var(--muted)}.account-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px;margin-bottom:32px}.account-card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:20px;transition:border-color .15s}.account-card:hover{border-color:#3e4560}.account-card.red{border-left:3px solid var(--red)}.account-card.yellow{border-left:3px solid var(--yellow)}.account-card.green{border-left:3px solid var(--green)}.card-header{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:14px}.card-name{font-size:14px;font-weight:600}.card-badge{font-size:10px;font-weight:600;padding:3px 8px;border-radius:20px;text-transform:uppercase;letter-spacing:.5px}.badge-red{background:var(--red-bg);color:var(--red)}.badge-yellow{background:var(--yellow-bg);color:var(--yellow)}.badge-green{background:var(--green-bg);color:var(--green)}.card-metrics{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px}.metric-label{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:3px}.metric-value{font-size:16px;font-weight:700}.metric-sub{font-size:11px;color:var(--muted);margin-top:2px}.metric-sub.up{color:var(--green)}.metric-sub.down{color:var(--red)}.card-rates{display:flex;border-top:1px solid var(--border);padding-top:12px}.rate-item{flex:1;text-align:center}.rate-item+.rate-item{border-left:1px solid var(--border)}.rate-label{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px;margin-bottom:4px}.rate-value{font-size:14px;font-weight:700}.rate-green{color:var(--green)}.rate-yellow{color:var(--yellow)}.rate-red{color:var(--red)}.card-flag{border-radius:6px;padding:7px 10px;margin-top:10px;font-size:11px;font-weight:500}.account-card.red .card-flag{background:var(--red-bg);color:var(--red)}.account-card.yellow .card-flag{background:var(--yellow-bg);color:var(--yellow)}.account-card.green .card-flag{background:var(--green-bg);color:var(--green)}.actions-section{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:22px 24px;margin-bottom:32px}.actions-title{font-size:14px;font-weight:600;margin-bottom:16px}.actions-title::before{content:'🎯 '}.action-item{display:flex;gap:14px;padding:12px 0;border-bottom:1px solid var(--border);align-items:flex-start}.action-item:last-child{border-bottom:none;padding-bottom:0}.action-num{background:var(--blue-bg);color:var(--accent);border-radius:50%;width:22px;height:22px;font-size:11px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:1px}.action-text{flex:1;line-height:1.5;font-size:13px}.action-account{font-weight:600;color:var(--accent)}.action-owner{display:inline-block;margin-left:8px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;padding:1px 7px;font-size:10px;color:var(--muted);font-weight:500;text-transform:uppercase;letter-spacing:.4px}.footer{text-align:center;color:var(--muted);font-size:11px;padding:24px 0 40px}.login-gate{display:flex;align-items:center;justify-content:center;min-height:100vh;background:var(--bg)}.login-box{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:40px 48px;width:360px;text-align:center}.login-logo{font-size:20px;font-weight:700;margin-bottom:8px}.login-logo span{color:var(--accent)}.login-sub{color:var(--muted);font-size:13px;margin-bottom:28px}.login-input{width:100%;background:var(--surface2);border:1px solid var(--border);color:var(--text);border-radius:8px;padding:10px 14px;font-size:14px;outline:none;margin-bottom:12px}.login-input:focus{border-color:var(--accent)}.login-btn{width:100%;background:var(--accent);color:#fff;border:none;border-radius:8px;padding:10px;font-size:14px;font-weight:600;cursor:pointer}.login-btn:hover{opacity:.9}.login-error{color:var(--red);font-size:12px;margin-top:8px;min-height:18px}"""

def build_page(director_name, accounts, password, week_label, generated_at):
    sorted_accounts = sorted(accounts, key=lambda x: x["cash"], reverse=True)
    total_cash = sum(a["cash"] for a in accounts); total_prev = sum(a["prev_cash"] for a in accounts)
    total_deals = sum(a["deals"] for a in accounts); prev_deals = sum(int(a["deals"]/(1+a["deals_chg"])) if (1+a["deals_chg"]) != 0 else 0 for a in accounts)
    avg_show = sum(a["show_rate"] for a in accounts)/len(accounts) if accounts else 0
    avg_close = sum(a["close_rate"] for a in accounts)/len(accounts) if accounts else 0
    avg_deal = total_cash/total_deals if total_deals > 0 else 0; prev_avg = total_prev/prev_deals if prev_deals > 0 else 0
    cash_chg = fmt_change(pct_change(total_cash, total_prev)); deals_chg = fmt_change(pct_change(total_deals, prev_deals)); avg_chg = fmt_change(pct_change(avg_deal, prev_avg))
    cards = "".join(account_card_html(a) for a in sorted_accounts); actions = action_items_html(sorted_accounts); first = director_name.split()[0]
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1.0"/><title>NR Dashboard — {first}</title><style>{CSS}</style></head><body>
<div id="gate" class="login-gate"><div class="login-box"><div class="login-logo">Net<span>Revenue</span></div><div class="login-sub">Sales Director Dashboard</div><input id="pw" class="login-input" type="password" placeholder="Enter your password" onkeydown="if(event.key==='Enter')unlock()"/><button class="login-btn" onclick="unlock()">View Dashboard</button><div id="err" class="login-error"></div></div></div>
<div id="dash" style="display:none"><div class="topbar"><div style="display:flex;align-items:center;gap:16px"><div class="logo">Net<span>Revenue</span></div><div class="week-badge">{week_label}</div></div><div class="director-tag">{director_name}</div></div><div class="main"><div class="summary-strip"><div class="stat-card"><div class="stat-label">Total Cash</div><div class="stat-value">{fmt_currency(total_cash)}</div><div class="stat-change {cash_chg["cls"]}">{cash_chg["text"]}</div></div><div class="stat-card"><div class="stat-label">Deals Closed</div><div class="stat-value">{total_deals}</div><div class="stat-change {deals_chg["cls"]}">{deals_chg["text"]}</div></div><div class="stat-card"><div class="stat-label">Avg Deal Size</div><div class="stat-value">{fmt_currency(avg_deal)}</div><div class="stat-change {avg_chg["cls"]}">{avg_chg["text"]}</div></div><div class="stat-card"><div class="stat-label">Avg Show Rate</div><div class="stat-value">{fmt_pct(avg_show)}</div></div><div class="stat-card"><div class="stat-label">Avg Close Rate</div><div class="stat-value">{fmt_pct(avg_close)}</div></div></div><div class="section-header"><div class="section-title">Your Accounts ({len(sorted_accounts)})</div><div class="section-meta">Sorted by revenue · {week_label}</div></div><div class="account-grid">{cards}</div><div class="actions-section"><div class="actions-title">Action Items This Week — {director_name}</div>{actions}</div></div><div class="footer">NetRevenue Apollo Team · Generated {generated_at} · Data via SalesVue</div></div>
<script>const PW="{password}";const KEY="nr_auth_{first.lower()}";if(sessionStorage.getItem(KEY)==="1")show();function unlock(){{if(document.getElementById("pw").value===PW){{sessionStorage.setItem(KEY,"1");show();}}else{{document.getElementById("err").textContent="Incorrect password.";}}}}function show(){{document.getElementById("gate").style.display="none";document.getElementById("dash").style.display="block";}}</script>
</body></html>"""

def main():
    from datetime import datetime
    start_date, end_date = get_previous_week()
    week_label = f"Mon {start_date} – Sun {end_date}"
    generated_at = datetime.now().strftime("%a %d %b %Y at %H:%M SAST")
    print(f"[NR] Generating dashboard for {week_label}")
    sv = SalesVueClient()
    print("[SalesVue] Pulling placements...")
    placements = sv.get_placements()
    director_accounts = {name: [] for name in DIRECTORS}
    active_accounts = []
    for p in placements:
        if not p.get("is_active"): continue
        for rep in p.get("reps", []):
            if rep["role"] == "Sales Director" and rep["user_id"] in DIRECTOR_BY_SV_ID:
                director_accounts[DIRECTOR_BY_SV_ID[rep["user_id"]]].append(p)
        active_accounts.append(p)
    print(f"[SalesVue] Pulling data for {len(active_accounts)} accounts...")
    account_data = {}
    def fetch(placement):
        aid = placement["account_id"]
        rev = sv.get_revenue_summary(aid, start_date, end_date)
        appt = sv.get_appointment_summary(aid, start_date, end_date)
        return aid, build_account_data(placement, rev, appt)
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(fetch, p): p for p in active_accounts}
        for f in as_completed(futures):
            try:
                aid, data = f.result(); account_data[aid] = data
            except Exception as e:
                print(f"  ⚠ Failed {futures[f]['account_name']}: {e}")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    for director_name, info in DIRECTORS.items():
        accounts = [account_data[p["account_id"]] for p in director_accounts.get(director_name, []) if p["account_id"] in account_data]
        if not accounts: print(f"  — Skipping {director_name} (no accounts)"); continue
        html = build_page(director_name, accounts, info["password"], week_label, generated_at)
        with open(os.path.join(OUTPUT_DIR, info["filename"]), "w", encoding="utf-8") as fh:
            fh.write(html)
        print(f"  ✓ {director_name} → {info['filename']} ({len(accounts)} accounts)")
    print(f"[NR] Done.")

if __name__ == "__main__":
    main()
