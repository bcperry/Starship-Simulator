"""Raptor engine workbench (Streamlit, SpaceX-ish dark).

Run it from the repo root:

    uv run streamlit run simulator/engine/gui.py

The live controls + sim loop live in an auto-refreshing fragment, so
dragging the throttle slider mid-burn doesn't restart the engine.
LATER: roll this up into the full vehicle.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import matplotlib

matplotlib.use("Agg")  # canvas renders only, no interactive backend
import streamlit as st
from matplotlib.figure import Figure

from simulator.engine import EngineState, RaptorEngine, step

# ---------------------------------------------------------------------------
# palette (SpaceX-ish)
# ---------------------------------------------------------------------------
BG = "#0b0f14"
PANEL = "#111723"
PLOT_BG = "#10161f"
GRID = "#232c3a"
CYAN = "#00b7ff"
WHITE = "#eef2f7"
AMBER = "#ff8c1a"
GREEN = "#5fd35f"
GREY = "#5a6678"

DT = 0.01       # sim step, s
REFRESH = 0.05  # fragment refresh / UI frame, s

TITLES = ["THRUST (MN)", "ISP (S)", "CHAMBER PRESSURE (MPA)",
          "MASS FLOW (KG/S)", "PROPELLANT REMAINING (KG)"]


def _stat(label: str, value: str, color: str = CYAN) -> str:
    return (f'<div style="background:{PANEL}; border:1px solid {GRID}; '
            f'border-radius:10px; padding:10px 14px;">'
            f'<div style="font-size:11px; color:{GREY}; letter-spacing:2px;">{label}</div>'
            f'<div style="font-size:21px; font-weight:700; color:{color}; margin-top:2px;">{value}</div>'
            f"</div>")


def _style_ax(ax, title: str) -> None:
    ax.set_facecolor(PLOT_BG)
    ax.set_title(title, color="#c9d4e0", fontsize=8.5,
                 loc="left", fontweight="bold", pad=4)
    ax.tick_params(colors=GREY, labelsize=7.5)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.grid(color=GRID, linewidth=0.6, alpha=0.7)


st.set_page_config(page_title="RAPTOR ENGINE", page_icon="🚀", layout="wide")
st.markdown(
    f"""<style>
html, body, [data-testid="stAppViewContainer"] {{ background:{BG}; color:#c9d4e0; }}
h1 {{ color:{WHITE}; letter-spacing:4px; font-weight:600; }}
p, li, [data-testid="stCaptionContainer"] {{ color:#8a97a8; }}
"""
    , unsafe_allow_html=True
)

st.title("RAPTOR ENGINE SIM")
st.caption("LOX / CH4 · `simulator/engine/raptor.py` · throttle & ambient are live mid-burn")

# ---------------------------------------------------------------------------
# sidebar: engine parameters (frozen at RUN)
# ---------------------------------------------------------------------------
with st.sidebar:
    st.subheader("ENGINE (frozen on RUN)")
    pc_mpa = st.number_input("Chamber P @ 100% (MPa)", 1.0, 60.0, 30.0, step=0.5)
    mr = st.number_input("Mixture ratio O/F", 1.0, 10.0, 3.6, step=0.05)
    throat_cm2 = st.number_input("Throat area (cm^2)", 10.0, 1000.0, 350.0, step=5.0)
    area_ratio = st.number_input("Nozzle A_e/A_t", 5.0, 100.0, 40.0, step=1.0)
    tau = st.number_input("Throttle time constant (s)", 0.0, 5.0, 0.5, step=0.05)
    load_kg = st.number_input("Propellant load (kg; 0 = inf)", 0.0, 1e6, 0.0, step=500.0)
    st.divider()
    duration = st.number_input("Max burn duration (s)", 2.0, 600.0, 20.0, step=1.0)


# ---------------------------------------------------------------------------
# live panel: controls + sim loop (auto-refreshing fragment)
# ---------------------------------------------------------------------------
@st.fragment(run_every=REFRESH)
def sim_panel() -> None:
    ss = st.session_state

    st.slider("THR (%)", 0, 100, 100, key="throttle")
    st.slider("AMBIENT PRESSURE (kPa)", 0.0, 101.3, 0.0, key="ambient")
    c1, c2, c3 = st.columns([1, 1, 2])
    c1.button("RUN", on_click=lambda: ss.__setitem__("run_now", True))
    c2.checkbox("HOLD", key="hold")
    speed = c3.slider("Sim speed (x realtime)", 1, 40, 10)

    throttle = ss.throttle / 100
    ambient = ss.ambient * 1e3  # Pa

    if ss.pop("run_now", False):
        engine = RaptorEngine(
            chamber_pressure=pc_mpa * 1e6,
            mixture_ratio=mr,
            throat_area=throat_cm2 / 1e4,
            nozzle_area_ratio=area_ratio,
            throttle_time_constant=max(tau, DT),
            propellant_mass=load_kg,
        )
        ss["sim"] = {
            "engine": engine, "state": EngineState(engine), "t": 0.0,
            "h": {"t": [], "F": [], "I": [], "pc": [], "dem": [],
                  "ox": [], "fu": [], "prop": []},
            "running": True, "starved": False,
        }

    sim = ss.get("sim")
    if not sim or not sim["running"]:
        st.caption("No burn in progress — hit RUN in the panel below the title."
                   if not sim else
                   f"Burn complete — T+{sim['t']:.1f} s, "
                   f"peak F={max(sim['h']['F'], default=0.0):.2f} MN"
                   + (" · feeds starved" if sim["starved"] else ""))
        charts_box = st.empty()
        if sim and sim["h"]["t"]:
            _render_charts(charts_box, sim["h"], sim)
        return

    h = sim["h"]
    out = None
    if not ss.hold:
        for _ in range(max(1, int(speed * REFRESH / DT))):
            out = step(sim["engine"], sim["state"], throttle, ambient, DT, t=sim["t"])
            sim["t"] += DT
            h["t"].append(sim["t"])
            h["F"].append(out.thrust / 1e6)
            h["I"].append(out.isp)
            h["pc"].append(out.chamber_pressure / 1e6)
            h["dem"].append(throttle * sim["engine"].chamber_pressure / 1e6)
            h["ox"].append(out.mdot_ox)
            h["fu"].append(out.mdot_fuel)
            h["prop"].append(out.propellant_remaining)
            sim["starved"] = (sim["engine"].propellant_mass > 0
                              and out.propellant_remaining == 0.0
                              and out.chamber_pressure < 2e3)
            if sim["starved"] or sim["t"] >= duration:
                break
        if sim["starved"] or sim["t"] >= duration:
            sim["running"] = False

    # HUD
    cols = st.columns(6)
    starved = bool(sim["starved"] and sim["engine"].propellant_mass)
    prop_txt = ("∞" if not sim["engine"].propellant_mass
                else "STARVED" if not h["prop"][-1] else f"{h['prop'][-1]:.0f} kg")
    for c, html in zip(cols, (
        _stat("THRUST", f"{h['F'][-1]:.3f} MN", CYAN),
        _stat("ISP", f"{h['I'][-1]:.1f} s", WHITE),
        _stat("CHAMBER P", f"{h['pc'][-1]:.2f} MPa", CYAN),
        _stat("MDOT O/F", f"{h['ox'][-1]:.0f}/{h['fu'][-1]:.0f} kg/s", AMBER),
        _stat("T+", f"{sim['t']:.2f} s", WHITE),
        _stat("PROPELLANT", prop_txt, AMBER if starved else GREEN),
    )):
        c.markdown(html, unsafe_allow_html=True)

    _render_charts(st.empty(), h, sim)
    time.sleep(REFRESH)  # pace the wall clock while HOLDing


def _render_charts(slot, h: dict, sim: dict) -> None:
    engine = sim["engine"]
    fig = Figure(figsize=(11, 5.6), dpi=110, facecolor=BG)
    axs = fig.subplots(5, 1, sharex=True, gridspec_kw={"hspace": 0.18})
    for ax, title in zip(axs, TITLES):
        _style_ax(ax, title)
    fig.text(0.5, 0.002, "T (S)", color=GREY, fontsize=8.5, ha="center")
    ax0, ax1, ax2, ax3, ax4 = axs
    prop_series = [] if not engine.propellant_mass else \
        [("PROPELLANT", h["prop"], GREEN, 1.2)]
    for ax, series in (
        (ax0, [("THRUST", h["F"], CYAN, 1.4)]),
        (ax1, [("ISP", h["I"], WHITE, 1.2)]),
        (ax2, [("Pc ACTUAL", h["pc"], CYAN, 1.4),
               ("Pc DEMAND", h["dem"], GREY, 0.8)]),
        (ax3, [("LOX", h["ox"], AMBER, 1.2), ("CH4", h["fu"], GREEN, 1.2)]),
        (ax4, prop_series),
    ):
        for label, data, color, lw in series:
            if data:
                ax.plot(h["t"], data, color=color, linewidth=lw, label=label)
        if len(series) > 1 and h["F"]:
            ax.legend(loc="upper left", fontsize=7, facecolor=PANEL,
                      edgecolor=GRID, labelcolor="#c9d4e0")
    slot.pyplot(fig)


sim_panel()
