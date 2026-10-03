"""Manual smoke test: feed SMS messages through the parser and print results.

Usage:
    python -m scripts.try_parser "AS- Rs 500 debited via UPI to SWIGGY on 12-08-2025 14:32"
"""
import asyncio
import sys

from app.schemas import TransactionType
from app.services.parser import parse_sms


def show(text: str) -> None:
    p = parse_sms(text)
    print(f"\ninput     : {text}")
    print(f"amount    : {p.amount}  ({p.txn_type.value})")
    print(f"mode      : {p.txn_mode.value if p.txn_mode else None}")
    print(f"merchant  : {p.merchant}")
    print(f"upi handle: {p.upi_handle}")
    print(f"upi ref   : {p.upi_ref}")
    print(f"account   : ****{p.account_last_four}" if p.account_last_four else "account   : -")
    print(f"bank      : {p.bank_name}")
    print(f"when      : {p.txn_timestamp}")
    print(f"balance   : {p.balance}")
    print(f"noise     : {p.is_noise} {('(' + p.noise_reason + ')') if p.noise_reason else ''}")
    print(f"confidence: {p.confidence}  missing={p.missing or '-'}")


if __name__ == "__main__":
    samples = sys.argv[1:] or [
        "HDFC Bank: Rs. 1,250.50 debited from A/C XXXXXX8899 via UPI to SWIGGY on 12-08-2025 14:32, UPI Ref No 523418972651. Avl bal Rs. 45,230.75",
        "INR 2,499.00 spent on Amazon Seller Services VISA Card XX1234 on 05-08-2025 21:10. Avl Lmt Rs. 1,48,000",
        "Rs. 85,000.00 credited to your SBI A/C XXXXXX2211 on 01-09-2025 towards salary",
        "Your OTP for the transaction of Rs. 45,000 is 883421. Do not share with anyone.",
        "Rs 500 withdrawn from SBI ATM XXXXXX2231 on 11-08-2025 22:40",
    ]
    for s in samples:
        show(s)