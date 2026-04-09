from __future__ import annotations
"""
PDF Rapor Olusturucu - Detayli islem gecmisi ve performans analizi.
"""

import os
from datetime import datetime
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from trade_journal import TradeJournal, CoinLists
from logger_setup import setup_logger

logger = setup_logger("ReportGen")

REPORT_DIR = os.path.join(os.path.dirname(__file__), "reports")


def generate_pdf_report() -> str:
    """Detayli PDF rapor olustur. Dosya yolunu dondurur."""
    os.makedirs(REPORT_DIR, exist_ok=True)

    journal = TradeJournal()
    coin_lists = CoinLists()
    summary = journal.get_summary()
    now = datetime.now()
    filename = f"rapor_{now.strftime('%Y%m%d_%H%M')}.pdf"
    filepath = os.path.join(REPORT_DIR, filename)

    doc = SimpleDocTemplate(filepath, pagesize=A4,
                            topMargin=15*mm, bottomMargin=15*mm,
                            leftMargin=15*mm, rightMargin=15*mm)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("Title2", parent=styles["Title"], fontSize=18,
                                  spaceAfter=5*mm)
    subtitle_style = ParagraphStyle("Sub", parent=styles["Normal"], fontSize=12,
                                     textColor=colors.grey, spaceAfter=3*mm)
    h2_style = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=14,
                               spaceBefore=5*mm, spaceAfter=3*mm)
    normal = styles["Normal"]

    elements = []

    # --- BASLIK ---
    elements.append(Paragraph("BotTraderForNight - Islem Raporu", title_style))
    elements.append(Paragraph(f"Olusturulma: {now.strftime('%d.%m.%Y %H:%M')}", subtitle_style))
    elements.append(HRFlowable(width="100%", thickness=1, color=colors.grey))
    elements.append(Spacer(1, 5*mm))

    # --- GENEL OZET ---
    elements.append(Paragraph("Genel Ozet", h2_style))

    ozet_data = [
        ["Toplam Islem", str(summary["total_trades"])],
        ["Acik Pozisyon", str(summary["open"])],
        ["Kapanan Islem", str(summary["closed"])],
        ["Kazanan", f"{summary['wins']} ({summary['win_rate']:.0f}%)"],
        ["Kaybeden", str(summary["losses"])],
        ["Toplam PnL", f"${summary['total_pnl']:+.2f}"],
        ["Toplam Kazanc", f"${summary['total_wins']:+.2f}"],
        ["Toplam Kayip", f"${summary['total_losses']:.2f}"],
        ["Ort. Kazanc", f"${summary['avg_win']:.2f}"],
        ["Ort. Kayip", f"${summary['avg_loss']:.2f}"],
        ["Reddedilen Sinyal", str(summary["rejected_count"])],
        ["Tarama Sayisi", str(summary["scan_count"])],
    ]

    ozet_table = Table(ozet_data, colWidths=[50*mm, 50*mm])
    ozet_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.Color(0.95, 0.95, 0.95)),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(ozet_table)
    elements.append(Spacer(1, 5*mm))

    # --- KAPANAN ISLEMLER ---
    closed_trades = [t for t in journal.data["trades"] if t["status"] == "CLOSED"]
    if closed_trades:
        elements.append(Paragraph("Kapanan Islemler", h2_style))

        header = ["#", "Coin", "Yon", "Giris", "Cikis", "PnL $", "PnL %", "Sebep"]
        trade_rows = [header]

        for t in closed_trades:
            pnl = t["pnl"] or 0
            pnl_pct = t["pnl_pct"] or 0
            reason = (t["close_reason"] or "")[:25]
            trade_rows.append([
                str(t["id"]),
                t["symbol"].replace("USDT", ""),
                t["side"],
                f"{t['entry_price']}",
                f"{t['close_price']}",
                f"${pnl:+.2f}",
                f"%{pnl_pct:+.2f}",
                reason,
            ])

        col_w = [8*mm, 22*mm, 12*mm, 22*mm, 22*mm, 20*mm, 16*mm, 45*mm]
        trade_table = Table(trade_rows, colWidths=col_w)

        style_cmds = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.2, 0.2, 0.2)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]

        # Karda yesil, zararda kirmizi
        for i, t in enumerate(closed_trades, start=1):
            pnl = t["pnl"] or 0
            if pnl > 0:
                style_cmds.append(("BACKGROUND", (0, i), (-1, i),
                                   colors.Color(0.9, 1, 0.9)))
            else:
                style_cmds.append(("BACKGROUND", (0, i), (-1, i),
                                   colors.Color(1, 0.92, 0.92)))

        trade_table.setStyle(TableStyle(style_cmds))
        elements.append(trade_table)
        elements.append(Spacer(1, 5*mm))

    # --- ACIK POZISYONLAR ---
    open_trades = [t for t in journal.data["trades"] if t["status"] == "OPEN"]
    if open_trades:
        elements.append(Paragraph("Acik Pozisyonlar", h2_style))

        header = ["#", "Coin", "Yon", "Giris", "Skor", "Acilis Zamani"]
        open_rows = [header]
        for t in open_trades:
            open_rows.append([
                str(t["id"]),
                t["symbol"].replace("USDT", ""),
                t["side"],
                f"{t['entry_price']}",
                f"{t['score']}",
                t["open_time"][:16].replace("T", " "),
            ])

        open_table = Table(open_rows, colWidths=[8*mm, 25*mm, 12*mm, 25*mm, 15*mm, 40*mm])
        open_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.2, 0.4, 0.6)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ]))
        elements.append(open_table)
        elements.append(Spacer(1, 5*mm))

    # --- REDDEDILEN SINYALLER (son 20) ---
    rejected = journal.data.get("rejected", [])[-20:]
    if rejected:
        elements.append(Paragraph("Son Reddedilen Sinyaller", h2_style))

        rej_rows = [["Coin", "Skor", "Sebep", "Zaman"]]
        for r in reversed(rejected):
            rej_rows.append([
                r["symbol"].replace("USDT", ""),
                f"{r['score']:.1f}",
                r.get("reason", "")[:30],
                r["time"][11:16],
            ])

        rej_table = Table(rej_rows, colWidths=[25*mm, 15*mm, 55*mm, 15*mm])
        rej_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.6, 0.2, 0.2)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ]))
        elements.append(rej_table)
        elements.append(Spacer(1, 5*mm))

    # --- COIN LISTELERI ---
    cl = coin_lists.data
    if cl.get("whitelist") or cl.get("blacklist"):
        elements.append(Paragraph("Coin Listeleri", h2_style))

        if cl.get("whitelist"):
            elements.append(Paragraph("Whitelist (Guvenilir)", normal))
            for sym, info in cl["whitelist"].items():
                elements.append(Paragraph(f"  {sym}: {info['reason']}", normal))

        if cl.get("blacklist"):
            elements.append(Paragraph("Blacklist (Kacinilacak)", normal))
            for sym, info in cl["blacklist"].items():
                elements.append(Paragraph(f"  {sym}: {info['reason']}", normal))

    # --- TARAMA ISTATISTIKLERI ---
    scans = journal.data.get("scans", [])
    if scans:
        elements.append(Paragraph("Tarama Istatistikleri", h2_style))
        total_scanned = sum(s["total_coins"] for s in scans)
        total_signals = sum(s["signals"] for s in scans)
        avg_duration = sum(s["duration_sec"] for s in scans) / len(scans)
        elements.append(Paragraph(
            f"Toplam tarama: {len(scans)} | "
            f"Taranan coin: {total_scanned} | "
            f"Bulunan sinyal: {total_signals} | "
            f"Ort. sure: {avg_duration:.0f}sn",
            normal
        ))

    # PDF olustur
    doc.build(elements)
    logger.info(f"PDF rapor olusturuldu: {filepath}")
    return filepath
