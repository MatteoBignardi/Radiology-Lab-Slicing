#!/usr/bin/python3
# -*- coding: utf-8 -*-

import topology_defs as topo_defs


DEFAULT_REST_PORT = 8080
VIDEO_UDP_DST_PORT = 9999

SERVICE_SLICE_VIDEO = "Video"
SERVICE_SLICE_NON_VIDEO = "NonVideo"

HOST_MAC = topo_defs.HOSTS

# -------------------- Regole di slicing in modalità TOPOLOGY - DAY --------------------
#
# TOPOLOGY_DAY_SLICES_RULES è una LISTA di slice.
# Ogni elemento della lista rappresenta una slice (Slice 1, Slice 2, Slice 3).
#
# Ogni slice è a sua volta un DIZIONARIO:
#    { DPID_switch : { MAC_host_dest : porta_uscita } }
#
# Queste strutture sono pensate per essere usate dal controller SDN per installare
# flow "destination MAC -> output port" sui vari switch, separati per slice.
TOPOLOGY_DAY_SLICES_RULES = [
    {
        # s1 Diagnostic
        "0000000000000001": {
            HOST_MAC["hRadWS1"]: 3,
            HOST_MAC["hRadWS2"]: 4,
            HOST_MAC["hPrDiag"]: 5,
            # verso Imaging (porta 1)
            HOST_MAC["hImgDev1"]: 1,
            HOST_MAC["hImgDev2"]: 1,
            HOST_MAC["hPrImg"]:   1,
            HOST_MAC["hPACS"]:    1,
            HOST_MAC["hBroker"]:  1
        },
        # s2 Imaging
        "0000000000000002": {
            HOST_MAC["hImgDev1"]: 3,
            HOST_MAC["hImgDev2"]: 4,
            HOST_MAC["hPrImg"]:   5,
            # verso Diagnostic (porta 1)
            HOST_MAC["hRadWS1"]: 1,
            HOST_MAC["hRadWS2"]: 1,
            HOST_MAC["hPrDiag"]: 1,
            # verso Interconnection (porta 2)
            HOST_MAC["hPACS"]:    2,
            HOST_MAC["hBroker"]:  2
        },
        # s3 Interconnection 
        "0000000000000003": {
            # verso Imaging (porta 1)
            HOST_MAC["hRadWS1"]: 1,
            HOST_MAC["hRadWS2"]: 1,
            HOST_MAC["hPrDiag"]: 1,
            HOST_MAC["hImgDev1"]: 1,
            HOST_MAC["hImgDev2"]: 1,
            HOST_MAC["hPrImg"]:   1,
            HOST_MAC["hBroker"]:   2,           
            # verso BackboneToServer s4 (porta 2)
            HOST_MAC["hPACS"]:    2,
},
        # s4 BackboneToServer
        "0000000000000004": {
            # verso Interconnection (porta 1)
            HOST_MAC["hRadWS1"]: 1,
            HOST_MAC["hRadWS2"]: 1,
            HOST_MAC["hPrDiag"]: 1,
            HOST_MAC["hImgDev1"]: 1,
            HOST_MAC["hImgDev2"]: 1,
            HOST_MAC["hPrImg"]:   1,
            # verso Server (porta 2)
            HOST_MAC["hPACS"]:    2,
            HOST_MAC["hBroker"]:  2
        },
        # s5 ServerZone
        "0000000000000005": {
            HOST_MAC["hPACS"]:   3,
            HOST_MAC["hBroker"]: 6,
            # ritorno verso s4 (porta 1)
            HOST_MAC["hRadWS1"]: 1,
            HOST_MAC["hRadWS2"]: 1,
            HOST_MAC["hPrDiag"]: 1,
            HOST_MAC["hImgDev1"]: 1,
            HOST_MAC["hImgDev2"]: 1,
            HOST_MAC["hPrImg"]:   1
        }
    },

    {
        "0000000000000006": {  # s6 SecurityZone
            HOST_MAC["nvr"]: 3,
            HOST_MAC["hCam1"]:   4,
            HOST_MAC["hCam2"]:   5,
            # verso s3 (porta 2) per raggiungere Admin hosts via s7/s8
            HOST_MAC["lCS"]: 2
        },
        "0000000000000003": {  # s3 Interconnection (in rosso si usa il link verso s7)
            HOST_MAC["nvr"]: 3,  # verso s6 (porta 3)
            HOST_MAC["hCam1"]:   3,
            HOST_MAC["hCam2"]:   3,
            HOST_MAC["lCS"]: 4
        },
        "0000000000000007": {  # s7 BackboneToAdmin
            HOST_MAC["nvr"]: 1,  # verso s3 (porta 1)
            HOST_MAC["hCam1"]:   1,
            HOST_MAC["hCam2"]:   1,
            HOST_MAC["lCS"]: 2
        },
        "0000000000000008": {  # s8 AdminZone
            HOST_MAC["lCS"]: 3,
            # verso s7 (porta 2) per raggiungere Security hosts
            HOST_MAC["nvr"]:  2,
            HOST_MAC["hCam1"]:    2,
            HOST_MAC["hCam2"]:    2,
        },
    },

    {
        "0000000000000008": {  # s8 verso Server (porta 1)
            HOST_MAC["hRISW1"]:   4,
            HOST_MAC["hRISW2"]:   5,
            HOST_MAC["hRIS"]:     1,
            HOST_MAC["hBroker"]:  1,
        },
        "0000000000000005": {  # s5 verso Admin (porta 2)
            HOST_MAC["hRIS"]:    5,
            HOST_MAC["hBroker"]: 6,
            HOST_MAC["hRISW1"]:   2,
            HOST_MAC["hRISW2"]:   2,
        },
    },
]

# -------------------- Regole di slicing in modalità TOPOLOGY - NIGHT --------------------
#
# In NIGHT:
# - Slice 1 viene disabilitata ({} vuoto)
# - Slice 2 uguale al DAY
# - Slice 3 simile al DAY ma senza hBroker (quindi Broker non raggiungibile)
TOPOLOGY_NIGHT_SLICES_RULES = [
    {},  # Slice 1 (Radiology) disabilitata in NIGHT
    TOPOLOGY_DAY_SLICES_RULES[1],  # Slice 2 (Security) uguale
    { # Slice 3 (Admin Core) NIGHT: come DAY senza hBroker
        "0000000000000008": { 
            HOST_MAC["hRISW1"]:   4,
            HOST_MAC["hRISW2"]:   5,
            HOST_MAC["hRIS"]:     1,
        },
        "0000000000000005": {  
            HOST_MAC["hRIS"]:     5,
            HOST_MAC["hRISW1"]:   2,
            HOST_MAC["hRISW2"]:   2,
        },
    },  
]


SERVICE_NONVIDEO_MAP = {
    "0000000000000001": {
        HOST_MAC["hRadWS1"]: 3,
        HOST_MAC["hRadWS2"]: 4,
        HOST_MAC["hPrDiag"]: 5,
        HOST_MAC["hImgDev1"]: 1,
        HOST_MAC["hImgDev2"]: 1,
        HOST_MAC["hPrImg"]:   1,
        HOST_MAC["hPACS"]:    1,
        HOST_MAC["hRIS"]:     1,
        HOST_MAC["hBroker"]:  1,
        HOST_MAC["lCS"]: 1,
        HOST_MAC["hRISW1"]:   1,
        HOST_MAC["hRISW2"]:   1,
        HOST_MAC["nvr"]:  2,
        HOST_MAC["hCam1"]:    2,
        HOST_MAC["hCam2"]:    2,
    },

    "0000000000000002": {
        HOST_MAC["hImgDev1"]: 3,
        HOST_MAC["hImgDev2"]: 4,
        HOST_MAC["hPrImg"]:   5,

        HOST_MAC["hRadWS1"]:  1,
        HOST_MAC["hRadWS2"]:  1,
        HOST_MAC["hPrDiag"]:  1,

        HOST_MAC["hPACS"]:    2,
        HOST_MAC["hRIS"]:     2,
        HOST_MAC["hBroker"]:  2,
        HOST_MAC["lCS"]: 2,
        HOST_MAC["hRISW1"]:   2,
        HOST_MAC["hRISW2"]:   2,
        HOST_MAC["nvr"]:  1,
        HOST_MAC["hCam1"]:    1,
        HOST_MAC["hCam2"]:    1,
    },

    "0000000000000003": {
        HOST_MAC["hRadWS1"]:  1,
        HOST_MAC["hRadWS2"]:  1,
        HOST_MAC["hPrDiag"]:  1,
        HOST_MAC["hImgDev1"]: 1,
        HOST_MAC["hImgDev2"]: 1,
        HOST_MAC["hPrImg"]:   1,
        HOST_MAC["hPACS"]:    2,
        HOST_MAC["hRIS"]:     2,
        HOST_MAC["hBroker"]:  2,
        HOST_MAC["lCS"]: 2,
        HOST_MAC["hRISW1"]:   2,
        HOST_MAC["hRISW2"]:   2,
        HOST_MAC["nvr"]:  1,
        HOST_MAC["hCam1"]:    1,
        HOST_MAC["hCam2"]:    1,
    },

    "0000000000000004": {
        HOST_MAC["hRadWS1"]:  1,
        HOST_MAC["hRadWS2"]:  1,
        HOST_MAC["hPrDiag"]:  1,
        HOST_MAC["hImgDev1"]: 1,
        HOST_MAC["hImgDev2"]: 1,
        HOST_MAC["hPrImg"]:   1,
        HOST_MAC["nvr"]:  1,
        HOST_MAC["hCam1"]:    1,
        HOST_MAC["hCam2"]:    1,
        HOST_MAC["hPACS"]:    2,
        HOST_MAC["hRIS"]:     2,
        HOST_MAC["hBroker"]:  2,
        HOST_MAC["lCS"]: 2,
        HOST_MAC["hRISW1"]:   2,
        HOST_MAC["hRISW2"]:   2,
    },

    "0000000000000005": {
        HOST_MAC["hPACS"]:    3,
        HOST_MAC["hRIS"]:     5,
        HOST_MAC["hBroker"]:  6,
        HOST_MAC["hRadWS1"]:  1,
        HOST_MAC["hRadWS2"]:  1,
        HOST_MAC["hPrDiag"]:  1,
        HOST_MAC["hImgDev1"]: 1,
        HOST_MAC["hImgDev2"]: 1,
        HOST_MAC["hPrImg"]:   1,
        HOST_MAC["nvr"]:  1,
        HOST_MAC["hCam1"]:    1,
        HOST_MAC["hCam2"]:    1,
        HOST_MAC["lCS"]: 2,
        HOST_MAC["hRISW1"]:   2,
        HOST_MAC["hRISW2"]:   2,
    },

    "0000000000000006": {
        HOST_MAC["nvr"]: 3,
        HOST_MAC["hCam1"]:   4,
        HOST_MAC["hCam2"]:   5,
        HOST_MAC["hRadWS1"]:  1,
        HOST_MAC["hRadWS2"]:  1,
        HOST_MAC["hPrDiag"]:  1,
        HOST_MAC["hImgDev1"]: 1,
        HOST_MAC["hImgDev2"]: 1,
        HOST_MAC["hPrImg"]:   1,
        HOST_MAC["hPACS"]:    1,
        HOST_MAC["hRIS"]:     1,
        HOST_MAC["hBroker"]:  1,
        HOST_MAC["lCS"]: 1,
        HOST_MAC["hRISW1"]:   1,
        HOST_MAC["hRISW2"]:   1,
    },

    "0000000000000008": {
        HOST_MAC["lCS"]: 3,
        HOST_MAC["hRISW1"]:   4,
        HOST_MAC["hRISW2"]:   5,
        HOST_MAC["hPACS"]:    1,
        HOST_MAC["hRIS"]:     1,
        HOST_MAC["hBroker"]:  1,
        HOST_MAC["hRadWS1"]:  1,
        HOST_MAC["hRadWS2"]:  1,
        HOST_MAC["hPrDiag"]:  1,
        HOST_MAC["hImgDev1"]: 1,
        HOST_MAC["hImgDev2"]: 1,
        HOST_MAC["hPrImg"]:   1,
        HOST_MAC["nvr"]:  1,
        HOST_MAC["hCam1"]:    1,
        HOST_MAC["hCam2"]:    1,
    },
}

SERVICE_VIDEO_MAP = {
    "0000000000000006": {
        HOST_MAC["nvr"]:   3,  
        HOST_MAC["lCS"]:  2,  
        HOST_MAC["hCam1"]:  4,  
        HOST_MAC["hCam2"]:  5, 
    },
    "0000000000000003": {
        HOST_MAC["nvr"]:   3, 
        HOST_MAC["lCS"]:  4, 
        HOST_MAC["hCam1"]:  3, 
        HOST_MAC["hCam2"]:  3,  
    },

    "0000000000000007": {
        HOST_MAC["nvr"]:   1,  
        HOST_MAC["lCS"]:  2, 
        HOST_MAC["hCam1"]:  1, 
        HOST_MAC["hCam2"]:  1,  
    },

    "0000000000000008": {
        HOST_MAC["lCS"]:  3,  
        HOST_MAC["nvr"]:   2, 
        HOST_MAC["hCam1"]:  2,  
        HOST_MAC["hCam2"]:  2, 
    },
}


class ProblemConstants:
    """
    Costanti "di problema" per l'algoritmo di slicing.

    Scopo:
        Raggruppare parametri globali usati da altri moduli (es. mapper/controller).
        In questo file contiene:
            - NUM_SLICES: numero totale di slice supportate
            - INCOMPATIBLE_SLICES: vincoli di incompatibilità tra slice

    Nota:
        INCOMPATIBLE_SLICES qui è inizializzato vuoto per tutte le slice,
        ma la struttura supporta l'idea:
            {slice_id: [lista_slice_non_compatibili]}
    """
    NUM_SLICES = 3
    INCOMPATIBLE_SLICES = {1: [], 2: [], 3: []}


# mantengo come default DAY (ma in runtime il mapper riceve DAY o NIGHT esplicitamente).
SLICES_RULES = TOPOLOGY_DAY_SLICES_RULES


class ControllerState:
    """
        Inizializza lo stato del controller con valori di default custom.

        Default:
            - active_slicing_mode = TOPOLOGY  (regole per slice)
            - active_mode         = DAY       (contesto diurno)
            - enabled_*           = set() vuoti (niente abilitato finché non configurato)
            - mappers             = {DAY: None, NIGHT: None} (cache mapper non ancora creata)
    """
    TOPOLOGY = "topology"
    SERVICE  = "service"

    DAY   = "day"
    NIGHT = "night"

    def __init__(self):
        self.active_slicing_mode = self.TOPOLOGY
        self.active_mode = self.DAY
        self.enabled_topology = set()
        self.enabled_service = set()
        self.mappers = {self.DAY: None, self.NIGHT: None}
