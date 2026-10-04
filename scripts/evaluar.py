"""Mide a Kev contra las respuestas correctas de data/mensajes.jsonl.

    uv run python scripts/evaluar.py --lang en
"""
import argparse, asyncio, json, statistics, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from soporte.kev import Kev

def load():
    return [json.loads(l) for l in (Path(__file__).resolve().parent.parent / "data" / "mensajes.jsonl").read_text().splitlines() if l.strip()]

async def main(lang):
    kev = Kev(lang=lang); msgs = load()
    await kev.classify("x", "warm up")
    rows = []
    for m in msgs:
        v = await kev.classify(m["de"], m["texto"]); rows.append((m, v))
    n = len(rows)
    acc = lambda f: sum(f(m, v) for m, v in rows) / n
    best = lambda key: max(((t / 100, acc(lambda m, v: (getattr(v, key) >= t / 100) == m[key])) for t in range(5, 96, 5)), key=lambda x: x[1])
    print(f"[{lang}] área {acc(lambda m, v: v.area == m['area']):.0%} | urgencia exacta {acc(lambda m, v: v.urgencia == m['urgencia']):.0%} "
          f"(±1 {acc(lambda m, v: abs(v.urgencia - m['urgencia']) <= 1):.0%}) | enojado@0.5 {acc(lambda m, v: (v.enojado >= .5) == m['enojado']):.0%} (mejor umbral {best('enojado')[0]:.2f} → {best('enojado')[1]:.0%}) "
          f"| escalar@0.5 {acc(lambda m, v: (v.escalar >= .5) == m['escalar']):.0%} (mejor umbral {best('escalar')[0]:.2f} → {best('escalar')[1]:.0%}) | {statistics.median(v.ms for _, v in rows):.0f} ms")
    # dudas: si la probabilidad del área más alta es baja, ¿se equivoca más?
    for t in (0.4, 0.5, 0.6):
        keep = [(m, v) for m, v in rows if max(v.area_probs.values()) >= t]
        if keep:
            print(f"   área con confianza ≥{t:.1f}: decide {len(keep)/n:.0%} de los mensajes y acierta {sum(v.area == m['area'] for m, v in keep)/len(keep):.0%}")
    errs = [(m["id"], m["area"], v.area, round(max(v.area_probs.values()), 2)) for m, v in rows if v.area != m["area"]]
    print("   errores de área (id, correcta, kev, conf):", errs)
    return rows

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--lang", default="en"); a = ap.parse_args()
    asyncio.run(main(a.lang))
