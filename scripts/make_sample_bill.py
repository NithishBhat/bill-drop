"""Draw the fake demo bill (sample_bills/sample_eversource_bill.png). All data is made up.

Usage:  python scripts/make_sample_bill.py   (needs Pillow: pip install -r requirements-dev.txt)
Keep the numbers in sync with tests/test_live_accuracy.py.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "sample_bills" / "sample_eversource_bill.png"

W, H = 1000, 1400
img = Image.new("RGB", (W, H), "white")
d = ImageDraw.Draw(img)
try:
    big, mid, small = (ImageFont.truetype("arial.ttf", s) for s in (36, 22, 18))
except OSError:
    big = mid = small = ImageFont.load_default()

d.text((40, 30), "SAMPLE ELECTRIC BILL - TEST DATA", font=big, fill="black")
d.text((40, 85), "Eversource (Eastern Massachusetts)", font=mid, fill="black")
y = 140
for label, val in [
    ("Customer Name", "JANE SAMPLE"),
    ("Name Key", "SAMP"),
    ("Account Number", "1234 567 8901"),
    ("Service Reference", "98765432"),
    ("Meter Number", "M123456789"),
    ("Service Address", "110 Example St, Boston MA 02115"),
    ("Billing Period", "Sep 03, 2026 - Oct 02, 2026 (30 days)"),
    ("Rate", "R-1 Residential"),
]:
    d.text((40, y), f"{label}: {val}", font=mid, fill="black"); y += 36

y += 20
d.text((40, y), "Delivery Services - Eversource", font=mid, fill="black"); y += 34
for label, val in [
    ("Customer Charge", "$10.00"),
    ("Distribution Charge 612 kWh x $0.09837", "$60.20"),
    ("Transmission 612 kWh x $0.04673", "$28.60"),
    ("Energy Efficiency / Other 612 kWh x $0.02342", "$14.33"),
]:
    d.text((60, y), label, font=small, fill="black"); d.text((800, y), val, font=small, fill="black"); y += 28
y += 14
d.text((40, y), "Supply Services - Basic Service (Eversource)", font=mid, fill="black"); y += 34
d.text((60, y), "Generation Charge 612 kWh x 17.323 cents", font=small, fill="black")
d.text((800, y), "$106.02", font=small, fill="black"); y += 40
d.text((40, y), "Total Amount Due Nov 01, 2026", font=mid, fill="black")
d.text((800, y), "$219.15", font=mid, fill="black"); y += 70

d.text((40, y), "Your Monthly kWh Usage", font=mid, fill="black"); y += 40
hist = [("10/25", 540), ("11/25", 590), ("12/25", 820), ("1/26", 910), ("2/26", 860), ("3/26", 700),
        ("4/26", 560), ("5/26", 480), ("6/26", 590), ("7/26", 720), ("8/26", 690), ("9/26", 612)]
base = y + 330
for i, (m, k) in enumerate(hist):
    x = 60 + i * 75
    d.rectangle([x, base - k * 0.33, x + 45, base], fill="#4a7bd0")
    d.text((x, base - k * 0.33 - 24), str(k), font=small, fill="black")
    d.text((x, base + 8), m, font=small, fill="black")

img.save(OUT)
print("saved", OUT)
