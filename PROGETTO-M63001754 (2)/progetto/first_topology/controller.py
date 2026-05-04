#!/usr/bin/python3
# -*- coding: utf-8 -*-

import eventlet
eventlet.monkey_patch() 

import os
import json
import mimetypes

from webob import Response

from ryu.base import app_manager

from ryu.controller import ofp_event
from ryu.controller.handler import (
    CONFIG_DISPATCHER,
    MAIN_DISPATCHER,
    DEAD_DISPATCHER,
    set_ev_cls,
)
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ipv4, udp
from ryu.app.wsgi import WSGIApplication, ControllerBase, route

from traffic_simulation import TrafficSimulationManager

from ProblemConstants import (
    TOPOLOGY_DAY_SLICES_RULES,
    TOPOLOGY_NIGHT_SLICES_RULES,
    ControllerState,
    VIDEO_UDP_DST_PORT,
    SERVICE_SLICE_VIDEO,
    SERVICE_NONVIDEO_MAP,
    SERVICE_VIDEO_MAP,
    HOST_MAC,
)

from MacToPortMapper import MacToPortMapper

# ============================================================================
# COSTANTI DI SUPPORTO (MAC address)
# ============================================================================

# Lista di tutti gli host noti (dizionario HOST_MAC -> valori MAC)
# Utile per eventuali controlli/filtri globali.
ALL_HOST_MACS = list(HOST_MAC.values())

# Insieme dei MAC legati al "servizio video"
# Questi host sono quelli che consideriamo parte del dominio video (NVR, cam, LCS, ecc.).
# Nota: la logica "effective_is_video" richiede sia UDP dst_port=VIDEO_UDP_DST_PORT
#       sia che src e dst stiano dentro questo set.
VIDEO_HOST_MACS = {
    "00:00:00:00:00:07",  # nvr
    "00:00:00:00:00:08",  # cam1
    "00:00:00:00:00:09",  # cam2
    "00:00:00:00:00:0a",  # lCS
}


class StaticGuiController(ControllerBase):
    """
    Controller WSGI che serve file statici della GUI.
    """
    def __init__(self, req, link, data, **config):
        """
        Scopo
        -----
        Inizializza il controller statico e memorizza la directory radice dei file.

        Parametri
        ---------
        req, link, data, config:
            Parametri forniti dal framework WSGI di Ryu.
        data["static_dir"]:
            Percorso della directory contenente i file statici (html/js/css).
        """
        super().__init__(req, link, data, **config)
        self.static_dir = data["static_dir"]  # directory base dei file statici

    @route('gui_index', '/ui', methods=['GET'])
    def index(self, req, **kwargs):
        return self._serve('index.html')

    @route('gui_monitor', '/ui/monitor', methods=['GET'])
    def monitor(self, req, **kwargs):
        return self._serve('monitor.html')

    @route('gui_files', '/ui/{path:.*}', methods=['GET'])
    def files(self, req, path, **kwargs):
        if not path or '..' in path or path.startswith('/'):
            return Response(status=403, body=b'Forbidden')
        return self._serve(path)

    def _serve(self, relpath: str):
        full = os.path.join(self.static_dir, relpath)
        if not os.path.isfile(full):
            return Response(status=404, body=b'Not Found')
        ctype, _ = mimetypes.guess_type(full)
        ctype = ctype or 'application/octet-stream'
        with open(full, 'rb') as f:
            body = f.read()
        return Response(content_type=ctype, body=body)


class Controller(app_manager.RyuApp):
    """
    Controller SDN basato su Ryu (OpenFlow 1.3).

    Scopo generale
    --------------
    Questa classe e' il "cuore" del controller:
      1) Si registra come RyuApp e riceve eventi OpenFlow (SwitchFeatures, StateChange, PacketIn).
      2) Mantiene uno stato applicativo (ControllerState) che decide:
         - modalita' day/night
         - modalita' slicing topology/service
         - quali slice topologiche sono abilitate
         - se il servizio video e' abilitato (service slicing)
      3) Installa flow OpenFlow sugli switch per:
         - isolare traffico per slice (topology slicing)
         - instradare traffico video/non-video su path diversi (service slicing)
      4) Espone interfaccia HTTP/WSGI:
         - GUI statica (StaticGuiController)
         - API JSON (ControllerServer)

    Componenti interni importanti (attributi)
    -----------------------------------------
    - self.wsgi:
        Istanza WSGIApplication di Ryu. Serve per registrare controller HTTP.
    - self.state:
        Oggetto ControllerState che rappresenta lo "stato logico" del controller.
    - self.traffic_sim:
        TrafficSimulationManager (gestione generatori traffico: dicom/video).
    - self.state.mappers:
        Mappa mode -> MacToPortMapper, con regole day/night e slice attive.
    - self.datapaths:
        Mappa dpid -> datapath oggetto Ryu (switch connessi).
    - self.learned:
        Dizionario learning L2 (dpid_str -> {mac -> in_port}) usato in topology mode.
    - self._service_flow_cache:
        Set di chiavi cache per evitare reinstall di flow identiche in service mode.
    - self._video_service_dpids:
        Set dei dpid_str su cui e' ammesso installare regole specifiche video (path video).
    - self._video_proactive_cache: 
        Cache delle regole VIDEO proattive già installate sugli switch. Serve per evitare reinserimenti multipli della stessa regola video ogni volta che arriva uno switch_features o un comando "video_on".

    Note sull'architettura
    ----------------------
    - In Ryu, ogni switch OpenFlow connesso e' rappresentato da un "datapath".
    - Quando arriva un pacchetto non matchato da flow, la table-miss lo manda al controller (PacketIn).
    - Il controller decide se:
        a) installare una flow (per traffico ripetitivo)
        b) fare solo PacketOut (decisione one-shot)
    """
    # Versione OpenFlow supportata da questa app: OpenFlow 1.3
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {'wsgi': WSGIApplication}
    # Cookie OpenFlow usati per marcare flow installate dal controller:
    #
    # - SERVICE_COOKIE: cookie unico per tutte le flow installate in modalita' service slicing.
    #   Serve a riconoscere/cancellare facilmente quel gruppo di flow.
    SERVICE_COOKIE = 0x5E12CE01
    # - TOPOLOGY_COOKIE_BASE:
    #   Valore base utilizzato per generare il cookie delle flow topologiche.
    #
    #   Il numero di slice (1..3) viene inserito nell’ultimo byte del cookie:
    #
    #       cookie = TOPOLOGY_COOKIE_BASE | (slice_number & 0xFF)
    #
    #   In questo modo ogni slice ha un cookie distinto e il controller
    #   può eliminare rapidamente tutte le flow appartenenti a una singola slice
    #   senza toccare le altre.
    TOPOLOGY_COOKIE_BASE = 0x5E12CE0200000000

    def __init__(self, *args, **kwargs):
        """
        Costruttore della RyuApp.
        """
        super().__init__(*args, **kwargs)

        self.wsgi = kwargs['wsgi']  # WSGI app di Ryu
        html_dir = os.path.join(os.path.dirname(__file__), '..', 'html')  
        self.wsgi.register(StaticGuiController, {'static_dir': html_dir})  

        self.state = ControllerState()
        self.traffic_sim = TrafficSimulationManager(self)

        self.state.mappers = {
            ControllerState.DAY: MacToPortMapper(TOPOLOGY_DAY_SLICES_RULES),
            ControllerState.NIGHT: MacToPortMapper(TOPOLOGY_NIGHT_SLICES_RULES),
        }

        self.datapaths = {}

        self.learned = {}

        self._service_flow_cache = set()

        self._video_service_dpids = set(SERVICE_VIDEO_MAP.keys())

        self._video_proactive_cache = set()

        self.wsgi.register(ControllerServer, {"controller_instance": self})

    # ============================================================
    # FLOW HELPERS (OpenFlow)
    # ============================================================
    def _install_table_miss(self, datapath):
        """
        Installa la regola di "table-miss" sullo switch.

        Scopo
        -----
        Garantire che qualsiasi pacchetto che NON matcha altre flow venga inviato al controller
        tramite PacketIn, cosi' il controller puo' decidere come gestirlo.

        Parametri
        ---------
        datapath:
            Oggetto datapath di Ryu che rappresenta lo switch.

        Ritorno
        -------
        Nessuno. Invia un messaggio OFPFlowMod allo switch.

        Effetti collaterali
        -------------------
        - Modifica la tabella flow dello switch.
        - Abilita l'arrivo di PacketIn al controller.

        Spiegazione
        -----------
        - match vuoto (OFPMatch()) => matcha qualsiasi pacchetto
        - action output controller => invia al controller senza buffer (NO_BUFFER)
        - priority=0 => regola di priorita' minima (ultima a essere considerata)
        """
        ofp = datapath.ofproto
        parser = datapath.ofproto_parser

        match = parser.OFPMatch()  # match "any"
        actions = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)]
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]

        # priority=0 => ultima regola, prende ciò che non matcha altre flow
        datapath.send_msg(
            parser.OFPFlowMod(
                datapath=datapath,
                priority=0,
                match=match,
                instructions=inst
            )
        )

    def _add_flow(self, datapath, priority, match, actions, cookie=0):
        """
        Helper generico per installare una flow.

        Scopo

        Parametri
        ---------
        datapath:
            Switch target.
        priority: int
            Priorita' della flow (maggiore = valutata prima).
        match:
            Oggetto parser.OFPMatch con i campi su cui effettuare il matching.
        actions: list
            Lista di azioni OpenFlow.
        cookie: int
            Marcatore a 64 bit per identificare il "gruppo" di flow (service vs topology slice).

        Ritorno
        -------
        Nessuno. Invia OFPFlowMod allo switch.

        Effetti collaterali
        -------------------
        Modifica la tabella flow dello switch.
        """
        parser = datapath.ofproto_parser
        ofp = datapath.ofproto

        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]

        datapath.send_msg(
            parser.OFPFlowMod(
                datapath=datapath,
                priority=priority,
                match=match,
                instructions=inst,
                idle_timeout=0,     
                hard_timeout=0,    
                cookie=cookie
            )
        )

    def _topology_cookie(self, slice_number: int) -> int:
        """
        Calcola il cookie per una flow di topology slicing.

        Scopo
        -----
        Produrre un cookie che codifica la slice (1..3) dentro l'ultimo byte,
        mantenendo una base costante per tutte le flow topologiche.

        Parametri
        ---------
        slice_number: int
            Numero slice richiesto (atteso 1..3).

        Ritorno
        -------
        int:
            Cookie finale: TOPOLOGY_COOKIE_BASE | (slice_number & 0xFF)
        """
        sn = int(slice_number) & 0xFF
        return int(self.TOPOLOGY_COOKIE_BASE | sn)

    def _topology_resolve_out_port_and_slice(self, mapper, dpid_str: str, src_mac: str, dst_mac: str):
        """
        Risolve la porta di uscita in topology mode e identifica la slice a cui appartiene la comunicazione.

        Scopo
        -----
        Dato uno switch (dpid_str) e una coppia (src_mac, dst_mac), questo metodo:
          1) Scorre le slice attive (mapper.active_slice)
          2) Verifica se src e dst appartengono alla stessa slice (slice_hosts[i])
          3) Se si', usa le regole di forwarding (slices_rules[i]) per trovare la out_port su quello switch.

        Parametri
        ---------
        mapper:
            MacToPortMapper per la modalita' attiva (day o night).
        dpid_str: str
            DPID in formato stringa a 16 cifre (come costruito in PacketIn).
        src_mac, dst_mac: str
            MAC sorgente e destinazione (Ethernet).

        Ritorno
        -------
        (out_port, slice_no):
            - out_port: int oppure None se non risolto
            - slice_no: int (1..N) oppure None se non risolto

        Effetti collaterali
        -------------------
        Nessuno (sola lettura).

        Note
        ----
        Se non trova una slice valida oppure non trova una regola per quel dst_mac su quello switch,
        ritorna (None, None).
        """
        for i, on in enumerate(getattr(mapper, 'active_slice', [])):
            if on != 1:
                continue
            slice_hosts = getattr(mapper, 'slice_hosts', [])
            slices_rules = getattr(mapper, 'slices_rules', [])
            if i < len(slice_hosts) and (src_mac in slice_hosts[i]) and (dst_mac in slice_hosts[i]):
                rules = slices_rules[i]
                port = rules.get(dpid_str, {}).get(dst_mac)
                if port is not None:
                    return port, (i + 1)
        return None, None

    def _delete_flows_by_cookie(self, datapath, cookie: int, cookie_mask: int = 0xFFFFFFFFFFFFFFFF):
        """
        Cancella flow che matchano un certo cookie.

        Scopo:
            Rimuovere selettivamente flow installate dal controller usando il campo cookie di OpenFlow.
            Questo e' utile per fare "reset mirati", ad esempio:
            - cancellare solo le flow di una specifica slice topologica (cookie per-slice)
            - cancellare tutte le flow relative al service slicing (cookie dedicato)
            senza dover fare wipe completo della tabella.

        Parametri:
            datapath:
                Oggetto datapath di Ryu (switch target).
            cookie (int):
                Cookie da matchare per identificare il gruppo di flow da rimuovere.
            cookie_mask (int):
                Maschera cookie usata nel confronto:
                - con mask = 0xFFFFFFFFFFFFFFFF il cookie deve combaciare esattamente
                - con maschere piu' "larghe" puoi cancellare famiglie di cookie (prefix-style)

        Ritorno:
            Nessuno. Invia un messaggio OFPFlowMod (DELETE) allo switch.

        Effetti collaterali:
            - Elimina flow dalla tabella dello switch (su tutte le tabelle, per impostazione).
            - Non altera direttamente lo stato Python del controller, ma cambia lo stato dello switch.

        Spiegazione:
            Riga 1:
                Recupera alias a ofproto e parser per costruire i messaggi OpenFlow.

            Riga 2:
                Invia un OFPFlowMod con:
                - table_id = OFPTT_ALL per applicare la cancellazione a tutte le tabelle
                - command = OFPFC_DELETE per indicare operazione di rimozione

            Riga 3:
                out_port = OFPP_ANY e out_group = OFPG_ANY:
                non filtriamo per porta o gruppo; vogliamo eliminare tutte le flow che matchano cookie/mask.

            Riga 4:
                cookie e cookie_mask determinano quali flow vengono selezionate per la cancellazione.

            Riga 5:
                match vuoto (OFPMatch()) significa "non filtrare sul match header":
                la selezione e' guidata principalmente dal cookie (piu' i campi delete).

            Riga 6:
                Il messaggio viene spedito allo switch tramite datapath.send_msg(...).
        """
        ofp = datapath.ofproto
        parser = datapath.ofproto_parser
        datapath.send_msg(
            parser.OFPFlowMod(
                datapath=datapath,
                table_id=ofp.OFPTT_ALL,
                command=ofp.OFPFC_DELETE,
                out_port=ofp.OFPP_ANY,
                out_group=ofp.OFPG_ANY,
                cookie=int(cookie),
                cookie_mask=int(cookie_mask),
                match=parser.OFPMatch(),
            )
        )

    def _delete_all_flows(self, datapath):
        """
        Cancella flow che matchano un certo cookie (con maschera).

        Scopo:
            Rimuovere selettivamente flow installate dal controller usando il campo cookie di OpenFlow.
            Questo e' utile per fare "reset mirati", ad esempio:
            - cancellare solo le flow di una specifica slice topologica (cookie per-slice)
            - cancellare tutte le flow relative al service slicing (cookie dedicato)
            senza dover fare wipe completo della tabella.

        Parametri:
            datapath:
                Oggetto datapath di Ryu (switch target).
            cookie (int):
                Cookie da matchare per identificare il gruppo di flow da rimuovere.
            cookie_mask (int):
                Maschera cookie usata nel confronto:
                - con mask = 0xFFFFFFFFFFFFFFFF il cookie deve combaciare esattamente
                - con maschere piu' "larghe" puoi cancellare famiglie di cookie (prefix-style)

        Ritorno:
            Nessuno. Invia un messaggio OFPFlowMod (DELETE) allo switch.

        Effetti collaterali:
            - Elimina flow dalla tabella dello switch (su tutte le tabelle, per impostazione).
            - Non altera direttamente lo stato Python del controller, ma cambia lo stato dello switch.

        Spiegazione:
            Riga 1:
                Recupera alias a ofproto e parser per costruire i messaggi OpenFlow.

            Riga 2:
                Invia un OFPFlowMod con:
                - table_id = OFPTT_ALL per applicare la cancellazione a tutte le tabelle
                - command = OFPFC_DELETE per indicare operazione di rimozione

            Riga 3:
                out_port = OFPP_ANY e out_group = OFPG_ANY:
                non filtriamo per porta o gruppo; vogliamo eliminare tutte le flow che matchano cookie/mask.

            Riga 4:
                cookie e cookie_mask determinano quali flow vengono selezionate per la cancellazione.

            Riga 5:
                match vuoto (OFPMatch()) significa "non filtrare sul match header":
                la selezione e' guidata principalmente dal cookie (piu' i campi delete).

            Riga 6:
                Il messaggio viene spedito allo switch tramite datapath.send_msg(...).
        """
        ofp = datapath.ofproto
        parser = datapath.ofproto_parser

        datapath.send_msg(
            parser.OFPFlowMod(
                datapath=datapath,
                table_id=ofp.OFPTT_ALL,          
                command=ofp.OFPFC_DELETE,        
                out_port=ofp.OFPP_ANY,          
                out_group=ofp.OFPG_ANY,          
                match=parser.OFPMatch()          
            )
        )

    def _packetout_single(self, datapath, data, in_port, out_port):
        """
        Invia un singolo PacketOut verso una porta specifica.

        Scopo:
            Emettere un pacchetto dallo switch su una singola porta (out_port),
            tipicamente quando:
            - vogliamo forwardare immediatamente un pacchetto ricevuto via PacketIn
            - prima/durante l'installazione di una flow (azione "one-shot")

        Parametri:
            datapath:
                Oggetto datapath di Ryu (switch che deve emettere il pacchetto).
            in_port (int):
                Porta di ingresso originale del pacchetto (da PacketIn).
                Serve come metadato e per alcune pipeline/azioni (es. OFPP_IN_PORT).
            out_port (int):
                Porta di uscita target su cui inviare il pacchetto.
            data (bytes):
                Payload Ethernet completo da inviare.
                In Ryu, questo e' spesso msg.data.

        Ritorno:
            Nessuno. Invia un OFPPacketOut allo switch.

        Effetti collaterali:
            Lo switch emette effettivamente il pacchetto sulla rete.
        """
        ofp = datapath.ofproto
        parser = datapath.ofproto_parser

        actions = [parser.OFPActionOutput(out_port)]

        datapath.send_msg(
            parser.OFPPacketOut(
                datapath=datapath,
                buffer_id=ofp.OFP_NO_BUFFER,
                in_port=in_port,
                actions=actions,
                data=data,
            )
        )

    def _ports_from_dstmac_map(self, dstmac_to_port: dict, in_port_exclude: int | None):
        """
        Estrae (e filtra) la lista di porte da una mappa dst_mac -> out_port.

        Scopo:
            Convertire una tabella di forwarding del tipo:
                { dst_mac: out_port, ... }
            in una lista di porte utilizzabile per un PacketOut multi-output.

            Questo e' usato soprattutto quando:
            - non esiste una entry specifica per il MAC di destinazione
            - quindi facciamo "flood controllato" sulle porte ammesse (es. dentro una slice)

        Parametri:
            dstmac_to_port (dict):
                Dizionario che mappa MAC destinazione -> porta di uscita.
                Nel tuo codice arriva tipicamente da:
                - SERVICE_VIDEO_MAP[dpid_str]   oppure
                - SERVICE_NONVIDEO_MAP[dpid_str]
                e rappresenta le porte "ammissibili" su quello switch per quel contesto.
            in_port_exclude (Optional[int]):
                Porta di ingresso da escludere.
                Serve a evitare che un broadcast/unknown unicast venga rimandato indietro
                sulla stessa porta da cui e' entrato (riflessione inutile).

        Ritorno:
            List[int]:
                Lista ordinata di porte (sorted), senza duplicati.

        Effetti collaterali:
            Nessuno.

        Spiegazione:
            Riga 1:
                Inizializza un set per accumulare le porte senza duplicati.

            Riga 2:
                Scorre tutti i valori (porte) presenti nella tabella dstmac_to_port.

            Riga 3:
                Aggiunge al set solo i valori che sono effettivamente interi (porte OpenFlow valide).

            Riga 4:
                Se e' stata fornita una porta di ingresso da escludere e questa e' nel set, la rimuove.

            Riga 5:
                Ritorna la lista di porte ordinate (sorted).
        """
        ports = set()

        for p in dstmac_to_port.values():
            if isinstance(p, int):
                ports.add(p)

        if in_port_exclude is not None and in_port_exclude in ports:
            ports.remove(in_port_exclude)

        return sorted(ports)

    def _install_proactive_video_rules(self, datapath=None):
        """
        Installa regole VIDEO proattive sugli switch del path video,
        usando match COMPLETO su eth_src + eth_dst + UDP dst=9999.

        Scopo:
            Garantire che il traffico video (definito come IPv4/UDP con destinazione porta 9999)
            venga sempre instradato nella VIDEO slice (SERVICE_VIDEO_MAP) anche se, prima,
            sono gia' state installate flow "non-video" piu' generiche (es. IPv4 + eth_src + eth_dst).

            In pratica: evitare il caso in cui una regola non-video pre-esistente catturi anche
            i pacchetti video e impedisca al controller di installare la flow video corretta.

        Quando si usa:
            Questa funzione va richiamata tipicamente:
            - quando si abilita il servizio video (POST /service/video/on)
            - quando uno switch si connette (switch_features_handler)
            cosi' le regole video esistono gia' nel dataplane PRIMA che arrivi traffico video.

        Parametri:
            datapath (Optional[Datapath]):
                Se passato, installa le regole solo su quello switch.
                Se None, installa su tutti gli switch attualmente connessi (self.datapaths).

        Ritorno:
            None

        Note importanti:
            - Applica SOLO se:
                * active_slicing_mode == SERVICE
                * SERVICE_SLICE_VIDEO e' abilitato in state.enabled_service
            - Applica SOLO sugli switch nel set:
                self._video_service_dpids
            cioe' quelli appartenenti al path video.

        Spiegazione:
            Riga 1-6:
                se non siamo in SERVICE mode o il video non e' abilitato, esce.

            Riga 7-9:
                Determina la lista di datapath su cui operare:
                - singolo datapath se fornito
                - altrimenti tutti i datapath connessi.

            Riga 10-12:
                Costruisce l'elenco delle coppie (src,dst) tra host video, escludendo src==dst.

            Riga 13:
                Cicla sui datapath e ricava dpid_str (formattato a 16 cifre).

            Riga 14-16:
                Filtra solo gli switch ammessi al video (path video): self._video_service_dpids.

            Riga 17-19:
                Recupera la tabella dst_mac -> out_port dalla SERVICE_VIDEO_MAP per quello switch.
                Se non presente o vuota, passa oltre.

            Riga 20-24:
                Per ogni coppia (src,dst):
                - ricava out_port dalla tabella usando dst
                - se manca, passa oltre (non sappiamo dove mandare quel dst su quello switch)

            Riga 25-30:
                Usa self._video_proactive_cache per evitare reinstall:
                chiave = (dpid_str, src, dst, out_port).
                Se la chiave e' gia' presente, non reinstalla.

            Riga 31-40:
                Costruisce match specifico video:
                    eth_type=0x0800 (IPv4)
                    ip_proto=17     (UDP)
                    udp_dst=9999
                    eth_src=src
                    eth_dst=dst
                e installa la flow con priority 200 e cookie SERVICE_COOKIE.
        """
        if self.state.active_slicing_mode != self.state.SERVICE:
            return
        if SERVICE_SLICE_VIDEO not in self.state.enabled_service:
            return

        dps = [datapath] if datapath is not None else list(self.datapaths.values())

        video_pairs = [(s, d) for s in VIDEO_HOST_MACS for d in VIDEO_HOST_MACS if s != d]

        for dp in dps:
            dpid_str = format(dp.id, 'd').zfill(16)

            if dpid_str not in self._video_service_dpids:
                continue

            parser = dp.ofproto_parser
            dstmac_to_port = SERVICE_VIDEO_MAP.get(dpid_str, {})
            if not dstmac_to_port:
                continue

            for (src, dst) in video_pairs:
                out_port = dstmac_to_port.get(dst)
                if out_port is None:
                    continue
                
                src_side = dstmac_to_port.get(src)
                dst_side = out_port  # uguale a dstmac_to_port.get(dst)

                # Se src e dst stanno sullo stesso lato di questo switch,
                # questo flusso NON attraversa lo switch -> regola inutile.
                if src_side is None or src_side == dst_side:
                    continue

                cache_key = (dpid_str, src, dst, int(out_port))
                if cache_key in self._video_proactive_cache:
                    continue
                self._video_proactive_cache.add(cache_key)

                match = parser.OFPMatch(
                    eth_type=0x0800,              # IPv4
                    ip_proto=17,                  # UDP
                    udp_dst=VIDEO_UDP_DST_PORT,   # 9999
                    eth_src=src,
                    eth_dst=dst
                )
                actions = [parser.OFPActionOutput(out_port)]

                self._add_flow(
                    datapath=dp,
                    priority=200,                
                    match=match,
                    actions=actions,
                    cookie=self.SERVICE_COOKIE
                )


    def _service_install_flow_for_packet(
        self,
        datapath,
        dpid_str: str,
        src: str,
        dst: str,
        out_port: int,
        is_video: bool,
        pkt,
    ):
        """
    Installa una flow in modalità SERVICE per il pacchetto corrente (video o non-video).

    Scopo:
        Quando siamo in service slicing e abbiamo deciso una out_port per (dst),
        questo metodo installa una regola OpenFlow in modo che i pacchetti successivi
        dello stesso "tipo" vengano inoltrati senza tornare al controller.

        - Caso NON-VIDEO:
            Match più generico (IPv4 + solo eth_dst),
            Priority bassa (10).

        Inoltre usa una cache per evitare reinstall ripetute della stessa flow.

    Parametri:
        datapath:
            Oggetto datapath Ryu (switch su cui installare la flow).
        dpid_str (str):
            DPID dello switch in formato stringa (usato in cache_key e per controllare path video).
        src (str):
            MAC sorgente del frame (usato solo nel match video).
        dst (str):
            MAC destinazione del frame (usato sia nel match video che non-video).
        out_port (int):
            Porta di uscita scelta (azione di forwarding).
        is_video (bool):
            True se il pacchetto e' stato classificato come video "effettivo" (UDP dst_port corretto + host video).
        pkt:
            Oggetto packet.Packet (qui NON viene usato nel corpo attuale; rimane come parametro per estensioni future).

    Ritorno:
        Nessuno. Se necessario invia un FlowMod (ADD) allo switch.

    Effetti collaterali:
        - Può modificare la tabella flow dello switch (installazione nuova flow).
        - Aggiorna self._service_flow_cache (cache delle flow già installate).

    Spiegazione:
        Riga 1:
            Recupera parser dal datapath per costruire match e azioni OpenFlow.

        Riga 2:
            Inizia il ramo VIDEO:
            entra solo se is_video=True e lo switch corrente e' nel set _video_service_dpids
            (cioe' fa parte del percorso su cui applichiamo regole video dedicate).

        Riga 3:
            le regole per il traffico video tra i video host vengono installate proattivamente.    

        Riga 4:
            Imposta priority=200 per far sì che la regola video vinca su regole più generiche.

        Riga 5:
            Ramo NON-VIDEO (o video su switch non appartenenti al path video):
            match più generico che guarda solo IPv4 e destinazione MAC (eth_dst=dst).

        Riga 6:
            Imposta priority=10 per la regola generica non-video.

        Riga 7:
            Costruisce una chiave di cache (cache_key) che rappresenta "questa flow":
              (dpid_str, priority, str(match), out_port, is_video)

        Riga 8:
            Se cache_key è già presente, significa che questa flow è stata già installata:
            ritorna subito evitando un FlowMod duplicato.

        Riga 9:
            Se non era in cache, aggiunge cache_key al set self._service_flow_cache.

        Riga 10:
            Costruisce la lista actions: output verso out_port.

        Riga 11:
            Chiama _add_flow(...) per inviare il FlowMod allo switch,
            usando cookie=self.SERVICE_COOKIE per marcare che questa flow appartiene al service slicing.
    """
        parser = datapath.ofproto_parser

        # VIDEO: le regole video vengono installate SOLO proattivamente.    
        if is_video and (dpid_str in getattr(self, "_video_service_dpids", set())):
            return
        else:
            match = parser.OFPMatch(
                eth_type=0x0800,
                eth_src=src,  
                eth_dst=dst
            )
            priority = 10

        cache_key = (dpid_str, priority, str(match), int(out_port), bool(is_video))
        if cache_key in self._service_flow_cache:
            return
        self._service_flow_cache.add(cache_key)

        actions = [parser.OFPActionOutput(out_port)]
        self._add_flow(
            datapath=datapath,
            priority=priority,
            match=match,
            actions=actions,
            cookie=self.SERVICE_COOKIE
        )

    def reset_everything(self):
        """
        Reset completo dello stato logico del controller e delle flow sugli switch connessi.

        Scopo:
            Riportare il controller in uno stato coerente dopo un cambio di contesto:
            - cambio day/night
            - cambio slicing mode (topology/service)
            - disabilitazione slice / video
            Il reset deve:
            1) azzerare le slice/service abilitati nello stato
            2) ricreare i mapper (day/night) per ripartire da regole pulite
            3) svuotare learning table e cache flow service
            4) cancellare tutte le flow dagli switch connessi e reinstallare la regola table-miss 

        Parametri:
            Nessuno.

        Ritorno:
            Nessuno.

        Spiegazione:
            Riga 1:
                Imposta enabled_topology a set vuoto: nessuna slice topologica considerata "abilitata" a livello di stato.

            Riga 2:
                Imposta enabled_service a set vuoto: nessun servizio considerato "abilitato".

            Riga 3:
                Esegue discard(SERVICE_SLICE_VIDEO) su enabled_service:
                e' ridondante dopo il set vuoto, ma rende esplicita l'intenzione di "spegnere video".

            Riga 4:
                Ricrea i mapper day/night da zero usando le regole importate (TOPOLOGY_*_SLICES_RULES).
                Questo azzera eventuali modifiche runtime fatte ai mapper (active_slice ecc.).

            Riga 5:
                Svuota il dizionario learned:
                elimina tutte le associazioni mac -> porta apprese finora.

            Riga 6:
                Svuota la cache _service_flow_cache:
                cosi' dopo il reset il controller puo' reinstallare flow service senza essere bloccato dalla cache.

            Riga 7:
                Itera su tutti i datapath attualmente connessi (self.datapaths.values()).

            Riga 8:
                Per ogni datapath:
                - cancella tutte le flow presenti (_delete_all_flows)
                - reinstalla la table-miss (_install_table_miss)
                cosi' il controller continua a ricevere PacketIn dopo il wipe.
        """
        # ---- reset stato slicing ----
        self.state.enabled_topology = set()       
        self.state.enabled_service = set()        
        self.state.enabled_service.discard(SERVICE_SLICE_VIDEO)  

        # ---- ricrea i mapper ----
        self.state.mappers = {
            ControllerState.DAY: MacToPortMapper(TOPOLOGY_DAY_SLICES_RULES),
            ControllerState.NIGHT: MacToPortMapper(TOPOLOGY_NIGHT_SLICES_RULES),
        }

        # ---- svuota learning/caches ----
        self.learned = {}
        self._service_flow_cache = set()

        # ---- wipe flow su switch connessi ----
        for dp in list(self.datapaths.values()):
            self._delete_all_flows(dp)        
            self._install_table_miss(dp)      

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        """
        Handler chiamato quando uno switch completa la handshake OpenFlow (fase features).

        Scopo:
            Appena lo switch si connette e invia le sue "features", il controller deve inizializzare
            la pipeline installando almeno la regola di table-miss.
            Senza table-miss, i pacchetti che non matchano flow potrebbero NON arrivare al controller
            (quindi niente PacketIn => niente logica applicativa).

        Parametri:
            ev:
                Evento Ryu EventOFPSwitchFeatures.
                ev.msg contiene il messaggio OpenFlow ricevuto, e in particolare:
                - ev.msg.datapath: riferimento allo switch (datapath) su cui installare le flow iniziali.

        Ritorno:
            Nessuno.

        Effetti collaterali:
            Modifica la tabella flow dello switch installando la table-miss (via _install_table_miss).
        """
        self._install_table_miss(ev.msg.datapath)
        self._install_proactive_video_rules(datapath=ev.msg.datapath)

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change_handler(self, ev):
        """
        Handler chiamato quando cambia lo stato della connessione di uno switch (datapath).

        Scopo:
            Tenere aggiornata la mappa self.datapaths (dpid -> datapath) con gli switch:
            - attualmente connessi e operativi (MAIN_DISPATCHER)
            - disconnessi/morti (DEAD_DISPATCHER)

            Questa mappa e' fondamentale per:
            - fare reset/flow cleanup su tutti gli switch (reset_everything)
            - installare/cancellare flow a seguito di comandi REST (slice_remove, video_off, ecc.)

        Parametri:
            ev:
                Evento Ryu EventOFPStateChange.
                Contiene:
                - ev.datapath: riferimento allo switch (datapath)
                - ev.state: stato nuovo (MAIN_DISPATCHER o DEAD_DISPATCHER)

        Ritorno:
            Nessuno.

        Effetti collaterali:
            Modifica self.datapaths:
            - aggiunge dp se lo switch entra in MAIN_DISPATCHER
            - rimuove dp se lo switch entra in DEAD_DISPATCHER

        Spiegazione:
            Riga 1:
                Il decoratore registra questo metodo per EventOFPStateChange e lo fa scattare
                solo quando lo stato e' MAIN_DISPATCHER o DEAD_DISPATCHER.

            Riga 2:
                Estrae il datapath dall'evento (ev.datapath) e lo assegna a dp per comodita'.

            Riga 3:
                Se lo switch e' in MAIN_DISPATCHER, significa che e' connesso e pronto:
                salva/aggiorna la entry in self.datapaths usando dp.id come chiave.

            Riga 4:
                Se lo switch e' in DEAD_DISPATCHER, significa che e' disconnesso:
                rimuove l'entry da self.datapaths in modo sicuro (pop con default None).
        """
        dp = ev.datapath

        if ev.state == MAIN_DISPATCHER:
            self.datapaths[dp.id] = dp
        elif ev.state == DEAD_DISPATCHER:
            self.datapaths.pop(dp.id, None)


    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        """
        Handler principale per i PacketIn.

        Scopo:
            Gestire ogni pacchetto che arriva al controller e decidere:
            - drop (se non ammesso dal slicing)
            - PacketOut one-shot (forward immediato)
            - installazione flow (per accelerare il forwarding pacchetti successivi)
            Supporta due modalità:
            1) SERVICE slicing: instrada video/non-video su mappe dedicate (SERVICE_*_MAP)
            2) TOPOLOGY slicing: instrada solo dentro slice attive usando mapper day/night

        Parametri:
            ev:
                Evento Ryu EventOFPPacketIn.
                ev.msg contiene:
                - datapath (switch sorgente)
                - match['in_port'] (porta d’ingresso)
                - data (frame Ethernet completo)

        Ritorno:
            Nessuno.

        Effetti collaterali:
            - In SERVICE mode: può installare flow via _service_install_flow_for_packet e inviare PacketOut.
            - In TOPOLOGY mode: aggiorna learning table e può installare flow L2 per quella coppia src/dst.
            - Può effettuare drop (ritorno immediato) per isolamento.

        Spiegazione riga per riga (nell'ordine del corpo della funzione):
            Riga 1:
                Estrae il messaggio OpenFlow dal PacketIn (ev.msg).

            Riga 2:
                Ricava il datapath (switch) dal messaggio.

            Riga 3:
                Alias a ofproto (costanti OpenFlow).

            Riga 4:
                Alias a parser (costruttori di match/azioni/messaggi OpenFlow).

            Riga 5:
                Estrae la porta di ingresso del pacchetto dal match (in_port).

            Riga 6:
                Calcola dpid_str come stringa a 16 cifre per indicizzare mappe per-switch.

            Riga 7:
                Parsifica msg.data in un oggetto packet.Packet di Ryu.

            Riga 8:
                Estrae il protocollo Ethernet (header L2) dal pacchetto.

            Riga 9-10:
                Se Ethernet non è presente (pacchetto non interpretabile), termina subito.

            Riga 11:
                Legge MAC sorgente dal frame Ethernet.

            Riga 12:
                Legge MAC destinazione dal frame Ethernet.

            Riga 13-15:
                Se è LLDP (ethertype 0x88cc), lo ignora e termina:
                LLDP è usato per discovery e non fa parte del forwarding logico applicativo.

            Riga 16-19:
                Se è ARP (ethertype 0x0806), termina:
                ARP è delegato a Mininet (autoStaticArp=True), quindi il controller non gestisce ARP.

            Riga 20:
                Estrae header IPv4 (se presente).

            Riga 21:
                Estrae header UDP (se presente).

            Riga 22:
                is_video è True se:
                - esiste IPv4
                - esiste UDP
                - e la udp dst_port coincide con VIDEO_UDP_DST_PORT

            Riga 23:
                Controlla se la sorgente appartiene all’insieme di host “video”.

            Riga 24:
                Controlla se la destinazione appartiene all’insieme di host “video”.

            Riga 25:
                effective_is_video è True solo se:
                - il pacchetto è video per porta UDP
                - e sia src che dst sono host video (riduce falsi positivi)

            Riga 26:
                Entra nel ramo SERVICE se la modalità slicing attiva è SERVICE.

            Riga 27-28:
                Se il servizio video non è abilitato (SERVICE_SLICE_VIDEO non in enabled_service),
                fa drop immediato (isolamento totale del servizio).

            Riga 29:
                Seleziona la tabella corretta per questo switch:
                - SERVICE_VIDEO_MAP[dpid_str] se effective_is_video
                - altrimenti SERVICE_NONVIDEO_MAP[dpid_str]
                Se manca l’entry per dpid_str, usa {}.

            Riga 30:
                Cerca la out_port per la destinazione MAC dentro la tabella selezionata.

            Riga 31:
                Se out_port è nota (non None), allora possiamo instradare puntualmente.

            Riga 32-40:
                Installa una flow coerente col pacchetto (video o non-video) chiamando
                _service_install_flow_for_packet(...).

            Riga 41:
                Esegue forwarding immediato: PacketOut singolo verso out_port.

            Riga 42:
                Altrimenti (out_port sconosciuta), si droppa il pacchetto

            Riga 45:
                Ritorna: in SERVICE mode la gestione termina qui.

            Riga 46:
                In TOPOLOGY mode: verifica se esiste almeno una slice attiva nel mapper della modalità corrente.

            Riga 47-48:
                Se nessuna slice è attiva, fa drop immediato (isolamento completo).

            Riga 49:
                Assicura che esista la learning table per questo switch (dpid_str) usando setdefault.

            Riga 50:
                Aggiorna la learning table: associa src_mac -> in_port per questo switch.

            Riga 51:
                Recupera il mapper corrispondente a DAY/NIGHT attivo.

            Riga 52:
                Risolve (out_port, slice_no) usando le regole della slice:
                ritorna None se la comunicazione non è ammessa o non esiste regola.

            Riga 53-54:
                Se out_port è None, fa drop immediato.

            Riga 55:
                Prepara azioni: output verso out_port.

            Riga 56:
                Prepara match L2 per installare una flow: in_port + eth_src + eth_dst.

            Riga 57-58:
                Se out_port non è OFPP_FLOOD, installa una flow con:
                - priority=10
                - cookie specifico della slice (self._topology_cookie(slice_no))

            Riga 59-67:
                Invia un PacketOut con le azioni calcolate, così il pacchetto corrente viene inoltrato subito.
        """
        msg = ev.msg
        datapath = msg.datapath
        ofp = datapath.ofproto
        parser = datapath.ofproto_parser

        in_port = msg.match['in_port']

        dpid_str = format(datapath.id, 'd').zfill(16)

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)

        if eth is None:
            return

        src = eth.src
        dst = eth.dst

        # ignora LLDP 
        if eth.ethertype == 0x88cc:
            return


        if eth.ethertype == 0x0806:
            # ARP gestito da Mininet (autoStaticArp=True) -> nessuna logica nel controller
            return

        ip4 = pkt.get_protocol(ipv4.ipv4)
        udpp = pkt.get_protocol(udp.udp)
        is_video = bool(ip4 and udpp and udpp.dst_port == VIDEO_UDP_DST_PORT)

        src_is_video_host = (src in VIDEO_HOST_MACS)
        dst_is_video_host = (dst in VIDEO_HOST_MACS)
        effective_is_video = bool(is_video and src_is_video_host and dst_is_video_host)

        if self.state.active_slicing_mode == self.state.SERVICE:
            if SERVICE_SLICE_VIDEO not in self.state.enabled_service:
                return

            table = SERVICE_VIDEO_MAP.get(dpid_str, {}) if effective_is_video else SERVICE_NONVIDEO_MAP.get(dpid_str, {})

            out_port = table.get(dst)

            if out_port is not None:
                self._service_install_flow_for_packet(
                    datapath=datapath,
                    dpid_str=dpid_str,
                    src=src,
                    dst=dst,
                    out_port=out_port,
                    is_video=effective_is_video,
                    pkt=pkt,
                )

                self._packetout_single(datapath, msg.data, in_port, out_port)
            else:

                return

            return  

        any_topo = any(self.state.mappers[self.state.active_mode].active_slice)
        if not any_topo:
            return

        self.learned.setdefault(dpid_str, {})
        self.learned[dpid_str][src] = in_port

        mapper = self.state.mappers[self.state.active_mode]
        out_port, slice_no = self._topology_resolve_out_port_and_slice(mapper, dpid_str, src_mac=src, dst_mac=dst)

        if out_port is None:
            return

        actions = [parser.OFPActionOutput(out_port)]

        match = parser.OFPMatch(in_port=in_port, eth_src=src, eth_dst=dst)

        if out_port != ofp.OFPP_FLOOD:
            self._add_flow(datapath, priority=10, match=match, actions=actions, cookie=self._topology_cookie(slice_no))

        datapath.send_msg(
            parser.OFPPacketOut(
                datapath=datapath,
                buffer_id=ofp.OFP_NO_BUFFER,
                in_port=in_port,
                actions=actions,
                data=msg.data,
            )
        )


class ControllerServer(ControllerBase):
    """
    Controller HTTP (WSGI) che espone tutte le API REST utilizzate dalla UI e dagli strumenti esterni.

    Scopo generale
    --------------
    Fornire un'interfaccia HTTP/JSON per:
      - ottenere lo stato del controller (mode, slicing, slice attive, video on/off)
      - modificare modalità e slicing
      - abilitare/disabilitare slice topologiche
      - attivare/disattivare il servizio video
      - controllare la simulazione di traffico (start/stop/status)
      - fornire informazioni statiche per la UI (lista host, lista porte)
    """


    def __init__(self, req, link, data, **config):
        super().__init__(req, link, data, **config)
        self.controller = data["controller_instance"]
        self.state = self.controller.state

    @route('cors', '/{path:.*}', methods=['OPTIONS'])
    def cors_preflight(self, req, **kwargs):
        return self._cors(Response(status=200))


    @route('status', '/status', methods=['GET'])
    def status(self, req, **kwargs):
        body = {
            "active_mode": "day" if self.state.active_mode == self.state.DAY else "night",
            "slicing_mode": "topology" if self.state.active_slicing_mode == self.state.TOPOLOGY else "service",
            "enabled_topology": [
                i + 1
                for i, v in enumerate(self.state.mappers[self.state.active_mode].active_slice)
                if v == 1
            ],
            "video_enabled": (SERVICE_SLICE_VIDEO in self.state.enabled_service),
        }
        return self._json(body)

    # -------- Traffic simulation --------
    @route('traffic_status', '/sim/traffic/status', methods=['GET'])
    def traffic_status(self, req):
        try:
            return self._json(self.controller.traffic_sim.status())
        except Exception as e:
            return self._json({"error": str(e)}, status=500)

    @route('traffic_start', '/sim/traffic/start', methods=['POST'])
    def traffic_start(self, req):
        data = self._body(req)
        sim_id = (data.get("sim_id") or "").strip()
        if not sim_id:
            return self._json({"error": "missing sim_id"}, status=400)

        ok, msg = self.controller.traffic_sim.start(sim_id)
        if ok:
            return self._json({"ok": True, "status": "running", "sim_id": sim_id})

        return self._json({"ok": False, "error": msg, "sim_id": sim_id}, status=409)

    @route('traffic_stop_all', '/sim/traffic/stop_all', methods=['POST'])
    def traffic_stop_all(self, req):
        data = self._body(req)
        reason = (data.get("reason") or "context_change")
        try:
            self.controller.traffic_sim.stop_all(str(reason))
            return self._json({"ok": True, "stopped": True, "reason": str(reason)})
        except Exception as e:
            return self._json({"ok": False, "error": str(e)}, status=500)

    # -------- UI port defs --------
    @route('ui_port_defs', '/ui/port-defs', methods=['GET'])
    def ui_port_defs(self, req, **kwargs):
        try:
            import topology_defs as topo
        except Exception as e:
            return self._json({"error": "cannot import topology", "detail": str(e)}, 500)

        port_defs = []

        for swA, swB, portA, portB, _bw in getattr(topo, 'LINKS', []):
            port_defs.append({
                "lineId": topo.ui_line_id_for_switch_link(swA, swB),
                "a": swA, "portA": int(portA),
                "b": swB, "portB": int(portB)
            })

        for host, sw, _hostP, swP in getattr(topo, 'HOST_LINKS', []):
            port_defs.append({
                "lineId": topo.ui_line_id_for_host_link(host, sw),
                "switch": sw,
                "port": int(swP)
            })

        return self._json({"port_defs": port_defs})

    # -------- UI host defs --------
    @route('ui_host_defs', '/ui/host-defs', methods=['GET'])
    def ui_host_defs(self, req, **kwargs):
        try:
            import topology_defs as topo
        except Exception as e:
            return self._json({"error": "cannot import topology", "detail": str(e)}, 500)

        return self._json({"hosts": topo.ui_host_defs()})

    # -------- Mode day/night (RESET) --------
    @route('mode', '/mode/set', methods=['POST'])
    def set_mode(self, req):
        self.controller.traffic_sim.stop_all('mode_change')

        data = self._body(req)
        mode = str(data.get("mode", "")).lower()

        if mode == "day":
            self.state.active_mode = self.state.DAY
        elif mode == "night":
            self.state.active_mode = self.state.NIGHT
        else:
            return self._json({"error": "mode must be day|night"}, 400)

        self.controller.reset_everything()
        return self._json({"ok": True, "mode": mode, "reset": True})

    # -------- Slicing mode set (RESET) --------
    @route('slicing', '/slicing/set', methods=['POST'])
    def slicing_set(self, req):
        self.controller.traffic_sim.stop_all('slicing_mode_change')

        data = self._body(req)
        mode = str(data.get("mode", "")).lower()

        if mode == "topology":
            self.state.active_slicing_mode = self.state.TOPOLOGY
        elif mode == "service":
            self.state.active_slicing_mode = self.state.SERVICE
        else:
            return self._json({"error": "mode must be topology|service"}, 400)

        self.controller.reset_everything()
        return self._json({"ok": True, "slicing_mode": mode, "reset": True})

    # -------- Topology slice add/remove --------
    @route('slice_add', '/slice/add', methods=['POST'])
    def slice_add(self, req):
        data = self._body(req)
        slice_number = int(data.get("slice", 0))

        if slice_number < 1 or slice_number > 3:
            return self._json({"error": "slice must be 1..3"}, 400)

        ok = self.state.mappers[self.state.active_mode].add_slice(slice_number)

        if ok:
            self.state.enabled_topology = {
                i + 1 for i, v in enumerate(self.state.mappers[self.state.active_mode].active_slice) if v == 1
            }

        return self._json({"ok": ok, "enabled_topology": sorted(self.state.enabled_topology)})

    @route('slice_remove', '/slice/remove', methods=['POST'])
    def slice_remove(self, req):
        self.controller.traffic_sim.stop_all('slice_removed')

        data = self._body(req)
        slice_number = int(data.get("slice", 0))

        if slice_number < 1 or slice_number > 3:
            return self._json({"error": "slice must be 1..3"}, 400)

        ok = self.state.mappers[self.state.active_mode].remove_slice(slice_number)

        self.state.enabled_topology = {
            i + 1 for i, v in enumerate(self.state.mappers[self.state.active_mode].active_slice) if v == 1
        }
        if ok and self.state.active_slicing_mode == self.state.TOPOLOGY:
            cookie = self.controller._topology_cookie(slice_number)
            for dp in self.controller.datapaths.values():
                self.controller._delete_flows_by_cookie(dp, cookie)


        return self._json({"ok": ok, "enabled_topology": sorted(self.state.enabled_topology)})

    @route('video_on', '/service/video/on', methods=['POST'])
    def video_on(self, req):
        self.state.enabled_service.add(SERVICE_SLICE_VIDEO)
        self.controller._install_proactive_video_rules()
        return self._json({"ok": True, "video_enabled": True})

    @route('video_off', '/service/video/off', methods=['POST'])
    def video_off(self, req):
        self.controller.traffic_sim.stop_all('video_slice_off')

        self.state.enabled_service.discard(SERVICE_SLICE_VIDEO)

        if self.state.active_slicing_mode == self.state.SERVICE:
            for dp in list(self.controller.datapaths.values()):
                self.controller._delete_all_flows(dp)
                self.controller._install_table_miss(dp)

            self.controller.learned = {}
            self.controller._service_flow_cache = set()
            self.controller._video_proactive_cache = set()


        return self._json({"ok": True, "video_enabled": False, "isolated": True})

    @route('service_add', '/service/add', methods=['POST'])
    def service_add(self, req):
        data = self._body(req)
        name = str(data.get("service", "")).strip()

        if name.lower() == "video":
            return self.video_on(req)

        return self._json({"error": "use /service/video/on (or service=Video)"}, 400)

    @route('service_remove', '/service/remove', methods=['POST'])
    def service_remove(self, req):
        data = self._body(req)
        name = str(data.get("service", "")).strip()

        if name.lower() == "video":
            return self.video_off(req)

        return self._json({"error": "use /service/video/off (or service=Video)"}, 400)

    # -------- Helpers JSON/CORS --------
    def _body(self, req):
        try:
            return json.loads(req.body.decode('utf-8')) if req.body else {}
        except Exception:
            return {}

    def _json(self, obj, status=200):
        resp = Response(
            content_type='application/json',
            body=json.dumps(obj).encode('utf-8'),
            status=status
        )
        return self._cors(resp)

    def _cors(self, resp: Response) -> Response:
        resp.headers.add('Access-Control-Allow-Origin', '*')
        resp.headers.add('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
        resp.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        return resp
