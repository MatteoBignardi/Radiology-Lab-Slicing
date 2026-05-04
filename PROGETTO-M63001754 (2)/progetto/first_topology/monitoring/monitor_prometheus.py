#!/usr/bin/python3
# -*- coding: utf-8 -*-

import os
import time
from collections import defaultdict

from prometheus_client import REGISTRY, CollectorRegistry, generate_latest
from prometheus_client import Gauge

from webob import Response

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, DEAD_DISPATCHER, set_ev_cls
from ryu.lib import hub
from ryu.app.wsgi import ControllerBase, WSGIApplication, route
from ryu.ofproto import ofproto_v1_3


PROMETHEUS_ENDPOINT = "/metrics"
PROMETHEUS_POLLTIME = float(os.getenv("PROMETHEUS_POLLTIME", "2.0"))



class PrometheusController(ControllerBase):
    """
    Controller WSGI responsabile dell'endpoint HTTP PROMETHEUS_ENDPOINT ("/metrics").

    Scopo:
        Rispondere alle richieste HTTP GET su /metrics restituendo le metriche Prometheus
        nel formato testuale standard (Prometheus text exposition format).
    """
    def __init__(self, req, link, data, **config):
        """
        Inizializza il controller WSGI.

        Parametri:
            req:
                request WebOb passata dal framework WSGI di Ryu.
            link:
                informazioni di routing/collegamento interne al WSGI di Ryu.
            data:
                eventuale dizionario dati condiviso.

        Ritorno:
            None.

        Spiegazione:
            Riga 1:
                Delega l'inizializzazione alla superclasse ControllerBase.
        """
        super().__init__(req, link, data, **config)

    @route("prometheus", PROMETHEUS_ENDPOINT, methods=["GET"])
    def metrics(self, req, **kwargs):
        """
        Handler per HTTP GET su PROMETHEUS_ENDPOINT.

        Scopo:
            Generare e restituire il payload testuale delle metriche Prometheus.

        Ritorno:
            webob.Response:
                response con:
                    - content_type = "text/plain; version=0.0.4; charset=utf-8"
                    - body = output di generate_latest(registry)

        Spiegazione riga per riga:
            Riga 1:
                Crea un registry locale (CollectorRegistry) separato dal REGISTRY globale.

            Riga 2-3:
                Itera sui collector presenti nel REGISTRY globale e li registra nel registry locale.
                Nota: il codice usa REGISTRY._collector_to_names.keys() per ottenere i collector.

            Riga 4:
                Genera il payload testo con generate_latest(registry).

            Riga 5-8:
                Costruisce e restituisce una Response WebOb con content_type Prometheus e body = payload.
        """
        registry = CollectorRegistry()
        for collector in list(REGISTRY._collector_to_names.keys()):
            registry.register(collector)
        data = generate_latest(registry)
        return Response(
            content_type="text/plain; version=0.0.4; charset=utf-8",
            body=data
        )


# -----------------------------
# Ryu app
# -----------------------------
class MonitorPrometheus(app_manager.RyuApp):
    """
    Applicazione Ryu che raccoglie statistiche dagli switch OpenFlow
    e le pubblica come metriche Prometheus.

    Contesto WSGI:
        Questa classe dichiara:
            _CONTEXTS = {"wsgi": WSGIApplication}
    # Cookie OpenFlow: usati dal controller per distinguere topology vs service slicing
    # (stessi valori definiti in first_topology/controller.py)
    Questo fa sì che Ryu passi a __init__ un oggetto:
    kwargs["wsgi"]
    Tale oggetto rappresenta il server WSGI interno di Ryu e viene
    usato esclusivamente per registrare il controller PrometheusController,
    che gestisce l'endpoint /metrics.
    """

    SERVICE_COOKIE = 0x5E12CE01
    TOPOLOGY_COOKIE_BASE = 0x5E12CE0200000000
    TOPOLOGY_COOKIE_MASK = 0xFFFFFFFFFFFFFF00  # ignora l'ultimo byte (slice number)

    @classmethod
    def _cookie_to_mode(cls, cookie: int) -> str:
        """Mappa il cookie del flow in una label leggibile per Grafana."""
        c = int(cookie)
        if c == cls.SERVICE_COOKIE:
            return "service"
        if (c & cls.TOPOLOGY_COOKIE_MASK) == cls.TOPOLOGY_COOKIE_BASE:
            return "topology"
        return "other"

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {"wsgi": WSGIApplication}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


        # Registrazione dell'endpoint /metrics nel server WSGI di Ryu.
        # kwargs["wsgi"] è fornito automaticamente da Ryu perché _CONTEXTS
        # include WSGIApplication.
        wsgi = kwargs["wsgi"]
        wsgi.register(PrometheusController, {})

        # switch connessi
        self.datapaths = {}

        # Serie viste per ogni switch (dpid): serve per rimuovere serie 'vecchie' quando i flow cambiano
        self._seen_flow_series = defaultdict(set)


        # -----------------------------
        # Metriche 
        # -----------------------------
        self.ryu_flow_count = Gauge(
            "ryu_flow_count",
            "Number of flows in a switch table",
            ["datapath_id", "table_id"],
        )

        self.ryu_packet_count = Gauge(
            "ryu_packet_count",
            "Packet count per flow (cumulative, from OF stats)",
            ["datapath_id", "table_id", "mode", "cookie", "in_port", "eth_src", "eth_dst", "eth_type", "ip_proto", "udp_dst"],
        )

        self.ryu_byte_count = Gauge(
            "ryu_byte_count",
            "Byte count per flow (cumulative, from OF stats)",
            ["datapath_id", "table_id", "mode", "cookie", "in_port", "eth_src", "eth_dst", "eth_type", "ip_proto", "udp_dst"],
        )

        self.ryu_duration_sec = Gauge(
            "ryu_duration_sec",
            "Flow duration in seconds (from OF stats)",
            ["datapath_id", "table_id", "mode", "cookie", "in_port", "eth_src", "eth_dst", "eth_type", "ip_proto", "udp_dst"],
        )

        self.monitor_thread = hub.spawn(self._monitor)

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change_handler(self, ev):
        """
        Handler dell'evento EventOFPStateChange (Ryu), registrato per gli stati:
            - MAIN_DISPATCHER
            - DEAD_DISPATCHER

        Scopo:
            Tenere aggiornato `self.datapaths` con gli switch (datapath) attualmente attivi.

        Parametri:
            ev:
                Oggetto evento Ryu. Nel codice vengono usati:
                    - ev.datapath  -> dp
                    - ev.state     -> stato corrente del datapath

        Ritorno:
            None.

        Effetti collaterali:
            - Modifica `self.datapaths` (aggiunge o rimuove una entry).
            - Scrive log informativi con self.logger.info.

        Spiegazione:
            Riga 1:
                Estrae il datapath dall'evento: dp = ev.datapath.

            Riga 2-6 (caso MAIN_DISPATCHER):
                Se lo stato è MAIN_DISPATCHER:
                    - controlla che dp.id non sia già presente in self.datapaths
                    - logga la connessione
                    - salva l'oggetto dp in self.datapaths[dp.id]

            Riga 7-12 (caso DEAD_DISPATCHER):
                Se lo stato è DEAD_DISPATCHER:
                    - controlla che dp.id sia presente in self.datapaths
                    - logga la disconnessione
                    - rimuove self.datapaths[dp.id]
        """
        dp = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            if dp.id not in self.datapaths:
                self.logger.info("Prometheus monitor: datapath %s connected", dp.id)
                self.datapaths[dp.id] = dp
        elif ev.state == DEAD_DISPATCHER:
            if dp.id in self.datapaths:
                self.logger.info("Prometheus monitor: datapath %s disconnected", dp.id)
                del self.datapaths[dp.id]

    def _monitor(self):
        """
        Loop di interrogazione periodico.

        Scopo:
            Interrogare ciclicamente tutti i datapath presenti in `self.datapaths` inviando una
            OFPFlowStatsRequest, e ripetere l'operazione dopo una pausa di PROMETHEUS_POLLTIME.

        Parametri:
            Nessuno.

        Ritorno:
            None (loop infinito).

        Effetti collaterali:
            - Chiama `_request_flow_stats(dp)` per ciascun datapath connesso.
            - Esegue `hub.sleep(PROMETHEUS_POLLTIME)` (pausa tra due cicli).
            - Produce traffico OpenFlow di controllo verso gli switch.

        Note (aderenti al codice):
            - Il codice itera su `list(self.datapaths.values())` (snapshot) per evitare problemi se
              `self.datapaths` cambia durante l'iterazione.

        Spiegazione:
            Riga 1:
                Entra in un ciclo infinito `while True`.

            Riga 2-3:
                Costruisce una lista dei datapath attuali e, per ciascuno, invia una richiesta stats
                chiamando `_request_flow_stats(dp)`.

            Riga 4:
                Attende PROMETHEUS_POLLTIME secondi prima del giro successivo.
        """

        while True:
            for dp in list(self.datapaths.values()):
                self._request_flow_stats(dp)
            hub.sleep(PROMETHEUS_POLLTIME)

    def _request_flow_stats(self, dp):
        """
        Invia una richiesta di statistiche delle regole OpenFlow allo switch rappresentato da `dp`.

        Scopo:
            Costruire e inviare un messaggio OpenFlow `OFPFlowStatsRequest` che richiede le statistiche
            delle regole presenti nello switch senza applicare filtri.

        Parametri:
            dp:
                Oggetto datapath Ryu verso cui inviare la richiesta.

        Ritorno:
            None.

        Dettagli della richiesta (aderenti al codice):
            Viene costruita così:
                parser.OFPFlowStatsRequest(
                    dp,
                    0,
                    ofp.OFPTT_ALL,   # tutte le tabelle
                    ofp.OFPP_ANY,    # nessun filtro su out_port
                    ofp.OFPG_ANY,    # nessun filtro su out_group
                    0, 0,
                    parser.OFPMatch() # match vuoto (nessun filtro sui campi di match)
                )

        Spiegazione:
            Riga 1:
                Legge `ofp = dp.ofproto`.

            Riga 2:
                Legge `parser = dp.ofproto_parser`.

            Riga 3-5:
                Costruisce l'oggetto `req` (OFPFlowStatsRequest) con i parametri sopra.

            Riga 6:
                Invia la richiesta allo switch con `dp.send_msg(req)`.
        """
        ofp = dp.ofproto
        parser = dp.ofproto_parser
        req = parser.OFPFlowStatsRequest(
            dp, 0, ofp.OFPTT_ALL, ofp.OFPP_ANY, ofp.OFPG_ANY, 0, 0, parser.OFPMatch()
        )
        dp.send_msg(req)


    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def _flow_stats_reply_handler(self, ev):
        """
        Gestisce una risposta FlowStatsReply (statistiche OpenFlow) e aggiorna le metriche Prometheus.

        Scopo:
            Tradurre i dati contenuti nella risposta (`ev.msg.body`) in valori numerici per Prometheus.
            La risposta contiene una lista di voci statistiche: ogni voce descrive una regola presente
            nelle tabelle OpenFlow dello switch (match + contatori + durata).

        Parametri:
            ev:
                evento Ryu. Da questo evento il metodo usa:
                    - lo switch che ha risposto (datapath)
                    - la lista delle voci statistiche (body)

        Ritorno:
            None.

        Effetti collaterali:
            - aggiorna Gauge Prometheus con `.set(...)`

        Spiegazione passaggio per passaggio (senza riscrivere il codice):

            1) Identificazione dello switch
               - Il metodo ricava un identificatore dello switch (DPID) in formato stringa.
                 Questo identificatore viene usato come etichetta (label) nelle metriche Prometheus.

            2) Conteggio delle regole presenti in ogni tabella OpenFlow (metrica ryu_flow_count)
               - Si percorre l'elenco delle voci statistiche e si costruisce un conteggio per `table_id`.
               - Risultato: per ciascuna tabella dello switch si ottiene "quante regole sono presenti".
               - Questo valore viene pubblicato nella metrica `ryu_flow_count` usando come etichette
                 l'identificatore dello switch e l'identificatore della tabella.

            3) Metriche per singola regola (una voce alla volta)
               - Si percorre l'elenco delle voci statistiche.
               - Per ogni voce:
                   a) si estraggono alcuni campi dal "match" (se non esistono vengono usati default).
                      Questi campi vengono usati come etichette per distinguere le serie in Prometheus.
                   b) si pubblicano i contatori cumulativi della singola regola:
                        * ryu_packet_count
                        * ryu_byte_count
                   c) si calcola la durata della regola combinando secondi e nanosecondi
                      (se un campo manca, il codice usa 0 come default).
                      Il valore viene pubblicato in:
                        * ryu_duration_sec
        """

        dp = ev.msg.datapath
        dpid = str(dp.id)
        current_series = set()  

        table_counts = defaultdict(int)
        for stat in ev.msg.body:
            table_counts[str(stat.table_id)] += 1

        for table_id, count in table_counts.items():
            self.ryu_flow_count.labels(
                datapath_id=dpid,
                table_id=str(table_id),
            ).set(count)

        for stat in ev.msg.body:
            table_id = str(stat.table_id)
            match = stat.match

            in_port  = match.get("in_port", "")
            eth_src  = match.get("eth_src", "")
            eth_dst  = match.get("eth_dst", "")
            eth_type = match.get("eth_type", "")
            ip_proto = match.get("ip_proto", "")
            udp_dst  = match.get("udp_dst", "")
            cookie_int = int(getattr(stat, "cookie", 0))
            cookie = hex(cookie_int)
            mode = self._cookie_to_mode(cookie_int)

            key = (
                dpid, table_id, mode, cookie,
                str(in_port), str(eth_src), str(eth_dst),
                str(eth_type), str(ip_proto), str(udp_dst),
            )
            current_series.add(key)


            self.ryu_packet_count.labels(
                datapath_id=dpid,
                table_id=table_id,
                mode=mode,
                cookie=cookie,
                in_port=str(in_port),
                eth_src=str(eth_src),
                eth_dst=str(eth_dst),
                eth_type=str(eth_type),
                ip_proto=str(ip_proto),
                udp_dst=str(udp_dst),
            ).set(int(stat.packet_count))

            self.ryu_byte_count.labels(
                datapath_id=dpid,
                table_id=table_id,
                mode=mode,
                cookie=cookie,
                in_port=str(in_port),
                eth_src=str(eth_src),
                eth_dst=str(eth_dst),
                eth_type=str(eth_type),
                ip_proto=str(ip_proto),
                udp_dst=str(udp_dst),
            ).set(int(stat.byte_count))

            dur_s = float(getattr(stat, "duration_sec", 0)) + float(getattr(stat, "duration_nsec", 0)) / 1e9
            self.ryu_duration_sec.labels(
                datapath_id=dpid,
                table_id=table_id,
                mode=mode,
                cookie=cookie,
                in_port=str(in_port),
                eth_src=str(eth_src),
                eth_dst=str(eth_dst),
                eth_type=str(eth_type),
                ip_proto=str(ip_proto),
                udp_dst=str(udp_dst),
            ).set(dur_s)

        # Rimuovi serie 'vecchie' (flow non più presenti nello switch) 
        stale = self._seen_flow_series[dpid] - current_series
        for key in stale:
            self.ryu_packet_count.remove(*key)
            self.ryu_byte_count.remove(*key)
            self.ryu_duration_sec.remove(*key)
        self._seen_flow_series[dpid] = current_series
