"""Generate the simple, original 256px application icon without external assets."""
from __future__ import annotations
from pathlib import Path
from PIL import Image, ImageDraw

OUT = Path(__file__).with_name("MicroSlideStitcher.png")
SIZE = 256
im = Image.new("RGBA", (SIZE, SIZE), "#f5f1e8")
d = ImageDraw.Draw(im)
# microscope body and slide panorama: deliberately bold at desktop-icon sizes.
d.rounded_rectangle((24, 24, 232, 232), radius=42, fill="#1f4c5c")
d.rounded_rectangle((47, 152, 211, 187), radius=8, fill="#efc66b", outline="#ffffff", width=5)
d.rectangle((56, 159, 202, 180), fill="#b76752")
d.rounded_rectangle((92, 50, 138, 129), radius=16, fill="#d5e8e5", outline="#ffffff", width=6)
d.polygon([(127, 65), (184, 104), (174, 121), (113, 82)], fill="#74b6b2", outline="#ffffff")
d.ellipse((90, 106, 142, 158), fill="#253a45", outline="#ffffff", width=6)
d.rounded_rectangle((77, 132, 159, 149), radius=7, fill="#d5e8e5", outline="#ffffff", width=4)
d.rounded_rectangle((64, 185, 196, 203), radius=9, fill="#d5e8e5")
im.save(OUT)
print(OUT, im.size)
