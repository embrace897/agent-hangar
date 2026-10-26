"""Vídeo do README: o Hangar desktop conectado ao Agent Hangar (o que é, benefícios, instalação, uso).
Capturas reais em /demo/shots (app Electron e console na instância de desenvolvimento); cartões gerados aqui."""
import pathlib

import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFont

D = pathlib.Path("/demo")
SH = D / "shots"
OUT = D / "out"
OUT.mkdir(exist_ok=True)
W, H, BAR = 1280, 800, 60
NAVY, NAVY2, AMBER, WHITE, MUTE = (28, 37, 48), (38, 50, 64), (244, 180, 0), (255, 255, 255), (170, 182, 196)
F = "/usr/share/fonts/truetype/liberation/LiberationSans-"
def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(F + ("Bold" if bold else "Regular") + ".ttf", size)


def card(kicker: str, title: str, lines: list[str], foot: str = "") -> Image.Image:
    im = Image.new("RGB", (W, H + BAR), NAVY)
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 14, H + BAR], fill=AMBER)
    y = 120
    if kicker:
        d.text((110, y), kicker.upper(), font=font(24), fill=AMBER)
        y += 52
    for t in title.split("\n"):
        d.text((110, y), t, font=font(54), fill=WHITE)
        y += 70
    y += 26
    for ln in lines:
        if ln.startswith("•"):
            d.ellipse([112, y + 12, 124, y + 24], fill=AMBER)
            d.text((146, y), ln[1:].strip(), font=font(29, False), fill=WHITE)
            y += 52
        else:
            d.text((110, y), ln, font=font(29, False), fill=MUTE)
            y += 46
    if foot:
        d.text((110, H + BAR - 90), foot, font=font(24, False), fill=MUTE)
    return im


def slide(name: str, caption: str) -> Image.Image:
    img = Image.open(SH / f"{name}.png").convert("RGB").resize((W, H), Image.LANCZOS)
    im = Image.new("RGB", (W, H + BAR), NAVY)
    im.paste(img, (0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle([0, H, 10, H + BAR], fill=AMBER)
    d.text((30, H + BAR // 2), caption, font=font(23), fill=WHITE, anchor="lm")
    return im


SEQ = [
    (card("Agent Hangar v0.53", "Hangar desktop,\nconnected to your company",
          ["A desktop app for Claude Code sessions, inspired by Claude Desktop.",
           "Now it signs in to Agent Hangar and brings the company's models,",
           "agents and decisions to the person's computer."]), 6.0),
    (card("Why it matters", "What the connection adds",
          ["• Sign in with the company account (OAuth, SSO): no API keys on laptops",
           "• Company-paid token models or your own Claude subscription, per session",
           "• Provider keys stay in the platform; budgets and code policy apply",
           "• Usage and cost recorded per person and team, without logging content",
           "• Your agents, decisions and inbox in the app, updated live",
           "• Admins see every device and revoke it in one click"]), 10.0),
    (card("Install", "Download and open",
          ["• github.com/valteresj2/hangar/releases",
           "• Windows: installer or portable · macOS: .dmg · Linux: AppImage or .deb",
           "• Unsigned builds · Windows: More info → Run anyway",
           "• Unsigned builds · macOS: right-click the app → Open",
           "• Claude Code ships inside the app: no separate install"]), 9.0),
    (slide("c01-hub", "Admin · Console → App Hangar: the URL to share, how to connect, the Windows policy"), 5.5),
    (slide("c02b-synced", "Admin · LLM Providers: one click syncs a connection's token models to the desktop"), 6.0),
    (slide("d01-home", "Member · Hangar works on its own; the platform is one click away"), 4.0),
    (slide("d02-settings", "Settings → Plataforma: paste the company URL and choose Conectar"), 5.0),
    (slide("d03-consent", "The browser opens the platform: sign in and authorize the app"), 5.0),
    (slide("d04-connected", "Connected: account, company, teams and the synced token models"), 5.0),
    (slide("d05-inicio", "Início: decisions, tasks, agents and cost, updated live"), 5.5),
    (slide("d06-agentes", "Meus agentes: personal agents and the team's Digital employees"), 4.5),
    (slide("d08-picker", "A new session on a company token model: Claude Sonnet 4.5 through claude-empresa"), 5.5),
    (slide("d09-streaming", "The turn goes app → platform proxy → provider; the provider key never leaves the vault"), 4.5),
    (slide("d10-answer", "The answer streams in, with the model, tokens and estimated cost of the turn"), 5.5),
    (slide("c03-devices", "Admin · Devices: who is connected and on which version; Revogar disconnects at once"), 5.5),
    (card("Get started", "Agent Hangar + Hangar desktop",
          ["• Platform: github.com/valteresj2/agent-hangar",
           "• App: github.com/valteresj2/hangar/releases",
           "• Guide: valteresj2.github.io/agent-hangar/docs/desktop-app/"],
          "Recorded on a development instance; people and companies are fictional."), 6.0),
]

FPS, FADE = 24, 10
path = OUT / "hangar-desktop.mp4"
writer = imageio_ffmpeg.write_frames(str(path), (W, H + BAR), fps=FPS, codec="libx264", pix_fmt_out="yuv420p",
                                     bitrate="450k", macro_block_size=4,
                                     output_params=["-tune", "stillimage", "-movflags", "+faststart"])
writer.send(None)
arrs = [np.asarray(im) for im, _ in SEQ]
for i, (a, (_, secs)) in enumerate(zip(arrs, SEQ, strict=True)):
    for _ in range(int(secs * FPS)):
        writer.send(a.tobytes())
    if i + 1 < len(arrs):
        b = arrs[i + 1]
        for k in range(1, FADE + 1):
            t = k / (FADE + 1)
            writer.send((a * (1 - t) + b * t).astype(np.uint8).tobytes())
writer.close()

# capturas para o README (1200 px, PNG otimizado)
for n in ("d05-inicio", "d10-answer", "c02b-synced", "d03-consent"):
    im = Image.open(SH / f"{n}.png").convert("RGB")
    im = im.resize((1200, int(im.height * 1200 / im.width)), Image.LANCZOS)
    im.quantize(colors=256, method=Image.Quantize.MEDIANCUT).save(OUT / f"{n.split('-', 1)[1]}.png", optimize=True)
SEQ[1][0].save(OUT / "benefits-card.png")
print("total", round(sum(s for _, s in SEQ) + FADE / FPS * (len(SEQ) - 1), 1), "s")
for f in sorted(OUT.iterdir()):
    print(f.name, round(f.stat().st_size / 1024), "KB")
