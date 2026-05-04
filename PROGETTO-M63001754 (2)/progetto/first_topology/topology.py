#!/usr/bin/python3

from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import OVSKernelSwitch, RemoteController
from mininet.cli import CLI
from mininet.link import TCLink

import os
import json
from pathlib import Path
import topology_defs as defs
setarp = True  # abilita ARP statico in Mininet (autoStaticArp)


N_SWITCHES = defs.N_SWITCHES
LINKS = defs.LINKS
HOST_LINKS = defs.HOST_LINKS
HOSTS = defs.HOSTS
HOST_IP = defs.HOST_IP
HOST_UI_LABEL = defs.HOST_UI_LABEL
HOST_UI_ALIAS = defs.HOST_UI_ALIAS

def host_ip(hostname: str, mac: str) -> str | None:
    """
    Restituisce l'IP associato ad un host (se definito).

    Parametri:
        hostname (str): nome host Mininet (es. "hCam1")
        mac (str): MAC dell'host

    Ritorno:
        str | None: IP dell'host se disponibile, altrimenti None.
    """
    return defs.host_ip(hostname, mac)


class Topology(Topo):
    """
    Topologia Mininet del progetto

    Note:
        Le strutture (HOSTS, LINKS, ecc.) vengono dal modulo topology_defs.
    """

    HOST_UI_ALIAS = HOST_UI_ALIAS
    HOST_UI_LABEL = HOST_UI_LABEL

    def ui_line_id_for_switch_link(self, swA: str, swB: str) -> str:
        """
        Costruisce un ID UI stabile per un link tra due switch.

        Scopo:
            Dare alla UI un identificatore deterministico, es. "l_s1_s2".

        Parametri:
            swA (str): nome switch A
            swB (str): nome switch B

        Ritorno:
            str: ID linea "l_<swA>_<swB>"
        """
        return f"l_{swA}_{swB}"

    def ui_line_id_for_host_link(self, host: str, sw: str) -> str:
        """
        Costruisce un ID UI stabile per un link host-switch.

        Scopo:
            Usare l'alias UI dell'host se esiste, per coerenza col frontend.

        Parametri:
            host (str): nome host Mininet (es. "hCam1")
            sw (str): nome switch (es. "s6")

        Ritorno:
            str: ID linea "l_<sw>_<alias>"
        """
        alias = self.HOST_UI_ALIAS.get(host, host)
        return f"l_{sw}_{alias}"

    def ui_host_defs(self) -> dict:
        """
        Genera definizioni host per la UI (serializzabili in JSON).

        Scopo:
            Costruire un dizionario con metadati host:
            - name, label, mac, ip

        Ritorno:
            dict: mappa {host_name: {...}} pronta per essere convertita in JSON.
        """
        defs = {}
        for hname, mac in HOSTS.items():
            defs[hname] = {
                "name": hname,
                "label": self.HOST_UI_LABEL.get(hname, self.HOST_UI_ALIAS.get(hname, hname)),
                "mac": mac,
                "ip": host_ip(hname, mac),
            }
        return defs

    def __init__(self):
        """
        Costruisce la topologia Mininet popolando l'oggetto Topo con:
            - switch (s1..sN) con DPID deterministico
            - host con MAC e indirizzo IP fissato 
            - link switch-switch con banda massima consentita sul link e HTB ((Hierarchical Token Bucket)) abilitato (garantisce che il link non superi la banda dichiarata)
            - link host-switch con porte specificate
        """
        Topo.__init__(self)
        for i in range(N_SWITCHES):
            sconfig = {"dpid": "%016x" % (i + 1)}
            self.addSwitch("s%d" % (i + 1), **sconfig)
        for hname, hmac in HOSTS.items():
            ip = HOST_IP.get(hname, None)
            self.addHost(hname, inNamespace=True, mac=hmac, ip=(ip + "/24") if ip else None)
        for link in LINKS:
            self.addLink(
                link[0], link[1],
                port1=link[2], port2=link[3],
                bw=link[4], use_htb=True
            )

        for link in HOST_LINKS:
            self.addLink(link[0], link[1], port1=link[2], port2=link[3])


def setOpenFlow13(net):
    """
    Imposta OpenFlow 1.3 su tutti gli switch OVS della rete.

    Parametri:
        net (Mininet): rete già costruita (net.build()).

    Effetti collaterali:
        Esegue comandi ovs-vsctl sul sistema e stampa log.
    """
    for sw in net.switches:
        sw.cmd(f"ovs-vsctl set bridge {sw.name} protocols=OpenFlow13")
        print(f"Set OpenFlow 1.3 for {sw.name}")


def write_mn_host_pids(net, out_path: str = "/tmp/mn_host_pids.json"):
    """
    Scrive su disco una mappa host->PID in JSON.

    Scopo:
        Consentire ad altri script di eseguire comandi negli host via:
            mnexec -a <pid> <cmd>

    Parametri:
        net (Mininet): rete avviata (o comunque con host.pid disponibili).
        out_path (str): path del file JSON di output.
    """
    data = {h.name: int(h.pid) for h in net.hosts if getattr(h, "pid", None)}
    tmp = Path(out_path + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    os.replace(str(tmp), out_path)
    os.chmod(out_path, 0o644)
    print(f"[topology] wrote pid-map: {out_path} ({len(data)} hosts)")


if __name__ == "__main__":
    """
    Avvio dello script (entry-point): crea e lancia la rete Mininet e apre la CLI.

    Scopo:
        Quando esegui questo file direttamente (python3 topology.py), qui viene:
        - costruita la topologia (classe Topology)
        - avviata una rete Mininet con switch OVS
        - collegato un controller remoto (SDN) in ascolto su 127.0.0.1:6653
        - impostata la versione OpenFlow 1.3 sugli switch
        - salvata su disco la mappa host->PID (per poter usare mnexec da altri script)
        - aperta la CLI interattiva per test/debug
        Alla chiusura della CLI (o in caso di errore), la rete viene sempre fermata.
    """
    topo = Topology()

    net = Mininet(
        topo=topo,
        controller=None,  
        switch=OVSKernelSwitch,
        build=False,
        autoSetMacs=False,
        autoStaticArp=setarp,
        link=TCLink,
    )

    net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6653)

    net.build()

    setOpenFlow13(net)

    net.start()

    write_mn_host_pids(net)

    # Forza (extra) il controller su ogni switch: utile se qualcosa non viene propagato
    for sw in net.switches:
        sw.cmd(f'ovs-vsctl set-controller {sw.name} tcp:127.0.0.1:6653')

    try:
        CLI(net)
    finally:
        net.stop()
