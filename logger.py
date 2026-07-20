# -*- coding: utf-8 -*-
"""
Centralized structured logger for Infomary backend.
Color-coded, timestamped, categorized logs.
"""
import logging
import sys
from datetime import datetime

# ANSI color codes
RESET   = "\033[0m"
BOLD    = "\033[1m"
DIM     = "\033[2m"

RED     = "\033[31m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
BLUE    = "\033[34m"
MAGENTA = "\033[35m"
CYAN    = "\033[36m"
WHITE   = "\033[37m"

BRIGHT_RED     = "\033[91m"
BRIGHT_GREEN   = "\033[92m"
BRIGHT_YELLOW  = "\033[93m"
BRIGHT_BLUE    = "\033[94m"
BRIGHT_MAGENTA = "\033[95m"
BRIGHT_CYAN    = "\033[96m"
BRIGHT_WHITE   = "\033[97m"

# (color, label) — plain ASCII only, no emojis
CATEGORIES = {
    "STARTUP": (BRIGHT_GREEN,  "STARTUP   "),
    "DB":      (BRIGHT_CYAN,   "DATABASE  "),
    "WS":      (BRIGHT_BLUE,   "WEBSOCKET "),
    "LLM":     (BRIGHT_MAGENTA,"LLM       "),
    "TOOL":    (BRIGHT_YELLOW, "TOOL      "),
    "LEAD":    (BRIGHT_GREEN,  "LEAD      "),
    "SHEET":   (CYAN,          "SHEET     "),
    "EMAIL":   (MAGENTA,       "EMAIL     "),
    "SEARCH":  (YELLOW,        "SEARCH    "),
    "API":     (BLUE,          "API       "),
    "SUCCESS": (BRIGHT_GREEN,  "SUCCESS   "),
    "WARN":    (YELLOW,        "WARN      "),
    "ERROR":   (BRIGHT_RED,    "ERROR     "),
}


class InfomaryFormatter(logging.Formatter):
    def format(self, record):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        ts_str = f"{DIM}{ts}{RESET}"

        msg = record.getMessage()
        color = WHITE
        prefix = "INFO      "

        for cat, (c, p) in CATEGORIES.items():
            if f"[{cat}]" in msg:
                color = c
                prefix = p
                msg = msg.replace(f"[{cat}]", "").strip()
                break

        if record.levelno == logging.ERROR:
            color = BRIGHT_RED
            prefix = CATEGORIES["ERROR"][1]
        elif record.levelno == logging.WARNING:
            color = YELLOW
            prefix = CATEGORIES["WARN"][1]

        sep = f"{DIM}|{RESET}"
        return f"{ts_str} {sep} {color}{BOLD}{prefix}{RESET} {sep} {color}{msg}{RESET}"


def get_logger(name: str = "infomary") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(InfomaryFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger


_log = get_logger()

def log_startup(msg: str):  _log.info(f"[STARTUP] {msg}")
def log_db(msg: str):       _log.info(f"[DB] {msg}")
def log_ws(msg: str):       _log.info(f"[WS] {msg}")
def log_llm(msg: str):      _log.info(f"[LLM] {msg}")
def log_tool(msg: str):     _log.info(f"[TOOL] {msg}")
def log_lead(msg: str):     _log.info(f"[LEAD] {msg}")
def log_sheet(msg: str):    _log.info(f"[SHEET] {msg}")
def log_email(msg: str):    _log.info(f"[EMAIL] {msg}")
def log_search(msg: str):   _log.info(f"[SEARCH] {msg}")
def log_api(msg: str):      _log.info(f"[API] {msg}")
def log_success(msg: str):  _log.info(f"[SUCCESS] {msg}")
def log_warn(msg: str):     _log.warning(f"[WARN] {msg}")
def log_error(msg: str):    _log.error(f"[ERROR] {msg}")

def log_divider(label: str = ""):
    width = 56
    if label:
        pad = max((width - len(label) - 2) // 2, 2)
        line = "-" * pad + f" {label} " + "-" * pad
    else:
        line = "-" * width
    _log.info(f"{DIM}{line}{RESET}")
