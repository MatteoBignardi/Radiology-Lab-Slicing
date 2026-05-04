#!/usr/bin/python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import re
import json
import time
import threading
import subprocess
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class SimState:
    """
    Rappresentazione strutturata dello stato runtime di una singola simulazione di traffico.

    Scopo:
        Questa classe modella tutte le informazioni necessarie per descrivere lo stato
        di una simulazione mentre viene avviata, eseguita o terminata.
        È una struttura dati aggiornata dinamicamente dal TrafficSimulationManager.

    Parametri / Campi:
        sim_id (str):
            Identificatore univoco della simulazione 
            È la chiave usata dal sistema per riferirsi alla simulazione.

        label (str):
            Descrizione leggibile destinata all’interfaccia utente

        src_host (str):
            Nome dell’host sorgente della simulazione (nel namespace Mininet).

        dst_host (str):
            Nome dell’host destinazione della simulazione.

        kind (str):
            Tipologia della simulazione.
            Valori previsti:
                - "dicom_store" → invio DICOM C-STORE verso PACS
                - "dicom_qr"    → DICOM Query/Retrieve (C-FIND / C-MOVE)
                - "video"       → streaming video UDP

        slice_id (int):
            Identificatore della slice topologica richiesta.
            1 = radiology, 2 = security

        status (str):
            Stato corrente della simulazione.
            Valori:
                - "idle"        → mai avviata
                - "running"     → in esecuzione
                - "interrupted" → interrotta manualmente
                - "terminated"  → completata normalmente
                - "error"       → terminata per errore

        error (Optional[str]):
            Messaggio di errore, se presente.

        started_at (Optional[float]):
            Timestamp dell’istante in cui la simulazione è stata avviata.

        ended_at (Optional[float]):
            Timestamp dell’istante in cui la simulazione è terminata.

        processes (List[subprocess.Popen]):
            Lista dei processi client/traffic-generator avviati per questa simulazione.

        server_processes (List[subprocess.Popen]):
            Lista dei processi server (es. dcmqrscp) avviati automaticamente quando mancano listener.

        capture_proc (Optional[subprocess.Popen]):
            Processo tcpdump attivo per la cattura pacchetti, se previsto.

        capture_file (Optional[str]):
            Percorso del file .pcap generato da tcpdump per questa simulazione.
    """
    sim_id: str
    label: str
    src_host: str
    dst_host: str
    kind: str          
    slice_id: int       
    status: str = "idle" 
    error: Optional[str] = None
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    processes: List[subprocess.Popen] = field(default_factory=list)
    server_processes: List[subprocess.Popen] = field(default_factory=list)
    capture_proc: Optional[subprocess.Popen] = None
    capture_file: Optional[str] = None


class TrafficSimulationManager:
    """
    Classe responsabile della gestione completa delle simulazioni di traffico
    all’interno di una topologia Mininet basata su slicing topologico.

    Scopo generale:
        - Fornire un'interfaccia ad alto livello per avviare, fermare e monitorare
          simulazioni di traffico di tipo DICOM e video UDP.
        - Interagire con i namespace di rete di Mininet tramite mnexec.
        - Gestire la cattura dei pacchetti tramite tcpdump.
        - Coordinare processi esterni.

    Componenti interni importanti:
        - self.ctrl:
            Istanza del controller, usata per verificare slicing e permessi.
        - self._lock:
            RLock globale per serializzare tutte le operazioni critiche.
        - self.assets_dir:
            Cartella contenente file DICOM, video e pcaps.
        - self.sims:
            Dizionario sim_id → SimState.
        - self._pid_map_cache:
            Cache della mappa host Mininet → PID reali.
        - self._auto_pacs_scps:
            Cache dei processi dcmqrscp avviati automaticamente.
    """

    # Percorso del file JSON che mappa:
    #   nome_host_mininet -> PID del processo dell'host
    # Tale mappa e' necessaria per eseguire comandi "dentro" il namespace di rete dell'host tramite mnexec.
    PID_MAP_PATH = Path("/tmp/mn_host_pids.json")


    # Dizionario di alias per i nomi host.
    #
    # Questa struttura NON e' indispensabile al funzionamento del sistema.
    # E' una funzionalita' aggiuntiva di comodita', pensata solo per rendere
    # piu' flessibile l'assegnazione dei nomi degli host.
    HOST_ALIASES = {
        # Security / cameras
        "cam1": "hCam1",
        "cam2": "hCam2",
        "hcam1": "hCam1",
        "hcam2": "hCam2",
        "nvr": "nvr",
        "lcs": "lCS",
        "lCS": "lCS",
        # Radiology
        "pacs": "hPACS",
        "hpacs": "hPACS",
        "imgacq1": "hImgDev1",
        "imgacq2": "hImgDev2",
        "imgdev1": "hImgDev1",
        "imgdev2": "hImgDev2",
        "radws1": "hRadWS1",
        "radws2": "hRadWS2",
        "ris": "hRIS",
        "broker": "hBroker",
    }

    # Definizione statica delle simulazioni disponibili.
    SIMS_DEF = {
        # Radiology (slice 1)
        "imgacq1_pacs": dict(
            label="Img Acquisition 1 → PACS (DICOM C-STORE)",
            src="hImgDev1", dst="hPACS", kind="dicom_store", slice_id=1,
            # Parametri PACS per DCMTK: AET e porta.
            pacs_aet="DCMQRSCP", pacs_port=4243,
            src_aet="IMGACQ1_AE",
            dicom_rel=Path("dicom/Imaging/ImgAcquisitionDevice1"),
        ),
        "radws1_pacs": dict(
            label="Radiology WS 1 ↔ PACS (DICOM Q/R)",
            src="hRadWS1", dst="hPACS", kind="dicom_qr", slice_id=1,
            # Parametri PACS per DCMTK: AET e porta.
            pacs_aet="DCMQRSCP", pacs_port=4243,
            # AET della workstation; ws_scp_port e' un parametro di configurazione (porta SCP locale).
            ws_aet="RADWS1_AE", ws_scp_port=105,
            # Campo di una vecchia versione del file: descrive l'intento della ricerca C-FIND (livello STUDY).
            # In questa versione NON viene letto per costruire i comandi
            query=dict(QueryRetrieveLevel="STUDY", PatientName="*"),
            # Campo di una vecchia versione del file: descrive un "codice univoco" che identifica un esame/paziente/studio specifico.
            # In questa versione NON viene letto per costruire i comandi
            study_uid=None,
            # Campo di una vecchia versione del file: descrive il metodo con cui si vorrebbero recuperare le immagini dopo la query.
            #   "move" significa: chiedere al PACS di INVIARE le immagini a una destinazione (operazione DICOM C-MOVE).
            retrieve="move",
        ),

        # Security (slice 2)
        "cam1_nvr": dict(
            label="Camera 1 → NVR (video)",
            src="hCam1", dst="nvr", kind="video", slice_id=2,
            video_file=Path("video/hCam1.mp4"), udp_port=5001
        ),
        "cam2_lcs": dict(
            label="Camera 2 → Local CS (video)",
            src="hCam2", dst="lCS", kind="video", slice_id=2,
            video_file=Path("video/hCam2.mp4"), udp_port=5002
        ),
    }

    # Mappa sim_id -> interfaccia (tipicamente di uno switch) su cui avviare tcpdump.
    # Serve a catturare il traffico pertinente alla simulazione su un punto significativo della topologia.
    CAPTURE_IFACE_BY_SIM = {
        "imgacq1_pacs": "s2-eth3",
        "radws1_pacs": "s1-eth3",
        "cam1_nvr": "s6-eth3",
        "cam2_lcs": "s7-eth2",
    }

    # Tempo minimo (in secondi) per cui tcpdump deve restare attivo anche se la simulazione termina subito.
    MIN_CAPTURE_SEC = 2.5   
    # Tempo aggiuntivo (in secondi) di cattura dopo la fine/interruzione della simulazione.
    POST_CAPTURE_SEC = 1.5  
    # Durata  (in secondi) dello streaming video.
    VIDEO_DURATION_SEC = 60

    def __init__(self, controller_instance, assets_dir: Optional[Path] = None):
        """
        Inizializza il TrafficSimulationManager.

        Spiegazione:
            Riga 1:
                Memorizza il riferimento al controller nell'istanza, per poter leggere lo stato di slicing nei metodi successivi.

            Riga 2:
                Crea un lock per rendere thread-safe le operazioni che leggono/modificano lo stato del manager.

            Riga 3:
                Imposta la directory degli asset: se il parametro e' presente lo usa, altrimenti calcola un valore di default.

            Riga 4:
                Inizializza la cache della mappa host->PID di Mininet come "non caricata"; verra' letta da file solo quando serve.

            Riga 5:
                Inizializza una cache dei processi PACS/SCP eventualmente avviati automaticamente, per evitare duplicati.

            Riga 6:
                Inizializza il dizionario che conterra' lo stato runtime di tutte le simulazioni (sim_id -> SimState).

            Riga 7:
                Scorre la definizione statica delle simulazioni e prepara lo stato iniziale per ciascuna di esse.

            Riga 8:
                Per ogni simulazione crea un oggetto SimState con i campi base (identificativi, host, tipo, slice)
                e lo inserisce nel dizionario self.sims; gli altri campi restano ai valori di default del dataclass.
        """

        self.ctrl = controller_instance
        self._lock = threading.RLock()
        self.assets_dir = assets_dir or self._resolve_assets_dir()
        self._pid_map_cache: Optional[Dict[str, int]] = None

        self._auto_pacs_scps: Dict[Tuple[str, int], subprocess.Popen] = {}

        self.sims: Dict[str, SimState] = {}
        for sim_id, d in self.SIMS_DEF.items():
            self.sims[sim_id] = SimState(
                sim_id=sim_id,
                label=d["label"],
                src_host=d["src"],
                dst_host=d["dst"],
                kind=d["kind"],
                slice_id=d["slice_id"],
            )

    def status(self) -> Dict:
        """
        Restituisce uno snapshot dello stato corrente del manager (serializzabile).

        Scopo:
            Fornire a UI/CLI una fotografia coerente dello stato:
            - percorso della cartella assets
            - per ogni simulazione: dati principali (id, label, host, tipo, slice, stato, errori, tempi)

        Parametri:
            Nessuno.

        Ritorno:
            Dict:
                Dizionario pronto per JSON con chiavi:
                - "assets_dir": stringa del path assets
                - "sims": dizionario {sim_id -> dettagli simulazione}

        Effetti collaterali:
            Nessuno. Non avvia processi e non modifica lo stato: legge soltanto.

        Spiegazione:
            Riga 1:
                Acquisisce il lock per leggere uno stato coerente mentre altre funzioni (start/stop/watcher)
                potrebbero modificare gli stessi oggetti SimState.

            Riga 2:
                Inizia a costruire il dizionario di risposta che verra' restituito al chiamante.

            Riga 3:
                Inserisce nel dizionario il percorso degli asset convertito a stringa
                (serve per renderlo facilmente serializzabile e stampabile).

            Riga 4:
                Costruisce la sezione "sims" come dizionario indicizzato per sim_id,
                iterando tutte le simulazioni in self.sims.

            Riga 5:
                Per ogni simulazione crea un sotto-dizionario con:
                identificativi (sim_id, label), endpoint (src, dst), tipo (kind), slice (slice_id),
                stato runtime (status), errore (error) e timestamp (started_at, ended_at).

            Riga 6:
                Restituisce lo snapshot completo al chiamante.
        """
        with self._lock:
            return {
                "assets_dir": str(self.assets_dir),
                "sims": {
                    sid: {
                        "sim_id": s.sim_id,
                        "label": s.label,
                        "src": s.src_host,
                        "dst": s.dst_host,
                        "kind": s.kind,
                        "slice_id": s.slice_id,
                        "status": s.status,
                        "error": s.error,
                        "started_at": s.started_at,
                        "ended_at": s.ended_at,
                    }
                    for sid, s in self.sims.items()
                }
            }

    def stop_all(self, reason: str = "context_change") -> None:
        """
        Ferma tutte le simulazioni attualmente in esecuzione.

        Scopo:
            Interrompere in modo centralizzato tutte le simulazioni con status "running".
            Questo metodo e' tipicamente usato quando cambia il contesto (es. cambia slicing, topologia, scenario)
            e non ha senso lasciare processi attivi.

        Parametri:
            reason (str):
                Motivazione testuale che verra' salvata nello stato della simulazione come "error"/motivo di stop.
                Default: "context_change".

        Ritorno:
            None.

        Effetti collaterali:
            - Per ogni simulazione running: termina i processi, ferma la cattura (se attiva) e aggiorna lo stato.
            - Le simulazioni vengono marcate come "interrupted" (tramite _stop_locked).

        Spiegazione riga per riga (nell'ordine del corpo della funzione):
            Riga 1:
                Acquisisce il lock per evitare che start/stop/watcher modifichino simultaneamente lo stato.

            Riga 2:
                Itera su tutti gli oggetti SimState registrati nel manager.

            Riga 3:
                Filtra solo le simulazioni che risultano attualmente in esecuzione (status == "running").

            Riga 4:
                Per ciascuna simulazione running invoca _stop_locked(s, reason=reason), che esegue:
                - terminazione dei processi associati,
                - stop della cattura tcpdump (se presente),
                - aggiornamento dello stato (interrupted) e dei timestamp.
        """
        with self._lock:
            for s in self.sims.values():
                if s.status == "running":
                    self._stop_locked(s, reason=reason)

    def start(self, sim_id: str) -> Tuple[bool, str]:
        """
        Avvia una simulazione identificata da sim_id.

        Scopo:
            - Verifica che la simulazione esista e sia avviabile.
            - Verifica che, secondo lo stato del controller (slicing), la simulazione sia consentita.
            - Inizializza lo stato runtime (timestamp, status, lista processi).
            - Avvia (se prevista) la cattura tcpdump.
            - Lancia i processi della simulazione in base al tipo (dicom_store / dicom_qr / video).
            - Avvia un watcher in thread per gestire terminazione e cleanup.

        Parametri:
            sim_id (str):
                Identificatore della simulazione da avviare (chiave in self.sims / SIMS_DEF).

        Ritorno:
            (bool, str):
                - (True, "started") se l'avvio va a buon fine
                - (False, motivo) se non e' possibile avviare o se avviene un errore

        Effetti collaterali:
            - Aggiorna lo stato di SimState (status, error, started_at, ended_at, processes).
            - Avvia processi esterni (mnexec + DCMTK/ffmpeg/nc) e potenzialmente tcpdump.
            - Avvia un thread watcher daemon.

        Nota di correttezza:
            Nel file originale questa funzione contiene un errore di sintassi:
            un 'elif' posizionato subito dopo una chiamata di funzione.
            Questa versione include la correzione minima: il primo ramo e' un 'if'.

        Spiegazione:
            Riga 1:
                Acquisisce il lock per eseguire controlli e aggiornamenti di stato in modo atomico.

            Riga 2:
                Verifica che sim_id esista; se non esiste termina subito con esito negativo.

            Riga 3:
                Recupera lo stato runtime (SimState) della simulazione richiesta.

            Riga 4:
                Impedisce il riavvio se la simulazione e' marcata "terminated" (one-shot).

            Riga 5:
                Impedisce l'avvio se la simulazione risulta gia' "running".

            Riga 6:
                Verifica permessi/slice tramite _allowed_locked; se non permessa termina con esito negativo.

            Riga 7:
                Reinizializza i campi runtime (error, timestamps, status, lista processi) per un avvio pulito.

            Riga 8:
                Entra in una sezione protetta da try/except per catturare errori durante l'avvio.

            Riga 9:
                Assicura che la pid-map di Mininet sia caricata (necessaria per mnexec).

            Riga 10:
                Avvia la cattura tcpdump se prevista per quella simulazione.

            Riga 11:
                Seleziona il tipo di simulazione (kind) e avvia i processi corretti:
                - dicom_store -> _launch_dicom_store_locked
                - dicom_qr    -> _launch_dicom_qr_locked
                - video       -> _launch_video_locked
                Se kind non e' supportato, genera un errore.

            Riga 12:
                Crea e avvia un thread watcher daemon che attende la fine e fa cleanup.

            Riga 13:
                Ritorna esito positivo.

            Riga 14:
                In caso di eccezione: marca lo stato "error", salva il messaggio, imposta ended_at,
                ferma processi eventualmente avviati, ferma la cattura e ritorna esito negativo.
        """
        with self._lock:
            if sim_id not in self.sims:
                return False, f"Unknown sim_id: {sim_id}"
            s = self.sims[sim_id]

            if s.status == "terminated":
                return False, "Simulation already terminated (one-shot)."
            if s.status == "running":
                return False, "Simulation already running."

            if not self._allowed_locked(s):
                return False, "Not allowed in current mode/slice."

            s.error = None
            s.started_at = time.time()
            s.ended_at = None
            s.status = "running"
            s.processes = []

            try:
                self._ensure_pid_map_locked()

                self._start_capture_locked(s)
                if s.kind == "dicom_store":
                    self._launch_dicom_store_locked(s)
                elif s.kind == "dicom_qr":
                    self._launch_dicom_qr_locked(s)
                elif s.kind == "video":
                    self._launch_video_locked(s)
                else:
                    raise RuntimeError(f"Unsupported kind: {s.kind}")

                t = threading.Thread(target=self._watcher, args=(s.sim_id,), daemon=True)
                t.start()
                return True, "started"
            except Exception as e:
                s.status = "error"
                s.error = str(e)
                s.ended_at = time.time()
                self._stop_processes(s.processes)
                s.processes = []
                self._stop_capture_locked(s)
                return False, s.error or "error"


    def _allowed_locked(self, s: SimState) -> bool:
        """
        Verifica se una simulazione e' consentita in base allo stato del controller (slicing).

        Scopo:
            Impedire l'avvio di simulazioni quando:
            - il controller non ha uno stato disponibile,
            - la modalita' di slicing attiva non e' quella topologica,
            - la slice richiesta dalla simulazione non e' abilitata.

        Parametri:
            s (SimState):
                Stato runtime della simulazione; qui interessa soprattutto `s.slice_id`.

        Ritorno:
            bool:
                True se la simulazione e' permessa nelle condizioni correnti del controller, False altrimenti.

        Effetti collaterali:
            Nessuno (lettura sola).

        Precondizione:
            Il metodo va chiamato con il lock gia' acquisito (da qui il suffisso _locked).

        Spiegazione:
            Riga 1:
                Legge l'attributo `state` dal controller in modo sicuro; se non esiste, ottiene None.

            Riga 2:
                Se lo stato del controller e' assente (None), nega l'avvio (False).

            Riga 3:
                Se la modalita' di slicing attiva non e' TOPOLOGY, nega l'avvio (False).

            Riga 4:
                Controlla se l'id di slice della simulazione e' presente nell'insieme/lista delle slice abilitate;
                se presente consente (True), altrimenti nega (False).
        """
        st = getattr(self.ctrl, "state", None)
        if st is None:
            return False
        if st.active_slicing_mode != st.TOPOLOGY:
            return False
        return (s.slice_id in st.enabled_topology)


    def _norm_host(self, host: str) -> str:
        """
        Normalizza un nome host fornito in input in un nome "canonico" usato dalla topologia.

        Scopo:
            Rendere piu' tollerante l'input (GUI/CLI/config) convertendo varianti e alias nel nome atteso dal sistema.
            E' una funzione di comodita': se si usano gia' i nomi canonici Mininet, questa funzione
            restituisce spesso l'input invariato.

        Parametri:
            host (str):
                Nome host da normalizzare (puo' essere vuoto, un alias, o gia' canonico).

        Ritorno:
            str:
                Nome host normalizzato (idealmente coerente con i nomi presenti in pid-map e in HOST_IP).

        Effetti collaterali:
            Nessuno.

        Spiegazione:
            Riga 1:
                Pulisce l'input: se host e' None o stringa vuota/spazi, lo riduce a stringa "pulita".

            Riga 2:
                Se dopo la pulizia il nome e' vuoto, lo restituisce subito (non ha senso normalizzare).

            Riga 3:
                Costruisce una chiave di lookup per HOST_ALIASES: prova prima la forma esatta, altrimenti la minuscola.

            Riga 4:
                Se la chiave e' presente in HOST_ALIASES, restituisce il nome canonico associato.

            Riga 5:
                Se esiste gia' una pid-map in cache e l'host e' gia' una chiave valida in quella mappa,
                restituisce l'host cosi' com'e' (gia' riconosciuto dal sistema).

            Riga 6:
                Prova a riconoscere pattern del tipo camN / radwsN / imgacqN (con N numerico) e costruisce
                un nome canonico coerente (hCamN, hRadWSN, hImgDevN).

            Riga 7:
                Gestisce alcuni casi speciali (pacs, lcs) convertendoli nella forma canonica prevista.

            Riga 8:
                Se nessuna regola si applica, restituisce l'input originale (fallback).
        """
        host = (host or "").strip()
        if not host:
            return host
        key = host if host in self.HOST_ALIASES else host.lower()
        if key in self.HOST_ALIASES:
            return self.HOST_ALIASES[key]
        if self._pid_map_cache and host in self._pid_map_cache:
            return host
        m = re.match(r"^(cam|radws|imgacq)(\d+)$", host, re.IGNORECASE)
        if m:
            kind, num = m.group(1).lower(), m.group(2)
            if kind == "cam":
                return f"hCam{num}"
            if kind == "radws":
                return f"hRadWS{num}"
            if kind == "imgacq":
                return f"hImgDev{num}"
        if host.lower() == "pacs":
            return "hPACS"
        if host.lower() == "lcs":
            return "lCS"
        return host

    def _resolve_assets_dir(self) -> Path:
        """
        Determina la cartella degli asset (DICOM / video / pcap) da usare come base.

        Scopo:
            Stabilire un percorso base affidabile per trovare file necessari alle simulazioni:
            - directory con DICOM (es. assets/dicom/...)
            - directory con video (es. assets/video/...)
            - eventualmente pcap

        Parametri:
            Nessuno.

        Ritorno:
            Path:
                Percorso assoluto della directory asset scelta.

        Effetti collaterali:
            Nessuno (sola lettura di filesystem e variabili d'ambiente).

        Criterio di scelta:
            1) Se e' definita la variabile d'ambiente ASSETS_DIR, viene usata quella.
            2) Altrimenti si cercano directory "assets" in posizioni standard rispetto al file Python.
            3) Se ne esiste una che contiene sottocartelle indicative (pcap/ o video/), viene preferita.
            4) Se nessuna ha indizi, si prende il primo candidato come fallback.

        Spiegazione:
            Riga 1:
                Legge la variabile d'ambiente ASSETS_DIR (se presente).

            Riga 2:
                Se ASSETS_DIR e' valorizzata, la converte in Path assoluto e la restituisce.

            Riga 3:
                Calcola il percorso del file corrente (questo .py) per ricavare un "project root" plausibile.

            Riga 4:
                Definisce una lista di directory candidate dove potrebbe trovarsi "assets".

            Riga 5:
                Scorre i candidati e sceglie il primo che contiene sottocartelle indicative (pcap/ o video/).

            Riga 6:
                Se nessun candidato contiene indizi, restituisce comunque il primo candidato (fallback).
        """
        env = os.environ.get("ASSETS_DIR")
        if env:
            return Path(env).expanduser().resolve()

        here = Path(__file__).resolve()
        project_root = here.parent.parent
        candidates = [
            project_root / "assets",
            project_root.parent / "assets",
        ]
        for c in candidates:
            if (c / "pcap").exists() or (c / "video").exists():
                return c
        return candidates[0]

    # -------------------- pid map / mnexec --------------------

    def _ensure_pid_map_locked(self):
        """
        Carica e mette in cache la mappa host->PID di Mininet (lettura da file JSON) se non e' gia' presente.

        Scopo:
            Per poter eseguire comandi dentro i namespace di rete degli host Mininet, il manager deve conoscere
            il PID del processo associato a ciascun host. Questa informazione viene letta da un file JSON
            (PID_MAP_PATH) e salvata in cache in self._pid_map_cache.

        Parametri:
            Nessuno.

        Ritorno:
            Nessuno. Aggiorna lo stato interno (self._pid_map_cache).

        Effetti collaterali:
            - Legge un file dal filesystem: /tmp/mn_host_pids.json
            - Aggiorna la cache self._pid_map_cache

        Errori/Eccezioni:
            - Se il file non esiste, solleva RuntimeError con messaggio chiaro.
            - Se il file contiene JSON non valido, json.loads solleva eccezione (propagata come errore di avvio).

        Precondizione:
            Deve essere chiamato con il lock gia' acquisito (da qui il suffisso _locked).

        Spiegazione riga per riga (nell'ordine del corpo della funzione):
            Riga 1:
                Se la cache esiste gia' (non e' None), esce subito: evita di rileggere il file piu' volte.

            Riga 2:
                Verifica che il file PID_MAP_PATH esista; se manca, genera un errore perche' senza PID-map
                non e' possibile usare mnexec.

            Riga 3:
                Legge il contenuto del file e lo interpreta come JSON (dizionario).

            Riga 4:
                Converte i valori in interi e salva il risultato in self._pid_map_cache.
        """
        if self._pid_map_cache is not None:
            return
        if not self.PID_MAP_PATH.exists():
            raise RuntimeError(f"Manca {self.PID_MAP_PATH}. Avvia prima la topologia Mininet.")
        data = json.loads(self.PID_MAP_PATH.read_text())
        self._pid_map_cache = {k: int(v) for k, v in data.items()}

    def _mnexec_locked(
        self,
        host: str,
        cmd: str,
        *,
        stdout_path: Optional[str] = None,
        stderr_path: Optional[str] = None,
    ) -> subprocess.Popen:
        host_n = self._norm_host(host)
        pid = self._pid_map_cache.get(host_n)  # type: ignore[union-attr]
        if pid is None:
            raise RuntimeError(f"Host {host} (normalized {host_n}) not found in pid map {self.PID_MAP_PATH}")

        full = ["mnexec", "-a", str(pid), "bash", "-lc", cmd]

        out = open(stdout_path, "ab") if stdout_path else subprocess.DEVNULL
        err = open(stderr_path, "ab") if stderr_path else subprocess.DEVNULL
        return subprocess.Popen(full, stdout=out, stderr=err)

    def _mnexec_run_locked(self, host: str, cmd: str) -> subprocess.CompletedProcess:
        """
        Avvia un comando dentro il namespace di rete di un host Mininet tramite mnexec.

        Scopo:
            Eseguire `cmd` come se fosse lanciato "dentro" l'host Mininet indicato, usando:
                mnexec -a <PID> bash -lc "<cmd>"
            Questo metodo NON attende la fine del comando: ritorna un Popen (processo in esecuzione).

        Parametri:
            host (str):
                Nome host (puo' essere alias o canonico). Viene normalizzato con _norm_host().
            cmd (str):
                Comando da eseguire (stringa). Viene passato a "bash -lc" per supportare pipe, &&, variabili, ecc.
            stdout_path (Optional[str]):
                Se valorizzato, stdout del processo viene scritto su quel file (append in binario).
                Se None, stdout viene scartato.
            stderr_path (Optional[str]):
                Se valorizzato, stderr del processo viene scritto su quel file (append in binario).
                Se None, stderr viene scartato.

        Ritorno:
            subprocess.Popen:
                Handle del processo avviato (non terminato). Il chiamante lo salva in s.processes e lo gestisce nel watcher/stop.

        Precondizioni:
            - Deve essere chiamato con lock gia' acquisito (da qui _locked).
            - self._pid_map_cache deve essere gia' caricata (_ensure_pid_map_locked), altrimenti non trova i PID.

        Effetti collaterali:
            - Avvia un processo esterno sul sistema.
            - Apre file descriptor per stdout/stderr se sono stati richiesti percorsi.

        Eccezioni:
            - Solleva RuntimeError se l'host non e' presente nella pid-map (non e' possibile eseguire mnexec).
            - Può propagare eccezioni di sistema se Popen fallisce (eseguibile mancante, permessi, ecc.).

        Spiegazione:
            Riga 1:
                Normalizza il nome host in forma canonica, per poterlo cercare nella pid-map.

            Riga 2:
                Legge dalla cache la PID corrispondente all'host canonico.

            Riga 3:
                Se la PID non esiste, interrompe con errore: senza PID non si puo' entrare nel namespace.

            Riga 4:
                Costruisce il comando completo mnexec + bash -lc che eseguira' `cmd` nel namespace corretto.

            Riga 5:
                Decide dove mandare stdout: se e' stato dato un path apre il file in append binario,
                altrimenti usa DEVNULL (scarta output).

            Riga 6:
                Decide dove mandare stderr: stesso criterio di stdout.

            Riga 7:
                Avvia il processo e ritorna immediatamente il Popen (senza wait).
        """
        host_n = self._norm_host(host)
        pid = self._pid_map_cache.get(host_n)  # type: ignore[union-attr]
        if pid is None:
            raise RuntimeError(f"Host {host} (normalized {host_n}) not found in pid map {self.PID_MAP_PATH}")
        full = ["mnexec", "-a", str(pid), "bash", "-lc", cmd]
        return subprocess.run(full, capture_output=True, text=True)
    
    def _start_capture_locked(self, s: SimState):
        """
        Avvia la cattura pacchetti (tcpdump) per una simulazione, se e' prevista un'interfaccia di cattura.

        Scopo:
            Se per la simulazione `s.sim_id` esiste una voce in CAPTURE_IFACE_BY_SIM,
            avvia tcpdump sull'interfaccia indicata e salva il file .pcap in /tmp.
            I riferimenti al processo e al file vengono salvati dentro lo stato SimState.

        Parametri:
            s (SimState):
                Stato della simulazione. Vengono letti:
                - s.sim_id (per cercare l'interfaccia)
                e vengono scritti:
                - s.capture_proc (processo tcpdump)
                - s.capture_file (percorso file pcap)

        Ritorno:
            Nessuno.

        Effetti collaterali:
            - Avvia un processo esterno tcpdump (se configurato).
            - Scrive un file pcap in /tmp.
            - Aggiorna campi di SimState relativi alla cattura.

        Note:
            - Se non e' configurata alcuna interfaccia per questa simulazione, la funzione non fa nulla.
            - In caso di errore nell'avvio di tcpdump, i campi capture_* vengono riportati a None.

        Spiegazione:
            Riga 1:
                Cerca l'interfaccia di cattura associata al sim_id; se non esiste, termina subito.

            Riga 2:
                Genera un timestamp intero per creare un nome file univoco.

            Riga 3:
                Costruisce il percorso del file pcap in /tmp includendo sim_id e timestamp.

            Riga 4:
                Salva il percorso del file pcap in s.capture_file (stato della simulazione).

            Riga 5:
                Entra in try/except per gestire eventuali errori di avvio tcpdump senza far fallire tutto.

            Riga 6:
                Costruisce la lista argomenti di tcpdump:
                -i <iface> (interfaccia)
                -U (scrittura "packet-buffered", utile per vedere dati subito)
                -n (no DNS)
                -s 0 (snaplen massimo)
                -w <file> (scrittura su pcap)

            Riga 7:
                Avvia tcpdump come processo in background e salva l'handle in s.capture_proc.

            Riga 8:
                In caso di eccezione, pulisce s.capture_proc e s.capture_file riportandoli a None.
        """
        iface = self.CAPTURE_IFACE_BY_SIM.get(s.sim_id)
        if not iface:
            return
        ts = int(time.time())
        cap_file = f"/tmp/sim_{s.sim_id}_{ts}.pcap"
        s.capture_file = cap_file
        try:
            # -U: packet-buffered, better for live; -n: no DNS; -s 0 full capture
            cmd = ["tcpdump", "-i", iface, "-U", "-n", "-s", "0", "-w", cap_file]
            s.capture_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            s.capture_proc = None
            s.capture_file = None

    def _stop_capture_locked(self, s: SimState):
        """
        Ferma la cattura tcpdump associata a una simulazione, se presente.

        Scopo:
            Terminare in modo robusto il processo tcpdump salvato in s.capture_proc:
            - prima prova terminate() e attende un breve timeout
            - se non si chiude, prova kill()

        Parametri:
            s (SimState):
                Stato della simulazione da cui leggere e azzerare s.capture_proc.

        Ritorno:
            Nessuno.

        Effetti collaterali:
            - Invia segnali di terminazione al processo tcpdump (se esiste).

        Note:
            La funzione azzera s.capture_proc subito (prima di terminare realmente) per evitare
            che altre parti del codice vedano ancora la cattura come "attiva".

        Spiegazione:
            Riga 1:
                Copia il riferimento al processo tcpdump in una variabile locale e azzera subito s.capture_proc.

            Riga 2:
                Se non esiste alcun processo (None), termina subito.

            Riga 3:
                Prova a inviare terminate() e attende fino a 2 secondi.

            Riga 4:
                Se terminate fallisce o scade il timeout, prova kill() come ultima risorsa.

            Riga 5:
                Se anche kill fallisce, ignora l'errore (l'obiettivo e' non far crashare ).
        """
        p = s.capture_proc
        s.capture_proc = None
        if not p:
            return
        try:
            p.terminate()
            p.wait(timeout=2)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
    def _ensure_dcmtk_available(self):
        """
        Verifica che i comandi DCMTK necessari siano disponibili nel sistema.

        Scopo:
            Le simulazioni DICOM si basano su tool esterni (DCMTK). Se mancano, e' inutile avviare la simulazione:
            meglio fermarsi subito con un errore esplicito.

        Parametri:
            Nessuno.

        Ritorno:
            Nessuno.

        Effetti collaterali:
            Nessuno (solo controlli).

        Eccezioni:
            Solleva RuntimeError se almeno uno degli eseguibili richiesti non e' presente nel PATH.

        Spiegazione:
            Riga 1:
                Scorre l'elenco dei binari DCMTK richiesti dal progetto (echo/store/find/move).

            Riga 2:
                Per ogni binario verifica se e' reperibile nel PATH (tramite shutil.which).

            Riga 3:
                Se un binario non e' trovato, interrompe con RuntimeError e messaggio chiaro su cosa installare.
        """
        for bin_name in ("echoscu", "storescp", "storescu", "findscu", "movescu"):
            if shutil.which(bin_name) is None:
                raise RuntimeError(
                    f"DCMTK tool '{bin_name}' not found in PATH. Install dcmtk (e.g., 'apt install dcmtk')."
                )

    def _ensure_pacs_scp_running_locked(
        self,
        pacs_host: str,
        *,
        pacs_aet: str,
        pacs_port: int,
        db_root: str = "/tmp/dcmtk_db",
    ) -> Optional[subprocess.Popen]:
        """
        Assicura che sul nodo PACS sia attivo un servizio SCP in ascolto sulla porta indicata.

        Scopo:
            Alcune simulazioni (es. C-STORE e C-FIND) richiedono che sul PACS esista un server DICOM (SCP).
            Questo metodo:
            - controlla se sulla porta `pacs_port` c'e' gia' un listener;
            - se non c'e', prepara directory DB + file di configurazione e avvia dcmqrscp;
            - memorizza il processo avviato per non duplicarlo.

        Parametri:
            pacs_host (str):
                Host (alias o canonico) su cui deve girare lo SCP.
            pacs_aet (str):
                "Nome DICOM" del PACS (AET = Application Entity Title): e' l'identificatore con cui i client
                indirizzano il server DICOM (SCP) quando fanno C-STORE/C-FIND.
            pacs_port (int):
                Porta TCP su cui deve ascoltare lo SCP.
            db_root (str):
                Directory base per il database DCMTK creato per l'SCP (default: /tmp/dcmtk_db).

        Ritorno:
            Optional[subprocess.Popen]:
                - None se la porta era gia' in ascolto (cioe' esiste gia' un servizio e non avviamo nulla).
                - Popen se il metodo ha avviato un nuovo processo dcmqrscp.

        Precondizioni:
            Deve essere chiamato con lock gia' acquisito (da qui il nome _locked).

        Effetti collaterali:
            - Esegue comandi sul nodo PACS (mkdir/cp/sed/chown/chmod).
            - Avvia un processo dcmqrscp se necessario.
            - Scrive file temporanei in /tmp e directory DB sotto db_root.

        Spiegazione:
            Riga 1:
                Costruisce una chiave (host normalizzato, porta) per identificare univocamente questo SCP.

            Riga 2:
                Se esiste gia' un processo SCP in cache per quella chiave, lo restituisce e non fa altri controlli.

            Riga 3:
                Esegue sul PACS un controllo di rete per vedere se la porta e' in LISTEN.

            Riga 4:
                Se la porta e' gia' in ascolto, ritorna None (significa: "gia' presente, non avvio niente").

            Riga 5:
                Prepara i percorsi (file cfg temporaneo e directory DB) che verranno usati da dcmqrscp.

            Riga 6:
                Costruisce un comando shell unico che:
                - crea la directory DB,
                - copia una cfg di base,
                - inserisce una riga AETable se manca,
                - aggiusta permessi,
                - avvia dcmqrscp sulla porta richiesta.

            Riga 7:
                Avvia dcmqrscp nel namespace del PACS (processo in background) con stdout/stderr su file.

            Riga 8:
                Salva il processo nella cache per evitare duplicati e lo restituisce.
        """
        key = (self._norm_host(pacs_host), int(pacs_port))
        if key in self._auto_pacs_scps:
            return self._auto_pacs_scps[key]

        # Eseguo un comando "dentro" il nodo PACS per verificare se la porta pacs_port e' gia' in ascolto.
        # Uso _mnexec_run_locked perche' voglio il returncode (success/fail) e non un processo in background.
        check = self._mnexec_run_locked(
            pacs_host,  # host su cui eseguire il controllo (namespace del PACS)
            # Comando:
            # - ss -ltnH: elenca socket TCP in LISTEN, in formato parsabile (-H senza header)
            # - sport = :PORT: filtra solo la porta richiesta
            # - grep -q LISTEN: ritorna 0 se trova una riga (porta in ascolto), altrimenti 1
            f"ss -ltnH 'sport = :{int(pacs_port)}' | grep -q LISTEN",
        )

        # Se returncode == 0 significa che la porta e' gia' in ascolto (quindi esiste gia' un SCP attivo).
        # In quel caso NON avvio un nuovo dcmqrscp: esco tornando None.
        if check.returncode == 0:
            return None

        # Preparo il path del file di configurazione che usero' per dcmqrscp.
        # Lo metto in /tmp con nome che include AET e porta per evitare collisioni tra istanze diverse.
        cfg_path = f"/tmp/dcmqrscp_{pacs_aet}_{int(pacs_port)}.cfg"

        # Preparo la directory "database" associata a questo AET sotto db_root.
        # dcmqrscp usa questa directory per memorizzare/studiare i dati (dipende dalla config).
        db_dir = f"{db_root}/{pacs_aet}"

        # Costruisco un'unica stringa di comando shell da eseguire sul PACS.
        # La racchiudo in "sh -lc '...'" per:
        # - avere sintassi shell (&&, quote, grep, sed)
        # - fallire subito se un pezzo in catena fallisce (grazie agli &&)
        cmd = (
            "sh -lc '"

            # Creo la directory del database (se non esiste gia').
            f"mkdir -p \"{db_dir}\" && "

            # Copio una configurazione base di dcmqrscp (di sistema) nel file temporaneo che usero' io.
            # Così posso modificarla senza toccare l'originale in /etc.
            f"cp /etc/dcmtk/dcmqrscp.cfg \"{cfg_path}\" && "

            # Controllo se nel file cfg esiste gia' una riga AETable che inizia con pacs_aet.
            # grep -qE "^AET[spazi]" -> esito 0 se la trova, 1 se non la trova.
            f"grep -qE \"^{pacs_aet}[[:space:]]\" \"{cfg_path}\" || "

            # Se la riga non esiste, la inserisco PRIMA della riga "AETable END".
            # La riga inserita definisce:
            # - nome AET (pacs_aet)
            # - directory DB (db_dir)
            # - permessi RW
            # - vincoli (10, 1024mb) e ANY (accetta da qualsiasi host) secondo sintassi DCMTK.
            f"sed -i \"/^AETable END/i {pacs_aet}   {db_dir}   RW (10, 1024mb) ANY\" \"{cfg_path}\" && "

            # Provo a rendere la directory db_root di proprieta' dell'utente/gruppo dcmtk (se esiste).
            # Se chown fallisce (utente non esiste o permessi), ignoro l'errore con "|| true".
            f"chown -R dcmtk:dcmtk \"{db_root}\" 2>/dev/null || true && "

            # Imposto permessi di lettura/scrittura/esecuzione per owner e group (775) sulla directory db_root.
            # Se chmod fallisce, ignoro l'errore con "|| true".
            f"chmod -R 775 \"{db_root}\" 2>/dev/null || true && "

            # Avvio il server dcmqrscp:
            # -v : verbose
            # -c : uso il file di configurazione che ho appena creato/modificato
            # poi passo la porta su cui deve mettersi in ascolto.
            # Uso exec per sostituire la shell col processo dcmqrscp (PID piu' pulito).
            f"exec dcmqrscp -v -c \"{cfg_path}\" {int(pacs_port)}"

            # Chiudo la stringa tra apici singoli avviata sopra.
            "'"
        )
        p = self._mnexec_locked(
            pacs_host,
            cmd,
            stdout_path=f"/tmp/pacs_storescp_{key[0]}_{key[1]}.out",
            stderr_path=f"/tmp/pacs_storescp_{key[0]}_{key[1]}.err",
        )
        self._auto_pacs_scps[key] = p
        return p

    def _launch_dicom_store_locked(self, s: SimState):
        """
        Avvia una simulazione DICOM di tipo C-STORE: prima C-ECHO, poi invio dei file DICOM (storescu).

        Scopo:
            Generare traffico realistico verso il PACS:
            1) verifica tool DCMTK presenti
            2) determina host/IP/parametri (AET, porta)
            3) assicura che esista un PACS SCP in ascolto
            4) trova la directory con i .dcm
            5) avvia la catena "echoscu && storescu" nel namespace dell'host sorgente

        Parametri:
            s (SimState):
                Stato della simulazione; viene letto s.sim_id / s.src_host / s.dst_host e viene aggiornata
                la lista s.processes con i processi avviati.

        Ritorno:
            Nessuno. In caso di problemi solleva eccezione (gestita dal chiamante start()).

        Effetti collaterali:
            - Può avviare un processo PACS (dcmqrscp) se la porta non è già in ascolto.
            - Avvia un processo client (echoscu+storescu) sul nodo sorgente.
            - Scrive log su file /tmp/traffic_sim_<sim_id>.out/.err

        Spiegazione:
            Riga 1:
                Verifica che i binari DCMTK richiesti siano disponibili (altrimenti errore immediato).

            Riga 2:
                Recupera la configurazione della simulazione da SIMS_DEF usando s.sim_id.

            Riga 3:
                Normalizza il nome dell'host destinazione (PACS).

            Riga 4:
                Normalizza il nome dell'host sorgente.

            Riga 5:
                Determina l'IP del PACS a partire dal nome host; se non lo conosce, interrompe con errore.

            Riga 6:
                Legge i parametri DICOM dalla configurazione (AET PACS, porta PACS, AET sorgente) con default sensati.

            Riga 7:
                Assicura che sul PACS ci sia uno SCP in ascolto sulla porta richiesta; se il metodo avvia un processo,
                lo registra in s.processes per poterlo poi terminare/gestire.

            Riga 8:
                Determina da quale directory leggere i file DICOM:
                - preferisce dicom_rel, ma accetta anche pcap_rel come fallback.

            Riga 9:
                Se non è configurato nessun percorso, interrompe: non sa dove trovare i .dcm.

            Riga 10:
                Costruisce il path assoluto della directory DICOM sotto assets_dir e verifica che esista.

            Riga 11:
                Costruisce il comando di "echo" (C-ECHO) verso il PACS.

            Riga 12:
                Costruisce il comando di "store" (C-STORE) che invia tutti i file DICOM presenti nella directory.

            Riga 13:
                Unisce i due comandi in una singola catena (echo poi store) usando &&.

            Riga 14:
                Avvia la catena di comandi nel namespace dell'host sorgente e redirige stdout/stderr su file.

            Riga 15:
                Salva l'handle del processo client in s.processes.
        """
        self._ensure_dcmtk_available()
        d = self.SIMS_DEF[s.sim_id]

        pacs = self._norm_host(s.dst_host)
        src = self._norm_host(s.src_host)

        pacs_ip = self._ip_of_host(pacs)
        if not pacs_ip:
            raise RuntimeError(f"Unknown dst IP for {pacs}")

        pacs_aet = d.get("pacs_aet", "DCMQRSCP")
        pacs_port = int(d.get("pacs_port", 4243))
        src_aet = d.get("src_aet", "MODALITY_AE")

        p_pacs = self._ensure_pacs_scp_running_locked(
            pacs,
            pacs_aet=pacs_aet,
            pacs_port=pacs_port,
        )
        if p_pacs is not None:
            s.processes.append(p_pacs)

        dicom_rel = d.get("dicom_rel")
        pcap_rel = d.get("pcap_rel")
        if dicom_rel is None and pcap_rel is None:
            raise RuntimeError("No dicom_rel/pcap_rel configured for this sim; cannot locate .dcm files")

        dicom_dir = (self.assets_dir / (dicom_rel or pcap_rel)).resolve()
        if not dicom_dir.exists():
            raise RuntimeError(
                f"DICOM dir not found: {dicom_dir} (put .dcm files there). "
                f"Tip: you can use assets/pcap/... as well; the code will accept .dcm there."
            )

        echo_cmd = f'echoscu -v -aec "{pacs_aet}" -aet "{src_aet}" "{pacs_ip}" {pacs_port}'

        store_cmd = f'storescu -v -aec "{pacs_aet}" -aet "{src_aet}" "{pacs_ip}" {pacs_port} "{dicom_dir}"'
        chain = echo_cmd + " && " + store_cmd

        p = self._mnexec_locked(
            src, chain,
            stdout_path=f"/tmp/traffic_sim_{s.sim_id}.out",
            stderr_path=f"/tmp/traffic_sim_{s.sim_id}.err",
        )
        s.processes.append(p)

    def _launch_dicom_qr_locked(self, s: SimState):
        """
        Avvia una simulazione DICOM di tipo Query/Retrieve “soft”: C-ECHO + serie di C-FIND.

        Scopo generale:
            - Preparare e lanciare traffico findscu verso il PACS in modo credibile.
            - Eseguire una prima query sincrona per capire compatibilità (Study Root vs Patient Root).
            - Lanciare ulteriori query STUDY in background.
            - Se possibile, ricavare uno StudyInstanceUID e lanciare anche una query SERIES.
            - NON lanciare C-MOVE in questa versione.

        Parametri:
            s (SimState):
                Stato della simulazione.

        Ritorno:
            Nessuno (eccezioni propagate al chiamante).

        Spiegazione riga-per-riga (nell’ordine del corpo della funzione):

        --- Parte iniziale: preparazione contesto DICOM ---

        Riga 1:
            Verifica che tutti i binari DCMTK necessari siano disponibili.

        Riga 2:
            Legge dalla configurazione staticamente definita (SIMS_DEF) i parametri per questa simulazione.

        Riga 3:
            Normalizza i nomi host di PACS (destinazione) e workstation (sorgente).

        Riga 4:
            Recupera l’IP del PACS; se sconosciuto, interrompe.

        Riga 5:
            Estrae da configurazione AET del PACS, porta e AET della workstation.

        Riga 6:
            Assicura che il PACS abbia un SCP attivo sulla porta richiesta; se avviato ora, lo registra in s.processes.

        Riga 7:
            Costruisce e avvia un C-ECHO per verificare la raggiungibilità del PACS; registra il processo.

        --- Preparazione della suite di query C-FIND (STUDY) ---

        Riga 8:
            Definisce i “return keys” standard da chiedere nelle query STUDY (UID, data, descrizione, paziente ecc.).

        Riga 9 (funzione interna _find_cmd):
            Costruisce un comando findscu completo in base a:
                - il root DICOM (Study Root -S oppure Patient Root -P)
                - il livello (STUDY o SERIES)
                - eventuali match keys (es. PatientName="*")
                - le return keys

            Dettaglio ruolo _find_cmd:
                - compone gli argomenti -k queryretrievelevel=...
                - aggiunge tutte le return tags
                - aggiunge match keys
                - costruisce la riga completa findscu -v -S/-P -aec ... -aet ... <IP> <PORT>

        Riga 10 (funzione interna _build_suite):
            Costruisce un elenco di query STUDY tipiche, ognuna con un nome simbolico:
                - study_all
                - study_by_name
                - study_by_id
                - study_by_date
                - study_by_mod

            Ogni elemento è (nome, comando findscu generato con _find_cmd).

        Riga 11:
            Imposta Study Root come default (-S) e genera l’intera suite di query STUDY.

        --- Prima query: compatibilità e estrazione UID ---

        Riga 12:
            Esegue la prima query della suite in modalità sincrona (run_locked),
            ottenendo stdout/stderr per diagnosi.

        Riga 13:
            Se il PACS rifiuta i presentation contexts per Study Root,
            cambia root_flag in Patient Root (-P), ricostruisce la suite e ripete la prima query.

        Riga 14:
            Salva lo stdout/stderr della prima query su file di debug.

        Riga 15:
            Prova a estrarre uno StudyInstanceUID dal risultato della prima query usando una regex.

        --- Lancio delle altre query STUDY in background ---

        Riga 16:
            Per ogni query rimanente nella suite (esclusa la prima),
            avvia il comando findscu nel namespace della workstation e registra i processi in s.processes.

        --- Query SERIES opzionale ---

        Riga 17:
            Se è stato trovato uno StudyInstanceUID:
                - costruisce una query SERIES filtrata su quell’UID tramite _find_cmd
                - lancia il comando SERIES in background
                - registra il processo

        --- Considerazioni finali ---
        Riga 18:
            NON tenta C-MOVE, per scelta esplicita del codice (meno dipendenze di configurazione).
        """
        self._ensure_dcmtk_available()
        d = self.SIMS_DEF[s.sim_id]

        pacs = self._norm_host(s.dst_host)
        ws = self._norm_host(s.src_host)

        pacs_ip = self._ip_of_host(pacs)
        if not pacs_ip:
            raise RuntimeError(f"Unknown dst IP for {pacs}")

        pacs_aet = d.get("pacs_aet", "DCMQRSCP")
        pacs_port = int(d.get("pacs_port", 4243))
        ws_aet = d.get("ws_aet", "RADWS_AE")

        p_pacs = self._ensure_pacs_scp_running_locked(
            pacs,
            pacs_aet=pacs_aet,
            pacs_port=pacs_port,
        )
        if p_pacs is not None:
            s.processes.append(p_pacs)

        echo_cmd = f'echoscu -v -aec "{pacs_aet}" -aet "{ws_aet}" "{pacs_ip}" {pacs_port}'
        p_echo = self._mnexec_locked(
            ws, echo_cmd,
            stdout_path=f"/tmp/traffic_sim_{s.sim_id}_echo.out",
            stderr_path=f"/tmp/traffic_sim_{s.sim_id}_echo.err",
        )
        s.processes.append(p_echo)

        base_return = [
            'StudyInstanceUID', 'StudyDate', 'StudyDescription',
            'PatientName', 'PatientID', 'ModalitiesInStudy'
        ]

        def _find_cmd(root_flag: str, level: str, match: dict, return_tags: list) -> str:
            ks = [f'-k QueryRetrieveLevel={level}']

            for tag in return_tags:
                ks.append(f'-k {tag}')
            # match keys
            for k, v in match.items():
                ks.append(f'-k {k}="{v}"')
            # root_flag is either "-S" (Study Root) or "-P" (Patient Root)
            return f'findscu -v {root_flag} -aec "{pacs_aet}" -aet "{ws_aet}" {" ".join(ks)} "{pacs_ip}" {pacs_port}'

        def _build_suite(root_flag: str):
            return [
                ("study_all",       _find_cmd(root_flag, "STUDY", {"PatientName": "*"}, base_return)),
                ("study_by_name",   _find_cmd(root_flag, "STUDY", {"PatientName": "ROSSI^*"}, base_return)),
                ("study_by_id",     _find_cmd(root_flag, "STUDY", {"PatientID": "12345"}, base_return)),
                ("study_by_date",   _find_cmd(root_flag, "STUDY", {"StudyDate": ""}, base_return)),
                ("study_by_mod",    _find_cmd(root_flag, "STUDY", {"ModalitiesInStudy": "CT"}, base_return)),
            ]

        root_flag_used = "-S"
        query_suite = _build_suite(root_flag_used)

        # Prendo il primo elemento della suite: e' la query "principale" che uso come test iniziale.
        first_name, first_cmd = query_suite[0]

        # Eseguo la prima query in modo sincrono (aspetto che finisca) per poter leggere stderr/stdout e decidere come procedere.
        cp = self._mnexec_run_locked(ws, first_cmd)

        # Se stderr contiene questo messaggio, significa che il PACS non accetta il "root" usato (-S Study Root).
        # In quel caso passo a Patient Root (-P) e rigenero tutta la suite coerente con quel root.
        if "No accepted presentation contexts" in (cp.stderr or ""):
            # Cambio root: da Study Root a Patient Root.
            root_flag_used = "-P"
            # Ricostruisco la suite di query con il nuovo root.
            query_suite = _build_suite(root_flag_used)
            # Riprendo la prima query della nuova suite (puo' cambiare perche' cambia il root_flag).
            first_name, first_cmd = query_suite[0]
            # Rieseguo la prima query in modo sincrono, ora col root corretto per quel PACS.
            cp = self._mnexec_run_locked(ws, first_cmd)

        Path(f"/tmp/traffic_sim_{s.sim_id}_{first_name}.out").write_text(cp.stdout or "")

        Path(f"/tmp/traffic_sim_{s.sim_id}_{first_name}.err").write_text(cp.stderr or "")

        # Inizializzo lo StudyInstanceUID a None: se non riesco a estrarlo, salto la query SERIES.
        study_uid = None

        # Cerco nello stdout della query una riga che contiene il tag (0020,000D) StudyInstanceUID in formato DCMTK.
        # La regex cattura il valore dentro le parentesi quadre: [... ].
        m_uid = re.search(r"\(0020,000D\)\s*UI\s*\[([^\]]+)\]", cp.stdout or "")

        # Se la regex ha trovato un UID, lo salvo (ripulito) in study_uid.
        if m_uid:
            study_uid = m_uid.group(1).strip()

        # Ora lancio le altre query della suite (dalla seconda in poi) in background:
        for name, cmd in query_suite[1:]:
            # Avvio il comando nel namespace della workstation (ws) senza attendere la fine.
            p = self._mnexec_locked(
                ws, cmd,
                # Salvo stdout su file dedicato, uno per query, per poter analizzare dopo.
                stdout_path=f"/tmp/traffic_sim_{s.sim_id}_{name}.out",
                # Salvo stderr su file dedicato, uno per query.
                stderr_path=f"/tmp/traffic_sim_{s.sim_id}_{name}.err",
            )
            # Registro il processo avviato per poterlo fermare in stop/watcher.
            s.processes.append(p)

        # Se sono riuscito a ricavare uno StudyInstanceUID, posso fare una query piu' specifica a livello SERIES
        # (cioe' "dimmi le serie dentro questo studio").
        if study_uid:
            # Costruisco il comando findscu per livello SERIES, filtrando per StudyInstanceUID.
            series_cmd = _find_cmd(
                root_flag_used,
                "SERIES",
                {"StudyInstanceUID": study_uid},
                ["SeriesInstanceUID", "SeriesDescription", "Modality", "NumberOfSeriesRelatedInstances"],
            )

            # Avvio la query SERIES in background nel namespace della workstation.
            p_series = self._mnexec_locked(
                ws, series_cmd,
                # Salvo stdout/stderr della query SERIES su file dedicati.
                stdout_path=f"/tmp/traffic_sim_{s.sim_id}_series.out",
                stderr_path=f"/tmp/traffic_sim_{s.sim_id}_series.err",
            )
            # Registro anche questo processo.
            s.processes.append(p_series)


    def _launch_video_locked(self, s: SimState):
        """
        Simula uno stream video su UDP: avvia un receiver sul nodo di destinazione e un sender sul nodo sorgente.

        Scopo:
            Generare traffico "tipo video" tra due host della topologia senza implementare un vero protocollo RTSP/RTP.
            L’approccio e’:
            - sul destinatario: un listener UDP che riceve e scarta i pacchetti
            - sul sorgente: ffmpeg che legge un file video e lo invia in UDP (formato mpegts)

        Parametri:
            s (SimState):
                Stato della simulazione; usa s.sim_id, s.src_host, s.dst_host e aggiorna s.processes.

        Ritorno:
            Nessuno. Se qualcosa non e’ coerente (file mancante, IP mancante) solleva RuntimeError.

        Effetti collaterali:
            - Avvia due processi nel namespace Mininet:
              1) receiver (nc) su dst
              2) sender (ffmpeg) su src
            - I processi vengono registrati in s.processes per stop/watcher.
            - Scrive log su /tmp/traffic_sim_<sim_id>.out/.err (shared tra receiver e sender).

        Spiegazione:
            Riga 1:
                Recupera la configurazione della simulazione (video_file, udp_port) da SIMS_DEF usando s.sim_id.

            Riga 2:
                Calcola il percorso assoluto del file video partendo da assets_dir e dal path relativo in config.

            Riga 3:
                Verifica che il file video esista; se manca interrompe con errore, perche’ ffmpeg non potrebbe partire.

            Riga 4:
                Legge la porta UDP dalla configurazione e la forza a int.

            Riga 5:
                Ricava l’IP del nodo destinazione (dst) tramite _ip_of_host; e’ necessario per costruire l’URL udp://IP:PORT.

            Riga 6:
                Se l’IP del destinatario non e’ noto, interrompe con errore (non si puo’ costruire la destinazione UDP).

            Riga 7:
                Imposta la durata dello streaming usando la costante VIDEO_DURATION_SEC.

            Riga 8:
                Costruisce il comando receiver:
                - timeout (durata + margine) per evitare che resti appeso per sempre
                - nc in UDP in ascolto sulla porta (modalita’ listen)
                - output scartato su /dev/null

            Riga 9:
                Avvia il receiver nel namespace del nodo destinazione e registra il processo.

            Riga 10:
                Costruisce il comando sender ffmpeg:
                - legge il file video in tempo reale (-re)
                - limita la durata (-t)
                - impacchetta in mpegts e invia in UDP verso dst_ip:port

            Riga 11:
                Avvia il sender nel namespace del nodo sorgente e registra il processo.

            Riga 12:
                Aggiunge entrambi i processi nella lista s.processes, cosi’ stop/watcher li possono terminare.
        """
        d = self.SIMS_DEF[s.sim_id]
        video = (self.assets_dir / d["video_file"]).resolve()
        if not video.exists():
            raise RuntimeError(f"Video file not found: {video}")

        port = int(d["udp_port"])
        dst_ip = self._ip_of_host(s.dst_host)
        if not dst_ip:
            raise RuntimeError(f"Unknown dst IP for {s.dst_host}")

        dur = self.VIDEO_DURATION_SEC
        recv_cmd = f'timeout {dur + 5} nc -u -lk {port} > /dev/null 2>&1'
        p_recv = self._mnexec_locked(
            s.dst_host, recv_cmd,
            stdout_path=f"/tmp/traffic_sim_{s.sim_id}.out",
            stderr_path=f"/tmp/traffic_sim_{s.sim_id}.err",
        )

        send_cmd = f'ffmpeg -hide_banner -loglevel warning -nostdin -re -i "{video}" -t {dur} -f mpegts udp://{dst_ip}:{port}'
        p_send = self._mnexec_locked(
            s.src_host, send_cmd,
            stdout_path=f"/tmp/traffic_sim_{s.sim_id}.out",
            stderr_path=f"/tmp/traffic_sim_{s.sim_id}.err",
        )

        s.processes.extend([p_recv, p_send])

    def _ip_of_host(self, host: str) -> Optional[str]:
        """
        Restituisce l'indirizzo IP associato a un host della topologia (se disponibile).

        Scopo:
            Tradurre un nome host (anche alias) nel suo IP, leggendo una mappa statica HOST_IP
            definita nel modulo `topology_defs`.
            Serve per costruire destinazioni di rete (es. udp://IP:port, echoscu/findscu verso IP).

        Parametri:
            host (str):
                Nome host da risolvere. Puo' essere canonico o alias: viene normalizzato.

        Ritorno:
            Optional[str]:
                - stringa IP se trovato
                - None se il modulo/mappa non esiste o se l'host non e' presente

        Effetti collaterali:
            Nessuno (solo import e lookup).

        Spiegazione:
            Riga 1:
                Prova a importare HOST_IP dal modulo topology_defs. Se l'import fallisce, verra' gestito dall'except.

            Riga 2:
                Normalizza il nome host (gestione alias) per aumentare la probabilita' di trovare la chiave nella mappa.

            Riga 3:
                Cerca prima l'host normalizzato, poi come fallback l'host originale; restituisce il primo IP non vuoto.

            Riga 4:
                Se qualcosa va storto (import mancante, eccezioni), restituisce None per indicare "IP non disponibile".
        """
        try:
            from topology_defs import HOST_IP
            host_n = self._norm_host(host)
            return HOST_IP.get(host_n) or HOST_IP.get(host)
        except Exception:
            return None



    def _stop_locked(self, s: SimState, reason: str = "stopped"):
        """
        Ferma una singola simulazione aggiornando stato e terminando tutte le risorse associate.

        Scopo:
            Eseguire uno stop "pulito" e consistente:
            - terminare processi client (s.processes)
            - terminare eventuali processi server (s.server_processes) se presenti
            - fermare la cattura tcpdump (se attiva)
            - aggiornare lo stato della simulazione a "interrupted" e salvare il motivo

        Parametri:
            s (SimState):
                Stato runtime della simulazione da fermare (verra' modificato).
            reason (str):
                Motivo dello stop; viene salvato in s.error per tracciabilita'.

        Ritorno:
            Nessuno.

        Effetti collaterali:
            - Invia terminate/kill a processi esterni.
            - Ferma tcpdump se era attivo.
            - Modifica campi di SimState (status/error/ended_at/processes).

        Precondizione:
            Deve essere chiamato con lock gia' acquisito (da qui _locked).

        Spiegazione:
            Riga 1:
                Termina in modo robusto tutti i processi client registrati in s.processes.

            Riga 2:
                Termina in modo robusto eventuali processi server (se l'attributo non esiste usa lista vuota).

            Riga 3:
                Azzera la lista processi client nello stato (da questo punto la simulazione non ha piu' processi attivi tracciati).

            Riga 4:
                Azzera la lista server_processes nello stato (mantiene coerenza anche se non era presente).

            Riga 5:
                Ferma tcpdump se era stato avviato per questa simulazione.

            Riga 6:
                Imposta lo stato logico della simulazione come interrotto.

            Riga 7:
                Salva il motivo di stop in s.error.

            Riga 8:
                Registra il timestamp di fine simulazione.
        """
        self._stop_processes(s.processes)
        self._stop_processes(getattr(s, "server_processes", []))
        s.processes = []
        s.server_processes = []
        self._stop_capture_locked(s)
        s.status = "interrupted"
        s.error = reason
        s.ended_at = time.time()

    @staticmethod
    def _stop_processes(ps: List[subprocess.Popen]):
        """
        Termina in modo robusto una lista di processi Popen.

        Scopo:
            Fornire un metodo unico e sicuro per chiudere processi avviati dal manager:
            - prova terminate()
            - attende un breve timeout
            - se non chiude, forza kill()
            L'obiettivo e' evitare processi zombie o rimasti appesi.

        Parametri:
            procs (List[subprocess.Popen]):
                Lista di processi (Popen) da terminare. Se contiene None o oggetti gia' terminati, vengono ignorati.

        Ritorno:
            Nessuno.

        Spiegazione:
            Riga 1:
                Itera su tutti i processi della lista.

            Riga 2:
                Salta gli elementi falsy (None) per robustezza.

            Riga 3:
                Prova terminate() e poi wait con timeout breve.

            Riga 4:
                Se terminate/wait falliscono (timeout o eccezione), prova kill().

            Riga 5:
                Se anche kill fallisce, ignora e continua per non bloccare l'intero stop.
        """
        for p in ps:
            try:
                if p.poll() is None:
                    p.terminate()
            except Exception:
                pass
        time.sleep(0.2)
        for p in ps:
            try:
                if p.poll() is None:
                    p.kill()
            except Exception:
                pass

    def _watcher(self, sim_id: str):
        """
        Thread watcher: attende la fine della simulazione e gestisce stop della cattura e aggiornamento stato.

        Scopo:
            Questo metodo viene eseguito in un thread daemon avviato da start().
            Si occupa di:
            - attendere la fine dei processi della simulazione,
            - garantire un tempo minimo di cattura tcpdump (MIN_CAPTURE_SEC),
            - aggiungere una coda di cattura (POST_CAPTURE_SEC),
            - fermare tcpdump,
            - aggiornare lo stato finale (terminated o interrupted) e i timestamp.

        Parametri:
            sim_id (str):
                Identificatore della simulazione da monitorare.

        Ritorno:
            Nessuno.


        Spiegazione riga per riga (nell'ordine del corpo):
            Riga 1:
                Recupera lo stato SimState della simulazione e ne fa una copia dei riferimenti necessari.

            Riga 2:
                Se la simulazione non esiste piu' (raro), esce.

            Riga 3:
                Attende la conclusione di tutti i processi registrati (con gestione errori).

            Riga 4:
                Calcola quanto tempo e' passato dall'avvio e, se e' meno di MIN_CAPTURE_SEC,
                attende il tempo rimanente per garantire una cattura minima.

            Riga 5:
                Attende POST_CAPTURE_SEC come "coda" per catturare anche gli ultimi pacchetti.

            Riga 6:
                Ferma la cattura tcpdump (se presente).

            Riga 7:
                Aggiorna lo stato finale della simulazione:
                - se era ancora "running" -> "terminated"
                - se era stata fermata altrove -> mantiene lo stato (es. "interrupted"/"error")

            Riga 8:
                Imposta il timestamp ended_at.
        """
        with self._lock:
            s = self.sims.get(sim_id)
        if not s:
            return

        if s.kind == "video":

            try:
                s.processes[-1].wait(timeout=self.VIDEO_DURATION_SEC + 10)
            except Exception:
                pass
            self._stop_processes(list(s.processes))
        else:
            for p in list(s.processes):
                try:
                    p.wait(timeout=90)
                except Exception:
                    pass


            self._stop_processes(list(getattr(s, "server_processes", [])))

            start_ts = s.started_at or time.time()
            now = time.time()
            min_end = start_ts + self.MIN_CAPTURE_SEC
            if now < min_end:
                time.sleep(max(0.0, min_end - now))
            time.sleep(self.POST_CAPTURE_SEC)

        with self._lock:
            s2 = self.sims.get(sim_id)
            if not s2:
                return
            if s2.status == "running":
                s2.status = "terminated"
                s2.ended_at = time.time()
                s2.processes = []
            self._stop_capture_locked(s2)
