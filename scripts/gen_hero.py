#!/usr/bin/env python3
"""Generate the hello.sh hero, adapted from the selected terminal mockup.

Run from any directory: python3 scripts/gen_hero.py
Only the role loop repeats; the command and prefix type once.
"""
from pathlib import Path
from svg import PALETTES, document, text

ASSETS = Path(__file__).resolve().parents[1] / "assets"
ROLES = ("a hardware engineer.", "an ML engineer.", "a research engineer.", "a software engineer.")
PREFIX = "Hi, I'm Jordan. I'm "
SIZE, PITCH = 28, 16.8
TYPE, HOLD, ERASE = 0.055, 2.4, 0.03
ROLE_START = 1.9 + len(PREFIX) * TYPE
CYCLE = sum(len(role) * (TYPE + ERASE) + HOLD for role in ROLES) + 0.3


def animation(attribute, steps, duration, begin=0, repeat=False):
    times = ";".join(f"{t / duration:.7f}" for t, _ in steps)
    values = ";".join(f"{v:.3f}" for _, v in steps)
    end = 'repeatCount="indefinite"' if repeat else 'fill="freeze"'
    return (f'<animate attributeName="{attribute}" calcMode="discrete" '
            f'values="{values}" keyTimes="{times}" dur="{duration:.5f}s" begin="{begin:.5f}s" {end}/>')


def typed(value, ident, x, y, start, color, duration=None, repeat=False, erase=False):
    end = start + len(value) * TYPE
    duration = duration or end
    steps = [(0, 0)]
    steps.extend((start + n * TYPE, n * PITCH) for n in range(1, len(value) + 1))
    if erase:
        steps.extend((end + HOLD + n * ERASE, (len(value) - n) * PITCH)
                     for n in range(1, len(value) + 1))
    if steps[-1][0] < duration:
        steps.append((duration, steps[-1][1]))
    begin = ROLE_START if repeat else 0
    clip = (f'<clipPath id="{ident}"><rect x="{x}" y="{y - SIZE}" width="0" height="42">'
            + animation("width", steps, duration, begin, repeat) + '</rect></clipPath>')
    return clip + text(value, x, y, color, SIZE, extra=f'clip-path="url(#{ident})"'), steps


def hero(theme):
    p = PALETTES[theme]
    parts = [f'<rect x="0.5" y="0.5" width="1199" height="299" rx="10" fill="{p["bg"]}" stroke="{p["line"]}"/>',
             '<style>.motion{display:none}@media(prefers-reduced-motion:no-preference){.motion{display:inline}.still{display:none}}</style>']

    def prompt(y):
        x, result = 48, []
        for value, color in (("jordan@waterloo", "green"), (":", "fg"), ("~", "blue"), ("$ ", "fg")):
            result.append(text(value, x, y, p[color], SIZE))
            x += len(value) * PITCH
        return "".join(result), x

    first, command_x = prompt(96)
    third, idle_x = prompt(220)
    parts.extend([first, '<g class="still">', text("./hello.sh", command_x, 96, p["fg"], SIZE),
                  text(PREFIX + ROLES[0], 48, 158, p["fg"], SIZE), third, '</g>', '<g class="motion">'])
    parts.append(typed("./hello.sh", "command", command_x, 96, 0.6, p["fg"])[0])
    parts.append(typed(PREFIX, "prefix", 48, 158, 1.9, p["fg"])[0])
    role_x = 48 + len(PREFIX) * PITCH
    cursor_steps = [(0, role_x + 2)]
    offset = 0
    for i, role in enumerate(ROLES):
        block, steps = typed(role, f"role-{i}", role_x, 158, offset, p["fg"], CYCLE, True, True)
        parts.append(block)
        finish = offset + len(role) * (TYPE + ERASE) + HOLD
        cursor_steps.extend((t, role_x + w + 2) for t, w in steps if offset < t <= finish)
        offset = finish
    cursor_steps.append((CYCLE, role_x + 2))
    parts.append(f'<g opacity="0"><set attributeName="opacity" to="1" begin="{ROLE_START}s"/>'
                 f'<rect x="{role_x + 2}" y="132" width="2.2" height="32" fill="{p["fg"]}">'
                 + animation("x", cursor_steps, CYCLE, ROLE_START, True)
                 + '<animate attributeName="opacity" values="1;0" calcMode="discrete" dur="1s" repeatCount="indefinite"/></rect></g>')
    parts.append(f'<g opacity="0"><set attributeName="opacity" to="1" begin="1.9s"/>{third}'
                 f'<rect x="{idle_x + 2}" y="194" width="15.4" height="32" fill="{p["fg"]}">'
                 '<animate attributeName="opacity" values="0.8;0" calcMode="discrete" dur="1.1s" repeatCount="indefinite"/></rect></g></g>')
    parts.append(text("jordanleis.com", 1152, 276, p["muted"], 14, extra='text-anchor="end"'))
    return document(1200, 300, "Hi, I'm Jordan.",
                    "Jordan at Waterloo. Hardware engineer, ML engineer, research engineer, software engineer. jordanleis.com", parts)


def main():
    ASSETS.mkdir(exist_ok=True)
    for theme in PALETTES:
        (ASSETS / f"hello-{theme}.svg").write_text(hero(theme), encoding="utf-8")


if __name__ == "__main__":
    main()
