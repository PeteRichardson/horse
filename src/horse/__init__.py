#!/usr/bin/env python3
"""
Generate a loopable galloping-horse animation as a 72x16-per-frame sprite sheet.

The horse is drawn from a small skeletal model (barrel, neck, head, tail, four
two-bone legs solved with IK) sampled at N evenly spaced points around one
stride.  Because the model is periodic in t, frame N wraps seamlessly to frame 0.

Rendering is layered: each part is drawn as a supersampled coverage mask, box
downsampled to 72x16, then composited with its own colour source.  That keeps
antialiasing clean while letting the barrel take a vertical gradient and the
off-side limbs take a cooler, darker tint.
"""

import argparse
import math

from PIL import Image, ImageChops, ImageDraw

W, H = 72, 16
SS = 8  # supersample factor
GROUND = 15.4  # y of the hoof contact line

# ---------------------------------------------------------------- skeleton ---
# All coordinates are in final display pixels, y down, horse facing LEFT.
SHOULDER = (32.0, 6.4)  # top of foreleg (inside the barrel)
HIP = (43.0, 5.8)  # top of hind leg
CHEST = (32.0, 5.6)  # neck root
CROUP = (44.8, 4.9)  # tail root

FORE_UP, FORE_LO = 4.6, 4.5  # foreleg segment lengths
HIND_UP, HIND_LO = 4.9, 4.8  # hind leg segment lengths
FORE_REACH, HIND_REACH = 5.0, 5.6

STANCE = 0.30  # fraction of the stride each hoof is planted
# (kind, near/far, touchdown phase)  -> transverse gallop, 4 beats + suspension
LEGS = [
    ("hind", False, 0.00),
    ("hind", True, 0.12),
    ("fore", False, 0.30),
    ("fore", True, 0.42),
]

# body travel per stride, implied by the stance sweep; used for ground scroll
TRAVEL = (FORE_REACH + HIND_REACH) / STANCE

# ----------------------------------------------------------------- palette ---
# Palomino: gold body, flaxen mane and tail.  The light mane/tail against a
# darker body is a marking almost unique to horses, and it survives 16px.
BODY_STOPS = [
    (2.0, (238, 190, 112)),
    (5.0, (214, 152, 74)),
    (9.0, (168, 104, 40)),
    (12.5, (112, 60, 20)),
    (15.5, (80, 42, 14)),
]
FLAXEN = (255, 244, 214)
TAIL = (203, 172, 130)  # darker than the mane: the tail must not be the brightest thing
HOOF = (238, 216, 184)
FAR_MUL = (0.47, 0.48, 0.58)  # cooler + darker: atmospheric perspective
EYE = (92, 46, 16)
CREASE = (0.70, 0.67, 0.72)  # shoulder / flank grooves, as a gradient multiplier
GROUND_C = (24, 24, 46)
CONTACT = (112, 106, 168)


def rot(p, c, a):
    s, k = math.sin(a), math.cos(a)
    dx, dy = p[0] - c[0], p[1] - c[1]
    return (c[0] + dx * k - dy * s, c[1] + dx * s + dy * k)


def ik(root, target, a, b, bend):
    """Two-bone IK. Returns the joint position. `bend` is +1/-1."""
    dx, dy = target[0] - root[0], target[1] - root[1]
    d = math.hypot(dx, dy)
    d = max(abs(a - b) + 1e-3, min(a + b - 1e-3, d))
    base = math.atan2(dy, dx)
    ca = max(-1.0, min(1.0, (a * a + d * d - b * b) / (2 * a * d)))
    ang = base + bend * math.acos(ca)
    return (root[0] + a * math.cos(ang), root[1] + a * math.sin(ang))


def hoof(t, phase, reach, lift):
    """Hoof position relative to the leg root's x. Returns (x, y, planted)."""
    u = (t - phase) % 1.0
    # The horse faces -x, so a planted hoof travels +x relative to the body.
    if u < STANCE:  # planted: slides backward under body
        s = u / STANCE
        return (reach * (2 * s - 1), GROUND, True)
    v = (u - STANCE) / (1 - STANCE)  # swing: folds up and reaches forward
    x = reach * math.cos(math.pi * v)
    y = GROUND - lift * math.sin(math.pi * v) ** 0.85
    return (x, y, False)


# ---------------------------------------------------------------- drawing ---
def taper(d, p, q, w1, w2, v=255):
    """A thick line from p to q with width w1 -> w2, round-capped."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    L = math.hypot(dx, dy) or 1e-6
    nx, ny = -dy / L * 0.5, dx / L * 0.5
    d.polygon(
        [
            (p[0] + nx * w1, p[1] + ny * w1),
            (q[0] + nx * w2, q[1] + ny * w2),
            (q[0] - nx * w2, q[1] - ny * w2),
            (p[0] - nx * w1, p[1] - ny * w1),
        ],
        fill=v,
    )
    for c, w in ((p, w1), (q, w2)):
        r = w * 0.5
        d.ellipse([c[0] - r, c[1] - r, c[0] + r, c[1] + r], fill=v)


def disc(d, c, r):
    d.ellipse([c[0] - r, c[1] - r, c[0] + r, c[1] + r], fill=255)


class Layers:
    """A named set of supersampled coverage masks."""

    def __init__(self, names):
        self.m = {n: Image.new("L", (W * SS, H * SS), 0) for n in names}
        self.d = {n: ImageDraw.Draw(self.m[n]) for n in names}

    def draw(self, name):
        return self.d[name]

    def resolved(self):
        return {n: im.resize((W, H), Image.BOX) for n, im in self.m.items()}


def draw_leg(dr, root, kind, phase, t, hoof_dr):
    if kind == "fore":
        a, b, reach, lift, bend = FORE_UP, FORE_LO, FORE_REACH, 5.0, 1
    else:
        a, b, reach, lift, bend = HIND_UP, HIND_LO, HIND_REACH, 4.6, -1
    hx, hy, planted = hoof(t, phase, reach, lift)
    target = (root[0] + hx, hy)
    knee = ik(root, target, a, b, bend)
    s = lambda p: (p[0] * SS, p[1] * SS)
    taper(dr, s(root), s(knee), 2.4 * SS, 1.6 * SS)
    taper(dr, s(knee), s(target), 1.6 * SS, 1.0 * SS)
    disc(hoof_dr, s(target), 0.62 * SS)
    return target, planted


def build(t, flip):
    """Draw one frame's layers. Returns (masks, contact points)."""
    L = Layers(
        ["far", "farhoof", "body", "shade", "tail", "mane", "near", "nearhoof", "eye"]
    )
    s = lambda p: (p[0] * SS, p[1] * SS)

    bob = -0.85 * math.cos(2 * math.pi * (t - 0.86))
    pitch = math.radians(5.0) * math.sin(2 * math.pi * (t - 0.10))
    pivot = (37.0, 5.3)

    def B(p):
        return rot((p[0], p[1] + bob), pivot, pitch)

    sh, hp, ch, cr = B(SHOULDER), B(HIP), B(CHEST), B(CROUP)
    contacts = []

    for kind, is_near, ph in LEGS:
        root = sh if kind == "fore" else hp
        tgt = "near" if is_near else "far"
        pos, planted = draw_leg(L.draw(tgt), root, kind, ph, t, L.draw(tgt + "hoof"))
        if planted:
            contacts.append(pos[0])

    # barrel: thin through the middle, mass at shoulder and haunch
    db = L.draw("body")
    taper(db, s(B((32.5, 5.6))), s(B((42.5, 5.3))), 3.8 * SS, 3.8 * SS)
    taper(db, s(B((42.5, 5.3))), s(B((45.0, 5.4))), 3.8 * SS, 2.6 * SS)
    disc(db, s(B((33.0, 6.0))), 2.7 * SS)
    disc(db, s(B((42.2, 5.6))), 3.0 * SS)

    # neck + head
    ext = 0.6 * math.sin(2 * math.pi * (t - 0.30))
    poll = B((24.2 - ext, 3.2 + 0.30 * ext))
    muzzle = B((20.8 - ext * 1.25, 5.9 + 0.45 * ext))
    taper(db, s(ch), s(poll), 4.4 * SS, 3.0 * SS)
    taper(db, s(poll), s(muzzle), 3.4 * SS, 1.5 * SS)
    disc(db, s(B((23.3 - ext * 1.1, 4.0 + 0.35 * ext))), 1.85 * SS)
    taper(db, s(B((25.1 - ext, 2.8))), s(B((25.7 - ext, 1.7))), 1.0 * SS, 0.4 * SS)

    # Shoulder and flank grooves. Clipped to the body mask in compose(), so they
    # carve the barrel rather than outlining it: the scapula's rear edge, and the
    # flank crease running from the point of hip down toward the stifle.
    ds = L.draw("shade")
    taper(ds, s(B((34.9, 4.2))), s(B((34.2, 6.3))), 0.80 * SS, 0.90 * SS, 200)
    taper(ds, s(B((34.2, 6.3))), s(B((33.3, 7.9))), 0.90 * SS, 0.55 * SS, 140)
    taper(ds, s(B((41.2, 3.7))), s(B((40.1, 6.1))), 0.80 * SS, 0.90 * SS, 200)
    taper(ds, s(B((40.1, 6.1))), s(B((40.8, 7.9))), 0.90 * SS, 0.55 * SS, 140)

    # eye: one dark pixel on a bright cheek is enough to orient the head
    disc(L.draw("eye"), s(B((23.9 - ext * 1.05, 3.6 + 0.32 * ext))), 0.55 * SS)

    # mane along the neck crest, and a forelock at the poll
    dm = L.draw("mane")
    taper(dm, s(B((30.9, 4.4))), s(B((25.6, 2.3 + 0.25 * ext))), 1.6 * SS, 1.2 * SS)
    taper(dm, s(B((25.6 - ext, 2.4))), s(B((23.9 - ext, 3.0))), 1.2 * SS, 0.7 * SS)

    # Tail: a plume, not a cord. The hair flares *wider* than the dock before it
    # softens at the tip - taper it down uniformly and you get a rat's tail.
    # Motion is a wave travelling down the length rather than a rigid swing, so
    # the tail S-bends and whips instead of hinging at the dock.
    dt = L.draw("tail")
    spine = [
        ((44.8, 4.8), 0.00, 2.0),
        ((47.7, 5.3), 0.28, 2.3),
        ((50.2, 6.4), 0.55, 2.4),
        ((52.0, 7.7), 0.80, 2.0),
        ((53.2, 8.9), 1.00, 1.4),
    ]
    pts = []
    for (px, py), u, wd in spine:
        wave = math.sin(2 * math.pi * (t - 0.55) - 2.2 * u)
        amp = 1.35 * u**1.25
        pts.append(
            (
                B(
                    (
                        px + 0.35 * u * math.cos(2 * math.pi * (t - 0.55)),
                        py + amp * wave,
                    )
                ),
                wd,
            )
        )
    for (p0, w0), (p1, w1) in zip(pts, pts[1:]):
        taper(dt, s(p0), s(p1), w0 * SS, w1 * SS)
    disc(dt, s(pts[-1][0]), 0.70 * SS)  # soft rounded tip

    masks = L.resolved()
    if flip:
        masks = {n: m.transpose(Image.FLIP_LEFT_RIGHT) for n, m in masks.items()}
        contacts = [W - 1 - c for c in contacts]
    return masks, contacts


# ------------------------------------------------------------ compositing ---
def gradient():
    """Vertical body gradient: lit topline, shadowed belly."""
    g = Image.new("RGB", (W, H))
    px = g.load()
    for y in range(H):
        yy = y + 0.5
        for i in range(len(BODY_STOPS) - 1):
            y0, c0 = BODY_STOPS[i]
            y1, c1 = BODY_STOPS[i + 1]
            if yy <= y1 or i == len(BODY_STOPS) - 2:
                f = max(0.0, min(1.0, (yy - y0) / (y1 - y0)))
                col = tuple(int(c0[k] + (c1[k] - c0[k]) * f) for k in range(3))
                break
        for x in range(W):
            px[x, y] = col
    return g


def scale_img(img, mul):
    px = img.load()
    out = Image.new("RGB", img.size)
    op = out.load()
    for y in range(img.size[1]):
        for x in range(img.size[0]):
            r, g, b = px[x, y]
            op[x, y] = (int(r * mul[0]), int(g * mul[1]), int(b * mul[2]))
    return out


def flat(c):
    return Image.new("RGB", (W, H), c)


def over(base, color_src, mask):
    return (
        Image.composite(color_src, base, mask)
        if mask.mode == "1"
        else Image.blend(base, color_src, 0).__class__ and _alpha(base, color_src, mask)
    )


def _alpha(base, src, mask):
    out = base.copy()
    bp, sp, mp, op = base.load(), src.load(), mask.load(), out.load()
    for y in range(H):
        for x in range(W):
            a = mp[x, y] / 255.0
            if a <= 0:
                continue
            b, s_ = bp[x, y], sp[x, y]
            op[x, y] = tuple(int(b[k] + (s_[k] - b[k]) * a) for k in range(3))
    return out


def compose(masks, contacts, ground, t, mono):
    img = Image.new("RGB", (W, H), (0, 0, 0))

    if mono:
        white, dim = flat((255, 255, 255)), flat((130, 130, 130))
        for n in ("far", "farhoof"):
            img = _alpha(img, dim, masks[n])
        for n in ("body", "tail", "mane", "near", "nearhoof"):
            img = _alpha(img, white, masks[n])
        return img

    grad = gradient()
    far = scale_img(grad, FAR_MUL)

    if ground:
        img = draw_ground(img, contacts, t)

    img = _alpha(img, far, masks["far"])
    img = _alpha(img, scale_img(flat(HOOF), FAR_MUL), masks["farhoof"])
    img = _alpha(img, grad, masks["body"])
    crease = ImageChops.multiply(masks["shade"], masks["body"])
    img = _alpha(img, scale_img(grad, CREASE), crease)
    img = _alpha(img, flat(EYE), masks["eye"])
    img = _alpha(img, flat(TAIL), masks["tail"])
    img = _alpha(img, flat(FLAXEN), masks["mane"])
    img = _alpha(img, grad, masks["near"])
    img = _alpha(img, flat(HOOF), masks["nearhoof"])
    return img


def draw_ground(img, contacts, t):
    """Dim scrolling ground with a brighter patch under each planted hoof."""
    big = Image.new("L", (W * SS, H * SS), 0)
    d = ImageDraw.Draw(big)
    period = TRAVEL / 5.0
    off = (t * TRAVEL) % period
    x = -period + off
    while x < W + period:
        d.rectangle([x * SS, 15.0 * SS, (x + period * 0.55) * SS, 15.9 * SS], fill=255)
        x += period
    base = _alpha(img, flat(GROUND_C), big.resize((W, H), Image.BOX))

    glow = Image.new("L", (W * SS, H * SS), 0)
    dg = ImageDraw.Draw(glow)
    for cx in contacts:
        dg.rectangle([(cx - 2.6) * SS, 14.9 * SS, (cx + 2.6) * SS, 15.9 * SS], fill=255)
    return _alpha(base, flat(CONTACT), glow.resize((W, H), Image.BOX))


def render(t, flip, ground, mono):
    masks, contacts = build(t, flip)
    return compose(masks, contacts, ground, t, mono)


# ------------------------------------------------------------------- main ---
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--frames", type=int, default=12)
    ap.add_argument("-o", "--out", default="horse_sheet.png")
    ap.add_argument("--layout", choices=["row", "column", "grid"], default="row")
    ap.add_argument("--cols", type=int, default=4, help="columns when --layout grid")
    ap.add_argument("--ground", action="store_true", help="scrolling ground + contact")
    ap.add_argument("--mono", action="store_true", help="original white silhouette")
    ap.add_argument("--flip", action="store_true", help="face right instead of left")
    ap.add_argument("--gif", default=None, help="also write a preview GIF")
    ap.add_argument("--fps", type=float, default=15.0)
    args = ap.parse_args()

    n = args.frames
    frames = [render(i / n, args.flip, args.ground, args.mono) for i in range(n)]

    if args.layout == "row":
        cols, rows = n, 1
    elif args.layout == "column":
        cols, rows = 1, n
    else:
        cols = args.cols
        rows = (n + cols - 1) // cols

    sheet = Image.new("RGB", (W * cols, H * rows), (0, 0, 0))
    for i, f in enumerate(frames):
        sheet.paste(f, ((i % cols) * W, (i // cols) * H))
    sheet.save(args.out)
    print(
        f"{args.out}: {sheet.size[0]}x{sheet.size[1]}, {n} frames, "
        f"{cols}x{rows} cells of {W}x{H}"
    )

    if args.gif:
        fr = [f.resize((W * 6, H * 6), Image.NEAREST) for f in frames]
        fr[0].save(
            args.gif,
            save_all=True,
            append_images=fr[1:],
            duration=int(1000 / args.fps),
            loop=0,
        )
        print(f"{args.gif}: preview at {args.fps} fps")


if __name__ == "__main__":
    main()
