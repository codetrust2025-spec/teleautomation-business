"""Deterministic helpers used around AI extraction and evaluation."""
from __future__ import annotations
import re
from datetime import datetime
from email.utils import parseaddr

def sender_domain(value:str)->str|None:
    address=parseaddr(value or '')[1].lower();return address.rsplit('@',1)[1] if '@' in address else None

def parse_currency(text:str)->str|None:
    value=(text or '').upper()
    if '₹' in value or re.search(r'\b(?:INR|LPA|LAKH)',value):return 'INR'
    if '$' in value or 'USD' in value:return 'USD'
    if 'EUR' in value or '€' in value:return 'EUR'
    if 'GBP' in value or '£' in value:return 'GBP'
    return None

def parse_ctc(text:str)->float|None:
    value=(text or '').replace(',','')
    match=re.search(r'(?:₹|INR\s*)?([0-9]+(?:\.[0-9]+)?)\s*(LPA|LAKH|LACS?)\b',value,re.I)
    if match:return float(match.group(1))*100000
    match=re.search(r'(?:CTC|COMPENSATION|SALARY)\D{0,20}(?:₹|INR)?\s*([0-9]{5,9})\b',value,re.I)
    return float(match.group(1)) if match else None

def parse_date(text:str)->str|None:
    value=(text or '').strip()
    for pattern in ('%Y-%m-%d','%d-%m-%Y','%d/%m/%Y','%d.%m.%Y','%d %B %Y','%d %b %Y'):
        try:return datetime.strptime(value,pattern).date().isoformat()
        except ValueError:pass
    return None

def parse_time(text:str)->str|None:
    value=(text or '').strip().upper().replace('.','')
    for pattern in ('%I:%M %p','%I %p','%H:%M'):
        try:return datetime.strptime(value,pattern).time().strftime('%H:%M:%S')
        except ValueError:pass
    return None
