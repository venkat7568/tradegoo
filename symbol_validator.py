#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
symbol_validator.py — Symbol validation and normalization
===========================================================
Prevents agents from guessing invalid symbol variations
"""

import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Common NSE symbol mappings (partial list - can be extended)
# This helps normalize company names to their trading symbols
# Format: "SEARCH_TERM" -> "TRADING_SYMBOL"
SYMBOL_ALIASES = {
    # Vinati Organics
    "VINATI ORGANICS": "VINATIORGA",
    "VINATI": "VINATIORGA",
    "VINATIORGANICS": "VINATIORGA",
    "VINATIORG": "VINATIORGA",
    "VINATIORGANIC": "VINATIORGA",

    # IT Companies
    "ITC": "ITC",
    "TCS": "TCS",
    "TATA CONSULTANCY": "TCS",
    "TATA CONSULTANCY SERVICES": "TCS",
    "INFY": "INFY",
    "INFOSYS": "INFY",
    "INFOSYS LIMITED": "INFY",
    "WIPRO": "WIPRO",
    "WIPRO LIMITED": "WIPRO",
    "HCLTECH": "HCLTECH",
    "HCL TECHNOLOGIES": "HCLTECH",
    "TECHM": "TECHM",
    "TECH MAHINDRA": "TECHM",

    # Banks
    "HDFCBANK": "HDFCBANK",
    "HDFC BANK": "HDFCBANK",
    "ICICIBANK": "ICICIBANK",
    "ICICI BANK": "ICICIBANK",
    "SBIN": "SBIN",
    "SBI": "SBIN",
    "STATE BANK": "SBIN",
    "KOTAKBANK": "KOTAKBANK",
    "KOTAK MAHINDRA": "KOTAKBANK",
    "AXISBANK": "AXISBANK",
    "AXIS BANK": "AXISBANK",

    # Conglomerates
    "RELIANCE": "RELIANCE",
    "RELIANCE INDUSTRIES": "RELIANCE",
    "RIL": "RELIANCE",
    "TATASTEEL": "TATASTEEL",
    "TATA STEEL": "TATASTEEL",
    "TATAMOTORS": "TATAMOTORS",
    "TATA MOTORS": "TATAMOTORS",

    # Pharma
    "SUNPHARMA": "SUNPHARMA",
    "SUN PHARMA": "SUNPHARMA",
    "DRREDDY": "DRREDDY",
    "DR REDDY": "DRREDDY",
    "CIPLA": "CIPLA",
    "DIVISLAB": "DIVISLAB",
    "DIVI'S LAB": "DIVISLAB",

    # Telecom
    "BHARTIARTL": "BHARTIARTL",
    "BHARTI AIRTEL": "BHARTIARTL",
    "AIRTEL": "BHARTIARTL",
    "HFCL": "HFCL",
    "HFCL LIMITED": "HFCL",

    # Auto
    "MARUTI": "MARUTI",
    "MARUTI SUZUKI": "MARUTI",
    "M&M": "M&M",
    "MAHINDRA": "M&M",
    "BAJAJ-AUTO": "BAJAJ-AUTO",
    "BAJAJ AUTO": "BAJAJ-AUTO",
    "EICHERMOT": "EICHERMOT",
    "EICHER MOTORS": "EICHERMOT",

    # FMCG
    "HINDUNILVR": "HINDUNILVR",
    "HINDUSTAN UNILEVER": "HINDUNILVR",
    "HUL": "HINDUNILVR",
    "NESTLEIND": "NESTLEIND",
    "NESTLE INDIA": "NESTLEIND",
    "BRITANNIA": "BRITANNIA",
    "ITC LTD": "ITC",
    "ITC LIMITED": "ITC",

    # Energy
    "NTPC": "NTPC",
    "NTPC LIMITED": "NTPC",
    "POWERGRID": "POWERGRID",
    "POWER GRID": "POWERGRID",
    "ONGC": "ONGC",
    "OIL AND NATURAL GAS": "ONGC",
    "BPCL": "BPCL",
    "BHARAT PETROLEUM": "BPCL",
}

def normalize_symbol(symbol: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Validate and normalize a trading symbol.

    Args:
        symbol: Input symbol (may be partial or full name)

    Returns:
        Tuple of (is_valid, normalized_symbol, error_message)
    """
    if not symbol or not isinstance(symbol, str):
        return False, None, "Symbol must be a non-empty string"

    # Clean up input
    clean = symbol.strip().upper().replace(" ", "")

    # Check direct match
    if clean in SYMBOL_ALIASES.values():
        logger.info(f"Symbol '{symbol}' validated: {clean}")
        return True, clean, None

    # Check aliases
    if clean in SYMBOL_ALIASES:
        normalized = SYMBOL_ALIASES[clean]
        logger.info(f"Symbol '{symbol}' mapped to: {normalized}")
        return True, normalized, None

    # Check with original spaces for full name lookup
    clean_with_spaces = symbol.strip().upper()
    if clean_with_spaces in SYMBOL_ALIASES:
        normalized = SYMBOL_ALIASES[clean_with_spaces]
        logger.info(f"Symbol '{symbol}' mapped to: {normalized}")
        return True, normalized, None

    # If not found, return original but flag as potentially invalid
    logger.warning(f"Symbol '{symbol}' not found in known symbols - using as-is: {clean}")
    return True, clean, f"Symbol '{clean}' not in validation list (may be valid NSE symbol)"


def validate_symbol_list(symbols: list[str]) -> Tuple[list[str], list[str]]:
    """
    Validate and normalize a list of symbols.

    Args:
        symbols: List of input symbols

    Returns:
        Tuple of (valid_symbols, errors)
    """
    valid = []
    errors = []

    for sym in symbols:
        is_valid, normalized, error = normalize_symbol(sym)
        if is_valid and normalized:
            valid.append(normalized)
        if error:
            errors.append(f"{sym}: {error}")

    return valid, errors


# For backward compatibility
def get_valid_symbol(symbol: str) -> Optional[str]:
    """Get validated symbol or None if invalid."""
    is_valid, normalized, _ = normalize_symbol(symbol)
    return normalized if is_valid else None
