#!/usr/bin/python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple, Any

_HERE = Path(__file__).resolve().parent
_TOPO_JSON = _HERE / "topology.json"

def _load() -> Dict[str, Any]:
    """
    Scopo:
        Caricare la configurazione della topologia dal file topology.json.

    Parametri:
        Nessuno.

    Ritorno:
        Dict[str, Any]:
            Dizionario con la configurazione letta dal JSON.
    """
    with _TOPO_JSON.open("r", encoding="utf-8") as f:
        return json.load(f)

_cfg = _load()

# N_SWITCHES: numero switch definiti nel JSON.
N_SWITCHES: int = int(_cfg["n_switches"])

# LINKS: lista link switch-switch nel formato:
#   (swA, swB, porta_su_A, porta_su_B, bw_Mbit)
LINKS: List[Tuple[str, str, int, int, int]] = [
    (a, b, int(pA), int(pB), int(bw))
    for (a, b, pA, pB, bw) in _cfg["links"]
]

# HOST_LINKS: lista link host-switch nel formato:
#   (host, switch, porta_su_host, porta_su_switch)
HOST_LINKS: List[Tuple[str, str, int, int]] = [
    (h, sw, int(hp), int(swp))
    for (h, sw, hp, swp) in _cfg["host_links"]
]

# HOSTS: mappa host_name -> MAC (serve per Mininet e per regole SDN basate su MAC dst).
HOSTS: Dict[str, str] = dict(_cfg["hosts"])

# HOST_IP: mappa host_name -> IP (opzionale; se assente, l'host non ha IP predefinito).
HOST_IP: Dict[str, str] = dict(_cfg.get("host_ip", {}))

HOST_UI_LABEL: Dict[str, str] = dict(_cfg.get("host_ui_label", {}))

HOST_UI_ALIAS: Dict[str, str] = dict(_cfg.get("host_ui_alias", {}))

def host_ip(hostname: str, mac: str | None = None) -> str | None:
    """
    Scopo:
        Restituire l'IP associato a un host, se definito in HOST_IP.

    Parametri:
        hostname (str):
            Nome host (chiave della mappa HOST_IP).
        mac (str | None):
            Non usato qui (tenuto per compatibilita' con altre interfacce).

    Ritorno:
        str | None:
            IP dell'host se presente, altrimenti None.

    """
    return HOST_IP.get(hostname)

def ui_line_id_for_switch_link(swA: str, swB: str) -> str:
    """
    Scopo:
        Generare un ID stabile per la UI che rappresenta un link tra switch.

    Parametri:
        swA (str): nome switch A
        swB (str): nome switch B

    Ritorno:
        str: "l_<swA>_<swB>"

    """
    return f"l_{swA}_{swB}"

def ui_line_id_for_host_link(host: str, sw: str) -> str:
    """
    Scopo:
        Generare un ID stabile per la UI che rappresenta un link host-switch.
        Usa l'alias UI dell'host se disponibile.

    Parametri:
        host (str): nome host reale (Mininet)
        sw (str): nome switch

    Ritorno:
        str: "l_<sw>_<alias_host>"

    """
    alias = HOST_UI_ALIAS.get(host, host)
    return f"l_{sw}_{alias}"

def ui_host_defs() -> Dict[str, Dict[str, str | None]]:
    """
    Scopo:
        Preparare un dizionario serializzabile (JSON) con metadati host per la UI.

    Parametri:
        Nessuno.

    Ritorno:
        Dict[str, Dict[str, str|None]]:
            { host_name: {name,label,mac,ip}, ... }
    """
    out: Dict[str, Dict[str, str | None]] = {}
    for hname, mac in HOSTS.items():
        out[hname] = {
            "name": hname,
            "label": HOST_UI_LABEL.get(hname, HOST_UI_ALIAS.get(hname, hname)),
            "mac": mac,
            "ip": host_ip(hname, mac),
        }
    return out
