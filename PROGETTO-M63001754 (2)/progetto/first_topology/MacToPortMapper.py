#!/usr/bin/python3
# -*- coding: utf-8 -*-

from ProblemConstants import ProblemConstants as st


class MacToPortMapper:
    """
    Gestore di slice topologiche e risoluzione MAC -> porta per switch (dpid).

    Scopo:
        Questa classe rappresenta un "mapper" che:
          1) tiene traccia di quali slice sono attive (active_slice)
          2) blocca l'attivazione di slice incompatibili (adjacency_list)
          3) mantiene una mappa cumulativa (self.map) contenente SOLO le entry delle slice attive
          4) fornisce funzioni di risoluzione:
             - resolve_out_port: unicast consentito dentro stessa slice
             - resolve_broadcast_ports: flood limitato alla slice del sorgente

    Attributi principali:
        self.slices_rules:
            Lista di regole per slice (una dict per slice).
        self.slice_hosts:
            Lista di set: per ogni slice, l'insieme dei MAC considerati "appartenenti" a quella slice.
        self.map:
            Mappa cumulativa costruita dalle slice attive:
              map[dpid][dst_mac] = out_port
        self.active_slice:
            Lista di flag 0/1 lunga NUM_SLICES: indica quali slice sono attive.
        self.adjacency_list:
            Lista di liste: adjacency_list[i] contiene indici di slice incompatibili con la slice i.

    Nota:
        La numerazione utente delle slice e' 1-based (1..NUM_SLICES).
    """

    def __init__(self, slices_rules: list[dict] | None = None):
        """
        Costruttore.

        Scopo:
            Inizializzare:
              - le regole di forwarding per slice (slices_rules)
              - il set di host per slice (slice_hosts)
              - lo stato delle slice attive (active_slice)
              - la struttura di incompatibilità tra slice (adjacency_list)

        Parametri:
            slices_rules (list[dict] | None):
                Se fornito, usa questa lista di regole.
                Se None, carica le regole di default da ProblemConstants.SLICES_RULES.

        Ritorno:
            Nessuno.

        Effetti collaterali:
            Inizializza le strutture interne della classe.

        Spiegazione:
            Riga 1:
                Importa le regole di default (DEFAULT_RULES) da ProblemConstants.

            Riga 2:
                Salva in self.slices_rules le regole passate dall'esterno oppure quelle di default.

            Riga 3:
                Inizializza self.slice_hosts come lista vuota.

            Riga 4-12:
                Per ogni rules di slice:
                  - crea un set hosts
                  - scorre tutti i dpid e le rispettive mappe dst->port
                  - accumula tutte le dst_mac (chiavi) nel set hosts
                  - salva hosts in self.slice_hosts
                Questo produce, per ogni slice, l'insieme dei MAC "presenti" nelle regole della slice.

            Riga 13:
                Inizializza self.map come dict vuoto (sarà popolato aggiungendo slice).

            Riga 14:
                Inizializza active_slice a tutti zeri: nessuna slice attiva inizialmente.

            Riga 15:
                Inizializza adjacency_list come lista di liste vuote, una per slice.

            Riga 16:
                Legge la definizione delle incompatibilità (st.INCOMPATIBLE_SLICES).

            Riga 17-25:
                Normalizza INCOMPATIBLE_SLICES in una lista di coppie (a, b):
                  - se inc e' dict: per ogni a -> [b1,b2,...] crea coppie (a,b)
                  - altrimenti assume inc già iterabile di coppie

            Riga 26-29:
                Per ogni coppia (a,b):
                  - converte da numerazione 1-based a 0-based
                  - aggiunge b nella lista di adiacenza di a e viceversa
        """
        from ProblemConstants import SLICES_RULES as DEFAULT_RULES
        self.slices_rules = slices_rules if slices_rules is not None else DEFAULT_RULES

        self.slice_hosts: list[set[str]] = []
        for rules in self.slices_rules:
            hosts = set()
            if isinstance(rules, dict):
                for _dpid, dstmap in rules.items():
                    if isinstance(dstmap, dict):
                        hosts.update(dstmap.keys())
            self.slice_hosts.append(hosts)

        self.map: dict[str, dict[str, int]] = {}
        self.active_slice = [0] * st.NUM_SLICES
        self.adjacency_list = [[] for _ in range(st.NUM_SLICES)]

        inc = st.INCOMPATIBLE_SLICES
        if isinstance(inc, dict):
            items = []
            for a, bs in inc.items():
                for b in bs:
                    items.append((a, b))
        else:
            items = list(inc)

        for a, b in items:
            a, b = a - 1, b - 1
            self.adjacency_list[a].append(b)
            self.adjacency_list[b].append(a)

    def verify_add_compatibility(self, slice_number: int) -> bool:
        """
        Verifica se una slice può essere attivata senza conflitti.

        Scopo:
            Controllare la lista di incompatibilità (adjacency_list) e impedire l'attivazione
            di una slice se una qualunque slice incompatibile è già attiva.

        Parametri:
            slice_number (int):
                Numero slice in input (1-based).

        Ritorno:
            bool:
                True  -> slice attivabile (nessun conflitto con slice attive)
                False -> slice non attivabile (conflitto rilevato)

        Effetti collaterali:
            Nessuno.

        Spiegazione:
            Riga 1:
                Scorre tutti gli indici di slice incompatibili con slice_number-1.

            Riga 2:
                Se una slice incompatibile risulta attiva (active_slice[i] == 1),
                ritorna False immediatamente.

            Riga 3:
                Se finisce il ciclo senza conflitti, ritorna True.
        """
        for i in self.adjacency_list[slice_number - 1]:
            if self.active_slice[i] == 1:
                return False
        return True

    def add_slice(self, slice_number: int) -> bool:
        """
        Attiva una slice e aggiorna la mappaa self.map.

        Scopo:
            Rendere operativa una slice (flag active_slice=1) e inserire in self.map
            tutte le entry (dpid -> dst_mac -> out_port) previste dalle rules di quella slice.

        Parametri:
            slice_number (int):
                Numero slice 1-based (1..NUM_SLICES).

        Ritorno:
            bool:
                True  -> slice attivata con successo (compatibile e dentro range)
                False -> slice non attivata (range invalido o incompatibile)

        Effetti collaterali:
            - Modifica self.active_slice.
            - Modifica self.map aggiungendo entry della slice.

        Spiegazione:
            Riga 1-2:
                Verifica che slice_number sia nel range valido (1..NUM_SLICES),
                altrimenti ritorna False.

            Riga 3:
                Controlla compatibilità chiamando verify_add_compatibility(slice_number).

            Riga 4:
                Se compatibile, marca la slice come attiva (active_slice[slice_number-1] = 1).

            Riga 5:
                Recupera le regole della slice:
                  - se l'indice esiste in slices_rules, usa quella dict
                  - altrimenti usa {} (fallback robusto)

            Riga 6-12:
                Per ogni dpid e per ogni dst nella slice:
                  - crea self.map[dpid] se non esiste
                  - inserisce/aggiorna self.map[dpid][dst] = out_port

            Riga 13:
                Ritorna True per indicare successo.

            Riga 14:
                Se non compatibile, ritorna False.
        """
        if not (1 <= slice_number <= st.NUM_SLICES):
            return False

        if self.verify_add_compatibility(slice_number):
            self.active_slice[slice_number - 1] = 1

            rules = self.slices_rules[slice_number - 1] if slice_number - 1 < len(self.slices_rules) else {}
            for dpid in rules:
                for dst in rules[dpid]:
                    if dpid not in self.map:
                        self.map[dpid] = {}
                    self.map[dpid][dst] = rules[dpid][dst]
            return True
        return False

    def remove_slice(self, slice_number: int) -> bool:
        """
        Disattiva una slice e ricostruisce coerentemente la mappa cumulativa.

        Scopo:
            Spegnere una slice (active_slice=0) e rimuovere le sue entry dalla mappa.
            Poi ricostruisce self.map riprocessando tutte le slice rimaste attive,
            così da mantenere coerenza anche in presenza di sovrapposizioni/override.

        Parametri:
            slice_number (int):
                Numero slice 1-based.

        Ritorno:
            bool:
                True  -> slice rimossa (range valido)
                False -> range non valido

        Effetti collaterali:
            - Modifica self.active_slice.
            - Modifica self.map rimuovendo entry e poi ricostruendola.

        Spiegazione:
            Riga 1-2:
                Controlla range (1..NUM_SLICES), altrimenti ritorna False.

            Riga 3:
                Disattiva la slice (active_slice[slice_number-1] = 0).

            Riga 4:
                Recupera le rules della slice rimossa (fallback {} se non presenti).

            Riga 5-11:
                Rimuove da self.map tutte le entry (dpid,dst) appartenenti a quella slice:
                  - se self.map contiene dpid e dst, elimina la chiave
                  - se dopo la rimozione self.map[dpid] è vuoto, elimina anche dpid

            Riga 12:
                Salva uno snapshot dello stato attivo corrente (current).

            Riga 13:
                Svuota completamente self.map.

            Riga 14:
                Azzera active_slice, per ricostruire da zero in modo deterministico.

            Riga 15-17:
                Per ogni slice che era attiva nello snapshot current:
                  - richiama add_slice(i+1)
                Ricostruisce self.map in modo coerente.

            Riga 18:
                Ritorna True.
        """
        if not (1 <= slice_number <= st.NUM_SLICES):
            return False

        self.active_slice[slice_number - 1] = 0

        rules = self.slices_rules[slice_number - 1] if slice_number - 1 < len(self.slices_rules) else {}
        for dpid in rules:
            for dst in rules[dpid]:
                if dpid in self.map and dst in self.map[dpid]:
                    del self.map[dpid][dst]
                if dpid in self.map and len(self.map[dpid]) == 0:
                    del self.map[dpid]

        current = self.active_slice[:]
        self.map.clear()
        self.active_slice = [0] * st.NUM_SLICES
        for i, on in enumerate(current):
            if on == 1:
                self.add_slice(i + 1)
        return True

    def get_map(self):
        """
        Restituisce la mappa corrente.

        Parametri:
            Nessuno.

        Ritorno:
            dict:
                self.map nel formato:
                  { dpid: { dst_mac: out_port, ... }, ... }
        """
        return self.map
